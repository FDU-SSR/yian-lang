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
 * 热路径是两个叶子函数: 弹链/取 bump 只有几条指令, 不建栈帧; 换 slab、大对象、内存
 * 耗尽全部转到 noinline 冷路径. 尺寸编译期已知的分配点由编译器算好类号直接调用
 * (类表在 yian_rt.h::YIAN_CLASS_BYTES), 运行时不必再按字节数查表.
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
#define YIAN_SLAB_CHUNK ((uint64_t)16 * YIAN_SLAB_BYTES)
#define YIAN_SLAB_HEADER ((uint64_t)64)
#define YIAN_LARGE_CACHE ((uint64_t)8 << 20)

#define YIAN_SLAB_MAGIC 0x5949414e534c4142ull  /* "YIANSLAB" */
#define YIAN_LARGE_MAGIC 0x5949414e4c415247ull /* "YIANLARG" */

/* 空闲链与缓存链写在空闲块负载的首字. */
#define YIAN_NEXT_OFFSET YIAN_HDR_BYTES

static const uint32_t yian_class_bytes[] = {YIAN_CLASS_BYTES};

_Static_assert(sizeof(yian_class_bytes) / sizeof(yian_class_bytes[0]) == YIAN_CLASS_COUNT,
               "YIAN_CLASS_BYTES must list YIAN_CLASS_COUNT classes");

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

static void *class_free[YIAN_CLASS_COUNT];  /* 每类空闲块链 */
static Slab *class_fill[YIAN_CLASS_COUNT];  /* 每类正在填充的 slab */

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

/* ── 类查找与块尺寸 ── */

static uint32_t class_bucket_start[64]; /* 每个 2 的幂桶里第一个够用的尺寸类 */
static int alloc_inited = 0;

/* 只在第一次通用分配时执行, 放在冷路径以免污染热路径的代码布局. */
static __attribute__((noinline, cold)) void alloc_init_slow(void) {
    for (uint32_t bucket = 0; bucket < 64; bucket++) {
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

/* 能容纳 ``bytes`` 的最小尺寸类的类号: clz 定桶 + 桶内至多 3 步.
 * 调用方保证 bytes 落在 [1, 最大类容量] 内, 且已执行过一次初始化. */
static inline uint32_t class_index(uint64_t bytes) {
    uint32_t index = class_bucket_start[63 - __builtin_clzll(bytes)];
    while (yian_class_bytes[index] < bytes) {
        index++;
    }
    return index;
}

/* 类内一块占用的字节数 (块头 + 负载, 16 B 对齐). 无需预计算表. */
static inline uint32_t class_stride(uint32_t index) {
    return (uint32_t)align_up(YIAN_HDR_BYTES + yian_class_bytes[index], 16);
}

static _Noreturn void alloc_fail(void) {
    static const uint8_t message[] = YIAN_OOM_MESSAGE;
    __yian_runtime_fail(message, sizeof(message) - 1);
}

/* ── slab ── */

/* 空闲区域链: 地址空间已经映射好、但还没交给任何尺寸类的 64 KiB 区域.
 * 链写在区域首字, 区域成为 slab 时被 magic 覆盖. */
static void *region_free = 0;

static inline void *region_next(const void *region) {
    void *value;
    memcpy(&value, region, sizeof(value));
    return value;
}

static inline void region_set_next(void *region, void *next) {
    memcpy(region, &next, sizeof(next));
}

/* 映射一块 64 KiB、64 KiB 对齐的 slab 区域, 描述符放在区域首部.
 *
 * 一次映射 YIAN_SLAB_CHUNK 字节并切成若干个 64 KiB 区域: 一个本次返回, 其余进
 * 空闲区域链. 区域粒度仍是 64 KiB (每个尺寸类的尾浪费上限不随 chunk 变大),
 * 而系统调用摊薄到每个区域不足一次; 头尾不足一个区域的零头还回内核. */
static __attribute__((noinline)) Slab *slab_map(void) {
    if (region_free != 0) {
        Slab *region = region_free;
        region_free = region_next(region);
        return region;
    }
    uint64_t total = YIAN_SLAB_CHUNK + YIAN_SLAB_BYTES;
    void *raw = mmap(0, (size_t)total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (raw == MAP_FAILED) {
        return 0;
    }
    uintptr_t base = (uintptr_t)align_up((uintptr_t)raw, YIAN_SLAB_BYTES);
    uintptr_t end = (uintptr_t)raw + total;
    uint32_t count = (uint32_t)((end - base) / YIAN_SLAB_BYTES);
    if (base > (uintptr_t)raw) {
        munmap(raw, (size_t)(base - (uintptr_t)raw));
    }
    uintptr_t used = base + (uintptr_t)count * YIAN_SLAB_BYTES;
    if (used < end) {
        munmap((void *)used, (size_t)(end - used));
    }
    for (uint32_t i = 1; i < count; i++) {
        void *spare = (void *)(base + (uintptr_t)i * YIAN_SLAB_BYTES);
        region_set_next(spare, region_free);
        region_free = spare;
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
static inline void *slab_take(Slab *slab) {
    char *cursor = slab->bump;
    if (cursor + class_stride(slab->class_index) > (char *)slab + YIAN_SLAB_BYTES) {
        return 0;
    }
    slab->bump = cursor + class_stride(slab->class_index);
    return cursor;
}

/* ── 分配与释放 ── */

/* 冷路径: 该类空闲链为空, 从正在填充的 slab 顺序取, 用尽则映射新 slab. */
static __attribute__((noinline)) void *class_refill(uint32_t index) {
    Slab *slab = class_fill[index];
    if (slab == 0) {
        slab = slab_new(index);
        class_fill[index] = slab;
    }
    void *block = slab_take(slab);
    if (block == 0) { /* 当前 slab 用尽: 换一块继续顺序取 */
        slab = slab_new(index);
        class_fill[index] = slab;
        block = slab_take(slab);
    }
    return block;
}

/* 热路径: 弹该类空闲链, 链空转冷路径. */
static inline void *class_pop(uint32_t index) {
    void *block = class_free[index];
    if (block != 0) {
        class_free[index] = block_next(block);
        return block;
    }
    return class_refill(index);
}

static void slab_release(void *block, uint32_t index) {
    block_set_next(block, class_free[index]);
    class_free[index] = block;
}

/* ── 大对象 ── */

static inline LargeHeader *large_header(void *chunk) {
    return (LargeHeader *)block_region(chunk);
}

static __attribute__((noinline)) void *large_alloc(uint64_t bytes) {
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

/* 冷路径: 大对象归还 (含按额度淘汰与退页). */
static __attribute__((noinline)) void large_release(void *chunk) {
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

/* ── 对外接口 (编译器调用, 块头与检查不动) ──
 *
 * 两个分配入口都写成叶子函数: 热路径只碰类链头与空闲块自身, 换 slab / 大对象 /
 * 内存耗尽全部转 YIAN_NOINLINE 的冷路径, 因此没有栈帧与寄存器保存开销. */

void *__secl_pool_alloc(uint64_t requested) {
    if (requested == 0) {
        requested = 1; /* 与 active_size 的空请求归一化一致 */
    }
    if (requested <= yian_class_bytes[YIAN_CLASS_COUNT - 1]) {
        if (__builtin_expect(!alloc_inited, 0)) {
            alloc_init_slow();
        }
        return class_pop(class_index(requested));
    }
    return large_alloc(requested);
}

/* 编译器在尺寸编译期已知的分配点直接用类号调用, 省掉按字节数查表. */
void *__secl_pool_alloc_class(uint32_t class_index_value) {
    if (class_index_value >= YIAN_CLASS_COUNT) { /* ABI 误用/版本不一致: 显式终止 */
        alloc_fail();
    }
    return class_pop(class_index_value);
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
