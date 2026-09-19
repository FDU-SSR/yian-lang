/*
 * alloc.c — YIAN 堆分配器: 尺寸类 arena.
 *
 * 块布局 (与 lockmech.py::BlockHeader 一致): [lock(8) | active_size(8) | 负载...],
 * 负载从基址 + YIAN_HDR_BYTES 开始. 编译器写 lock 与 active_size; 分配器不占用块头,
 * 空闲块的自由链写在空闲负载的首字 (YIAN_NEXT_OFFSET = YIAN_HDR_BYTES).
 *
 * 尺寸类: 16 B 起、每倍频 3 档、比例约 1.25, 共 36 档至 49152 B; 更大的请求走大对象
 * chunk. 每个 slab 是 64 KiB、64 KiB 对齐的独立映射: [Slab 描述符(64 B) | 块...];
 * 大对象 chunk 同样放在 64 KiB 对齐 region 的 +64 处, region 首部放 LargeHeader.
 * 因此 `block & ~(SLAB_BYTES-1)` 对两种块都指向 region 首部, 用其中的 magic 区分.
 *
 * 归还: 只用 madvise(MADV_DONTNEED) 归还物理页, 从不 munmap——地址空间始终保留,
 * 悬垂指针读已释放块的锁槽仍命中映射 (读回 0), 检查按 S003/S006 报错而非段错误.
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

#define YIAN_SLAB_MAGIC 0x5949414e534c4142ull  /* "YIANSLAB" */
#define YIAN_LARGE_MAGIC 0x5949414e4c415247ull /* "YIANLARG" */

/* 空闲链与缓存链写在空闲块负载的首字. */
#define YIAN_NEXT_OFFSET YIAN_HDR_BYTES

static const uint32_t yian_class_bytes[] = {
    16,     20,     25,     32,     40,     50,     64,     80,     100,
    128,    160,    200,    256,    320,    400,    512,    640,    800,
    1024,   1280,   1600,   2048,   2560,   3200,   4096,   5120,   6400,
    8192,   10240,  12800,  16384,  20480,  25600,  32768,  40960,  49152,
};

#define YIAN_CLASS_COUNT ((uint32_t)(sizeof(yian_class_bytes) / sizeof(yian_class_bytes[0])))

typedef struct Slab {
    uint64_t magic;
    struct Slab *next;   /* 同类"有空闲块"链 */
    struct Slab *prev;
    void *free_head;     /* 空闲块链 (链在空闲负载首字) */
    void *bump;          /* 尚未借出过的块游标 */
    uint32_t class_index;
    uint32_t capacity;   /* 本 slab 的块数 */
    uint32_t live_count; /* 已借出的块数 */
    uint32_t madvised;   /* 是否已归还物理页 */
    uint32_t resident_empty; /* 空 slab 且页常驻 (计入 class_resident_empty) */
    uint32_t pad;
} Slab;

_Static_assert(sizeof(Slab) <= YIAN_SLAB_HEADER, "Slab descriptor must fit the slab header");

/* 大对象 region 首部 (region 与 chunk 的偏移见 large_alloc). */
typedef struct LargeHeader {
    uint64_t magic;
    uint64_t payload;
} LargeHeader;

static uint32_t class_stride[YIAN_CLASS_COUNT];
static uint32_t class_bucket_start[64];
static Slab *class_partial[YIAN_CLASS_COUNT];  /* 有空闲块的 slab */
static Slab *class_returned[YIAN_CLASS_COUNT]; /* 已归还物理页的空 slab */
static uint32_t class_resident_empty[YIAN_CLASS_COUNT];
static uint64_t slab_mapped_bytes = 0;
static int alloc_inited = 0;

/* 大对象缓存: 块负载首字复用为链; 超额度者移出缓存并归还物理页 */
static void *large_head = 0;
static void *large_tail = 0;
static uint64_t large_cached_bytes = 0;

/* ── 块与 region ── */

static inline void *block_next(const void *block) {
    void *value;
    memcpy(&value, (const char *)block + YIAN_NEXT_OFFSET, sizeof(value));
    return value;
}

static inline void block_set_next(void *block, void *next) {
    memcpy((char *)block + YIAN_NEXT_OFFSET, &next, sizeof(next));
}

/* region 首部: slab 块与大小对象块都按 64 KiB 对齐, 因此掩码即可定位. */
static inline void *block_region(const void *block) {
    return (void *)((uintptr_t)block & ~(uintptr_t)(YIAN_SLAB_BYTES - 1));
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
    slab->magic = YIAN_SLAB_MAGIC;
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
    return block;
}

static void slab_release_block(void *block) {
    Slab *slab = (Slab *)block_region(block);
    block_set_next(block, slab->free_head);
    slab->free_head = block;
    slab->live_count--;
    uint32_t class = slab->class_index;
    if (slab->live_count == 0) {
        if (class_resident_empty[class] < YIAN_SLAB_KEEP_EMPTY
            || slab_mapped_bytes <= YIAN_SLAB_WATERMARK) {
            /* 保留页常驻: slab 仍在 partial 链上, 直接复用. */
            if (slab->resident_empty == 0) {
                slab->resident_empty = 1;
                class_resident_empty[class]++;
            }
            return;
        }
        /* 保留额度用尽且超过额度: 归还物理页并移入"已归还"链; 地址空间保留. */
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

static inline LargeHeader *large_header(void *chunk) {
    return (LargeHeader *)block_region(chunk);
}

static void *large_alloc(uint64_t bytes) {
    void *previous = 0;
    for (void *chunk = large_head; chunk != 0; chunk = block_next(chunk)) {
        if (large_header(chunk)->payload >= bytes) {
            void *next = block_next(chunk);
            if (previous == 0) {
                large_head = next;
            } else {
                block_set_next(previous, next);
            }
            if (large_tail == chunk) {
                large_tail = previous;
            }
            large_cached_bytes -= large_header(chunk)->payload;
            block_set_next(chunk, 0);
            return chunk;
        }
        previous = chunk;
    }

    uint64_t payload = align_up(bytes, 16);
    uint64_t total = align_up(YIAN_HDR_BYTES + payload + YIAN_SLAB_BYTES * 2, 4096);
    void *raw = mmap(0, (size_t)total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (raw == MAP_FAILED) {
        alloc_fail();
    }
    /* chunk 放在 64 KiB 对齐 region 的 +64 处, region 首部放 LargeHeader. */
    uintptr_t region = (uintptr_t)align_up((uintptr_t)raw + YIAN_SLAB_HEADER, YIAN_SLAB_BYTES);
    uintptr_t chunk = region + YIAN_SLAB_HEADER;
    if (region > (uintptr_t)raw) {
        munmap(raw, (size_t)(region - (uintptr_t)raw));
    }
    uintptr_t end = chunk + YIAN_HDR_BYTES + payload;
    if (end < (uintptr_t)raw + total) {
        munmap((void *)end, (size_t)((uintptr_t)raw + total - end));
    }
    LargeHeader *header = (LargeHeader *)region;
    header->magic = YIAN_LARGE_MAGIC;
    header->payload = payload;
    block_set_next((void *)chunk, 0);
    return (void *)chunk;
}

static void large_release(void *chunk) {
    uint64_t payload = large_header(chunk)->payload;
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
        uint64_t victim_payload = large_header(victim)->payload;
        large_head = block_next(victim);
        release_pages((char *)victim + YIAN_HDR_BYTES, victim_payload);
        block_set_next(victim, 0);
        large_cached_bytes -= victim_payload;
    }
}

/* ── 对外接口 (编译器调用, 块头与检查不动) ── */

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
    if (*(uint64_t *)block_region(block) == YIAN_SLAB_MAGIC) {
        slab_release_block(block);
        return;
    }
    large_release(block);
}

/* 自测钩子: 块的可用负载容量 (slab 块取尺寸类, 大对象取 chunk 容量). */
uint64_t __secl_pool_payload(const void *block) {
    void *region = block_region(block);
    if (*(const uint64_t *)region == YIAN_SLAB_MAGIC) {
        const Slab *slab = (const Slab *)region;
        return yian_class_bytes[slab->class_index];
    }
    return ((const LargeHeader *)region)->payload;
}
