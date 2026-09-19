/*
 * alloc.c — YIAN 堆分配器: 尺寸类 arena.
 *
 * 块布局 (与 lockmech.py::BlockHeader 一致): [lock(8) | active_size(8) | 负载...],
 * 负载从基址 + YIAN_HDR_BYTES 开始. 编译器写 lock 与 active_size; 分配器不占用块头,
 * 空闲块的自由链写在空闲负载的首字 (YIAN_NEXT_OFFSET = YIAN_HDR_BYTES).
 *
 * 分配路径 (每类一条自由链 + 一个正在填充的 slab):
 *   alloc: 类内自由链非空 → 弹出一块; 否则从当前 slab 顺序取一块, 用尽再映射新 slab.
 *   free : 由块地址定位 region 首部的 magic/类号, 把块压回该类自由链.
 * 两个路径都是 O(1) 且只碰热数据: 类数组、当前 slab 的 bump 游标、空闲块自身.
 *
 * 尺寸类: 16 B 起、每倍频 3 档、比例约 1.25, 共 36 档至 49152 B; 更大的请求走大对象
 * chunk. slab 是 64 KiB、64 KiB 对齐的独立映射; 大对象 chunk 放在 64 KiB 对齐 region
 * 的 +64 处, region 首部放 LargeHeader. 因此 `block & ~(SLAB_BYTES-1)` 对两种块都指向
 * region 首部, 用其中的 magic 区分.
 *
 * 归还: slab 页常驻 (空闲块留在类自由链里复用, 长跑 RSS 由各类峰值决定); 大对象在
 * 超出缓存额度被淘汰时用 madvise(MADV_DONTNEED) 归还物理页, 但从不 munmap——地址空间
 * 始终保留, 悬垂指针读已释放块的锁槽仍命中映射 (读回 0), 检查按 S003/S006 报错而非段错误.
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
    char *bump;          /* 下一个尚未借出的块 */
    uint32_t class_index;
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
static void *class_free[YIAN_CLASS_COUNT];  /* 每类空闲块链 */
static Slab *class_fill[YIAN_CLASS_COUNT];  /* 每类正在填充的 slab */
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

/* region 首部: slab 块与大对象块都按 64 KiB 对齐, 因此掩码即可定位. */
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
    slab->bump = (char *)slab + YIAN_SLAB_HEADER;
    return slab;
}

/* 从 slab 顺序取一块; 用尽返回 0. */
static void *slab_take(Slab *slab) {
    char *cursor = slab->bump;
    if (cursor + class_stride[slab->class_index] > (char *)slab + YIAN_SLAB_BYTES) {
        return 0;
    }
    slab->bump = cursor + class_stride[slab->class_index];
    return cursor;
}

/* ── 分配与释放 ── */

static void *class_alloc(uint64_t bytes) {
    uint32_t index = class_index(bytes);
    void *block = class_free[index];
    if (block != 0) {
        class_free[index] = block_next(block);
        return block;
    }
    Slab *slab = class_fill[index];
    if (slab == 0) {
        slab = slab_new(index);
        class_fill[index] = slab;
    }
    block = slab_take(slab);
    if (block == 0) { /* 当前 slab 用尽: 换一块继续顺序取 */
        slab = slab_new(index);
        class_fill[index] = slab;
        block = slab_take(slab);
    }
    return block;
}

static void slab_release(void *block, uint32_t index) {
    block_set_next(block, class_free[index]);
    class_free[index] = block;
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
    void *region = block_region(block);
    if (*(uint64_t *)region == YIAN_SLAB_MAGIC) {
        slab_release(block, ((Slab *)region)->class_index);
        return;
    }
    large_release(block);
}

/* 自测钩子: 块的可用负载容量 (slab 块取尺寸类, 大对象取 chunk 容量). */
uint64_t __secl_pool_payload(const void *block) {
    void *region = block_region(block);
    if (*(const uint64_t *)region == YIAN_SLAB_MAGIC) {
        return yian_class_bytes[((const Slab *)region)->class_index];
    }
    return ((const LargeHeader *)region)->payload;
}
