/*
 * alloc.c — YIAN 堆分配器: 尺寸类 arena.
 *
 * 块布局 (P1 沿用 32 B 块头, 与 lockmech.py::BlockHeader 一致):
 *   [lock(8) | capacity(8) | next(8) | active_size(8) | payload...]
 * 编译器负责写 lock 与 active_size; 分配器只用 capacity 与 next:
 *   - capacity 记录块所属尺寸类的负载容量; 最高位为 1 表示"大对象 chunk";
 *   - next 在空闲块里串自由链 (P2 起改写到空闲负载区).
 *
 * 尺寸类: 16 B 起、每倍频 3 档、比例约 1.25, 共 36 档至 49152 B; 更大的请求走
 * 大对象 chunk (mmap, 按容量缓存). 每个 slab 是 64 KiB、64 KiB 对齐的独立映射:
 *   [Slab 描述符(64 B) | 块...]
 * 类内维护"有空闲块的 slab"链, 分配与释放都是 O(1).
 *
 * 归还: 只用 madvise(MADV_DONTNEED) 归还物理页, 从不 munmap——地址空间始终保留,
 * 因此悬垂指针读已释放块的锁槽仍然命中映射 (读回 0/SENTINEL), 检查按 S003/S006
 * 报错而不是段错误. 空 slab 进"已归还"链等待复用, 水位以下的空 slab 留在 partial 链.
 *
 * 单线程: 不加锁 (见 docs/security.md §10).
 */

#include "yian_rt.h"

#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* ── 参数 ── */

#define YIAN_SLAB_BYTES ((uint64_t)64 * 1024)
#define YIAN_SLAB_HEADER ((uint64_t)64)
/* slab 页的常驻额度: 映射量低于它时, 空 slab 保持页常驻 (分配-释放循环不重触页);
 * 超过额度才开始归还物理页, 因此长跑 RSS 有上界而短中期 churn 不退化为缺页. */
#define YIAN_SLAB_WATERMARK ((uint64_t)32 << 20)
/* 超过额度后每类仍保留的"空但页常驻"slab 数. */
#define YIAN_SLAB_KEEP_EMPTY 4u
#define YIAN_LARGE_CACHE ((uint64_t)8 << 20)
#define YIAN_LARGE_FLAG ((uint64_t)1 << 63)

#define YIAN_CAPACITY_OFFSET 8u
#define YIAN_NEXT_OFFSET 16u

static const uint32_t yian_class_bytes[] = {
    16,     20,     25,     32,     40,     50,     64,     80,     100,
    128,    160,    200,    256,    320,    400,    512,    640,    800,
    1024,   1280,   1600,   2048,   2560,   3200,   4096,   5120,   6400,
    8192,   10240,  12800,  16384,  20480,  25600,  32768,  40960,  49152,
};

#define YIAN_CLASS_COUNT ((uint32_t)(sizeof(yian_class_bytes) / sizeof(yian_class_bytes[0])))

typedef struct Slab {
    struct Slab *next;   /* 同类链表 */
    struct Slab *prev;
    void *free_head;     /* 空闲块链 (链在块的 next 字段) */
    void *bump;          /* 尚未借出过的块游标 */
    uint32_t class_index;
    uint32_t capacity;   /* 本 slab 的块数 */
    uint32_t live_count; /* 已借出的块数 */
    uint32_t madvised;   /* 是否已归还物理页 */
    uint32_t resident_empty; /* 空 slab 且页常驻 (计入 class_resident_empty) */
} Slab;

static uint32_t class_stride[YIAN_CLASS_COUNT];
static uint32_t class_bucket_start[64];
static Slab *class_partial[YIAN_CLASS_COUNT]; /* 有空闲块的 slab */
static Slab *class_returned[YIAN_CLASS_COUNT]; /* 已归还物理页的空 slab */
static uint32_t class_resident_empty[YIAN_CLASS_COUNT]; /* 各类型保留的常驻空 slab 数 */
static uint64_t slab_mapped_bytes = 0;
static int alloc_inited = 0;

/* 大对象缓存: 块头 next/capacity 复用为链与容量; 超水位后仅移出缓存、不归还地址空间 */
static void *large_head = 0;
static void *large_tail = 0;
static uint64_t large_cached_bytes = 0;

/* ── 块头访问 ── */

static inline void *block_next(const void *block) {
    void *value;
    memcpy(&value, (const char *)block + YIAN_NEXT_OFFSET, sizeof(value));
    return value;
}

static inline void block_set_next(void *block, void *next) {
    memcpy((char *)block + YIAN_NEXT_OFFSET, &next, sizeof(next));
}

static inline uint64_t block_capacity(const void *block) {
    uint64_t value;
    memcpy(&value, (const char *)block + YIAN_CAPACITY_OFFSET, sizeof(value));
    return value;
}

static inline void block_set_capacity(void *block, uint64_t capacity) {
    memcpy((char *)block + YIAN_CAPACITY_OFFSET, &capacity, sizeof(capacity));
}

static inline uint64_t align_up(uint64_t value, uint64_t align) {
    return (value + align - 1) & ~(align - 1);
}

/* madvise 要求页对齐: 只归还完全落在 [start, start+length) 内的整页. */
static void release_pages(void *start, uint64_t length) {
    long page_value = sysconf(_SC_PAGESIZE);
    uint64_t page = page_value > 0 ? (uint64_t)page_value : 4096;
    uintptr_t from = (uintptr_t)align_up((uintptr_t)start, page);
    uintptr_t to = ((uintptr_t)start + length) & ~(uintptr_t)(page - 1);
    if (to > from) {
        madvise((void *)from, (size_t)(to - from), MADV_DONTNEED);
    }
}

/* ── 初始化与类查找 ── */

static void alloc_init(void) {
    for (uint32_t i = 0; i < YIAN_CLASS_COUNT; i++) {
        class_stride[i] = (uint32_t)align_up(YIAN_HDR_BYTES + yian_class_bytes[i], 16);
    }
    for (uint32_t bucket = 0; bucket < 64; bucket++) {
        /* 该桶里第一个"容量 ≥ 2^bucket"的尺寸类; 桶内再线性微调. */
        uint64_t threshold = (uint64_t)1 << bucket;
        class_bucket_start[bucket] = YIAN_CLASS_COUNT - 1;
        for (uint32_t i = 0; i < YIAN_CLASS_COUNT; i++) {
            if ((uint64_t)yian_class_bytes[i] >= threshold) {
                class_bucket_start[bucket] = i;
                break;
            }
        }
    }
    alloc_inited = 1;
}

static uint32_t class_index(uint64_t bytes) {
    uint32_t bucket = (uint32_t)(63 - __builtin_clzll(bytes));
    uint32_t index = class_bucket_start[bucket];
    while (index + 1 < YIAN_CLASS_COUNT && yian_class_bytes[index] < bytes) {
        index++;
    }
    return index;
}

static void alloc_fail(void) {
    static const uint8_t message[] = YIAN_OOM_MESSAGE;
    __yian_runtime_fail(message, sizeof(message) - 1);
}

/* ── slab ── */

static void list_push(Slab **head, Slab *slab) {
    slab->prev = 0;
    slab->next = *head;
    if (*head != 0) {
        (*head)->prev = slab;
    }
    *head = slab;
}

static void list_unlink(Slab **head, Slab *slab) {
    if (slab->prev != 0) {
        slab->prev->next = slab->next;
    } else if (*head == slab) {
        *head = slab->next;
    }
    if (slab->next != 0) {
        slab->next->prev = slab->prev;
    }
    slab->next = 0;
    slab->prev = 0;
}

/* 映射一块 64 KiB、64 KiB 对齐的 slab 区域, 描述符放在区域首部. */
static Slab *slab_map(void) {
    uint64_t total = YIAN_SLAB_BYTES * 2;
    void *raw = mmap(0, (size_t)total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (raw == MAP_FAILED) {
        return 0;
    }
    uintptr_t base = (uintptr_t)align_up((uintptr_t)raw, YIAN_SLAB_BYTES);
    uintptr_t end = base + YIAN_SLAB_BYTES;
    if (base > (uintptr_t)raw) {
        munmap(raw, (size_t)(base - (uintptr_t)raw));
    }
    if (end < (uintptr_t)raw + total) {
        munmap((void *)end, (size_t)((uintptr_t)raw + total - end));
    }
    return (Slab *)base;
}

static Slab *slab_new(uint32_t index) {
    Slab *slab = slab_map();
    if (slab == 0) {
        alloc_fail();
    }
    slab->class_index = index;
    slab->capacity = (uint32_t)((YIAN_SLAB_BYTES - YIAN_SLAB_HEADER) / class_stride[index]);
    slab->live_count = 0;
    slab->madvised = 0;
    slab->resident_empty = 0;
    slab->free_head = 0;
    slab->bump = (char *)slab + YIAN_SLAB_HEADER;
    slab->next = 0;
    slab->prev = 0;
    slab_mapped_bytes += YIAN_SLAB_BYTES;
    return slab;
}

/* 取出一个已归还物理页的空 slab 复用; 顺序块游标从头开始. */
static Slab *slab_reuse_returned(uint32_t index) {
    Slab *slab = class_returned[index];
    if (slab == 0) {
        return 0;
    }
    list_unlink(&class_returned[index], slab);
    slab->free_head = 0;
    slab->bump = (char *)slab + YIAN_SLAB_HEADER;
    slab->madvised = 0;
    slab->resident_empty = 0;
    return slab;
}

static void *slab_take_block(Slab *slab) {
    if (slab->free_head != 0) {
        void *block = slab->free_head;
        slab->free_head = block_next(block);
        return block;
    }
    char *end = (char *)slab + YIAN_SLAB_BYTES;
    char *cursor = slab->bump;
    if (cursor + class_stride[slab->class_index] > end) {
        return 0;
    }
    slab->bump = cursor + class_stride[slab->class_index];
    return cursor;
}

static void *class_alloc(uint64_t bytes) {
    uint32_t index = class_index(bytes);
    Slab *slab = class_partial[index];
    if (slab == 0) {
        slab = slab_reuse_returned(index);
        if (slab == 0) {
            slab = slab_new(index);
        }
        list_push(&class_partial[index], slab);
    }
    if (slab->resident_empty != 0) {
        slab->resident_empty = 0;
        class_resident_empty[index]--;
    }
    void *block = slab_take_block(slab);
    if (block == 0) { /* 记账异常: 该 slab 实际已满, 摘掉后重试 */
        list_unlink(&class_partial[index], slab);
        return class_alloc(bytes);
    }
    slab->live_count++;
    if (slab->live_count == slab->capacity) {
        list_unlink(&class_partial[index], slab);
    }
    block_set_capacity(block, yian_class_bytes[index]);
    block_set_next(block, 0);
    return block;
}

static void slab_release_block(void *block) {
    Slab *slab = (Slab *)((uintptr_t)block & ~(uintptr_t)(YIAN_SLAB_BYTES - 1));
    block_set_next(block, slab->free_head);
    slab->free_head = block;
    slab->live_count--;
    uint32_t class = slab->class_index;
    if (slab->live_count == 0) {
        if (class_resident_empty[class] < YIAN_SLAB_KEEP_EMPTY
            || slab_mapped_bytes <= YIAN_SLAB_WATERMARK) {
            /* 保留页常驻: slab 仍在 partial 链上, 直接复用 (避免归还后立刻重触页). */
            if (slab->resident_empty == 0) {
                slab->resident_empty = 1;
                class_resident_empty[class]++;
            }
            return;
        }
        /* 保留额度用尽且超过水位: 归还物理页并移入"已归还"链; 地址空间保留, 悬垂读仍命中映射. */
        list_unlink(&class_partial[class], slab);
        release_pages((char *)slab + YIAN_SLAB_HEADER, YIAN_SLAB_BYTES - YIAN_SLAB_HEADER);
        slab->madvised = 1;
        slab->resident_empty = 0;
        slab->free_head = 0;
        slab->bump = (char *)slab + YIAN_SLAB_HEADER;
        list_push(&class_returned[class], slab);
        return;
    }
    if (slab->live_count + 1 == slab->capacity) {
        list_push(&class_partial[class], slab); /* 由满转空 */
    }
}

/* ── 大对象 ── */

static void *large_alloc(uint64_t bytes) {
    void *previous = 0;
    for (void *chunk = large_head; chunk != 0; chunk = block_next(chunk)) {
        if ((block_capacity(chunk) & ~YIAN_LARGE_FLAG) >= bytes) {
            void *next = block_next(chunk);
            if (previous == 0) {
                large_head = next;
            } else {
                block_set_next(previous, next);
            }
            if (large_tail == chunk) {
                large_tail = previous;
            }
            large_cached_bytes -= block_capacity(chunk) & ~YIAN_LARGE_FLAG;
            block_set_next(chunk, 0);
            return chunk;
        }
        previous = chunk;
    }

    uint64_t payload = align_up(bytes, 16);
    uint64_t mapped = align_up(YIAN_HDR_BYTES + payload, 4096);
    void *chunk = mmap(0, (size_t)mapped, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (chunk == MAP_FAILED) {
        alloc_fail();
    }
    block_set_capacity(chunk, payload | YIAN_LARGE_FLAG);
    block_set_next(chunk, 0);
    return chunk;
}

static void large_release(void *chunk) {
    uint64_t payload = block_capacity(chunk) & ~YIAN_LARGE_FLAG;
    block_set_next(chunk, 0);
    if (large_tail == 0) {
        large_head = chunk;
    } else {
        block_set_next(large_tail, chunk);
    }
    large_tail = chunk;
    large_cached_bytes += payload;
    while (large_cached_bytes > YIAN_LARGE_CACHE && large_head != large_tail) {
        /* 淘汰出缓存: 归还它的物理页, 地址空间仍保留给悬垂读. */
        void *victim = large_head;
        uint64_t victim_payload = block_capacity(victim) & ~YIAN_LARGE_FLAG;
        large_head = block_next(victim);
        release_pages((char *)victim + YIAN_HDR_BYTES, victim_payload);
        block_set_next(victim, 0);
        large_cached_bytes -= victim_payload;
    }
}

/* ── 对外接口 (编译器调用, 与旧池函数同名同签名) ── */

void *__secl_pool_alloc(uint64_t requested) {
    if (!alloc_inited) {
        alloc_init();
    }
    if (requested == 0) {
        requested = 1;
    }
    if (requested <= yian_class_bytes[YIAN_CLASS_COUNT - 1]) {
        return class_alloc(requested);
    }
    return large_alloc(requested);
}

void __secl_pool_release(void *block) {
    if (block == 0) {
        return;
    }
    if ((block_capacity(block) & YIAN_LARGE_FLAG) != 0) {
        large_release(block);
        return;
    }
    slab_release_block(block);
}
