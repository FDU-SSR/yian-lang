/*
 * alloc.c: YIAN heap allocator with size-class arenas.
 *
 * Block layout (matching lockmech.py::BlockHeader): [lock(8) | active_size(8) | payload...],
 * with payload at base+YIAN_HDR_BYTES. The compiler writes lock/active_size; the allocator leaves the header alone.
 * Free-list links occupy the first payload word (YIAN_NEXT_OFFSET=YIAN_HDR_BYTES).
 *
 * Allocation paths, with one free list and filling slab per class:
 *   alloc: pop a free block, otherwise bump within the current slab, mapping a new one when exhausted.
 *   free: locate the region header's magic/class from the block address and push onto the class free list.
 * Both paths are O(1), touching only hot class state, slab cursors, and the free block itself.
 *
 * Hot paths are leaf functions without stack frames; slab replacement, large objects, and OOM
 * use noinline cold paths. The compiler passes known class indices directly for compile-time sizes,
 * using yian_rt.h::YIAN_CLASS_BYTES, avoiding runtime lookup by bytes.
 *
 * Size classes start at 16 B, with three steps per doubling and ratios near 1.25, totaling 36 classes
 * through 49152 B. Larger requests use large chunks. Slabs are independent 64 KiB aligned mappings;
 * large chunks start at offset 64 in aligned regions whose headers contain LargeHeader.
 * block & ~(SLAB_BYTES-1) finds either region header, distinguished by magic.
 *
 * Slab pages stay resident, with free blocks reused per class and RSS determined by class peaks.
 * Large-object cache eviction uses madvise(MADV_DONTNEED) to return physical pages, never munmap:
 * mappings remain, dangling slot reads yield zero, and checks report S003/S006 rather than segfaults.
 *
 * Single-threaded, without locks (see docs/security.md).
 */

#include "yian_rt.h"

#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* -- Parameters -- */

#define YIAN_SLAB_BYTES ((uint64_t)64 * 1024)
#define YIAN_SLAB_CHUNK ((uint64_t)16 * YIAN_SLAB_BYTES)
#define YIAN_SLAB_HEADER ((uint64_t)64)
#define YIAN_LARGE_CACHE ((uint64_t)8 << 20)

#define YIAN_SLAB_MAGIC 0x5949414e534c4142ull  /* "YIANSLAB" */
#define YIAN_LARGE_MAGIC 0x5949414e4c415247ull /* "YIANLARG" */

/* Free/cache links occupy the first word of a free block's payload. */
#define YIAN_NEXT_OFFSET YIAN_HDR_BYTES

static const uint32_t yian_class_bytes[] = {YIAN_CLASS_BYTES};

_Static_assert(sizeof(yian_class_bytes) / sizeof(yian_class_bytes[0]) == YIAN_CLASS_COUNT,
               "YIAN_CLASS_BYTES must list YIAN_CLASS_COUNT classes");

typedef struct Slab {
    uint64_t magic;
    char *bump;          /* Next block not yet loaned out. */
    uint32_t class_index;
    uint32_t pad;
} Slab;

_Static_assert(sizeof(Slab) <= YIAN_SLAB_HEADER, "Slab descriptor must fit the slab header");

/* Large-object region header; see large_alloc for the region/chunk offset. */
typedef struct LargeHeader {
    uint64_t magic;
    uint64_t payload;
} LargeHeader;

static void *class_free[YIAN_CLASS_COUNT];  /* Free-block list per class. */
static Slab *class_fill[YIAN_CLASS_COUNT];  /* Filling slab per class. */

/* Large-object cache links use the first payload word; excess blocks are evicted and physical pages returned. */
static void *large_head = 0;
static void *large_tail = 0;
static uint64_t large_cached_bytes = 0;

/* -- Blocks and regions -- */

static inline void *block_next(const void *block) {
    void *value;
    memcpy(&value, (const char *)block + YIAN_NEXT_OFFSET, sizeof(value));
    return value;
}

static inline void block_set_next(void *block, void *next) {
    memcpy((char *)block + YIAN_NEXT_OFFSET, &next, sizeof(next));
}

/* Region header: slab and large blocks use 64 KiB aligned regions, located by masking. */
static inline void *block_region(const void *block) {
    return (void *)((uintptr_t)block & ~(uintptr_t)(YIAN_SLAB_BYTES - 1));
}

static inline uint64_t align_up(uint64_t value, uint64_t align) {
    return (value + align - 1) & ~(align - 1);
}

/* madvise requires page alignment; return only complete pages inside [start,start+length). */
static void release_pages(void *start, uint64_t length) {
    long page_value = sysconf(_SC_PAGESIZE);
    uint64_t page = page_value > 0 ? (uint64_t)page_value : 4096;
    uintptr_t from = (uintptr_t)align_up((uintptr_t)start, page);
    uintptr_t to = ((uintptr_t)start + length) & ~(uintptr_t)(page - 1);
    if (to > from) {
        madvise((void *)from, (size_t)(to - from), MADV_DONTNEED);
    }
}

/* -- Class lookup and block sizes -- */

static uint32_t class_bucket_start[64]; /* First fitting size class in each power-of-two bucket. */
static int alloc_inited = 0;

/* Initialize on the first generic allocation, on a cold path to preserve hot-path code layout. */
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

/* Smallest class fitting bytes: clz selects the bucket, followed by at most three steps.
 * Caller guarantees bytes in [1,largest class capacity] and completed initialization. */
static inline uint32_t class_index(uint64_t bytes) {
    uint32_t index = class_bucket_start[63 - __builtin_clzll(bytes)];
    while (yian_class_bytes[index] < bytes) {
        index++;
    }
    return index;
}

/* Bytes per class block: header plus payload, aligned to 16 B; no precomputed table needed. */
static inline uint32_t class_stride(uint32_t index) {
    return (uint32_t)align_up(YIAN_HDR_BYTES + yian_class_bytes[index], 16);
}

static _Noreturn void alloc_fail(void) {
    static const uint8_t message[] = YIAN_OOM_MESSAGE;
    __yian_runtime_fail(message, sizeof(message) - 1);
}

/* ── slab ── */

/* Free regions: mapped 64 KiB areas not yet assigned to a size class.
 * Their first-word link is replaced by magic when the region becomes a slab. */
static void *region_free = 0;

static inline void *region_next(const void *region) {
    void *value;
    memcpy(&value, region, sizeof(value));
    return value;
}

static inline void region_set_next(void *region, void *next) {
    memcpy(region, &next, sizeof(next));
}

/* Map a 64 KiB aligned slab region with its descriptor at the region base.
 *
 * Map YIAN_SLAB_CHUNK bytes and split into 64 KiB regions, returning one and caching the rest.
 * Region granularity stays 64 KiB, so per-class tail waste does not grow with chunk size.
 * System calls amortize across regions; incomplete leading/trailing regions return to the kernel. */
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

/* Bump-allocate a slab block; return zero when exhausted. */
static inline void *slab_take(Slab *slab) {
    char *cursor = slab->bump;
    if (cursor + class_stride(slab->class_index) > (char *)slab + YIAN_SLAB_BYTES) {
        return 0;
    }
    slab->bump = cursor + class_stride(slab->class_index);
    return cursor;
}

/* -- Allocation and release -- */

/* Cold path: empty class free list; bump from the filling slab or map another when exhausted. */
static __attribute__((noinline)) void *class_refill(uint32_t index) {
    Slab *slab = class_fill[index];
    if (slab == 0) {
        slab = slab_new(index);
        class_fill[index] = slab;
    }
    void *block = slab_take(slab);
    if (block == 0) { /* Replace the exhausted slab and continue bump allocation. */
        slab = slab_new(index);
        class_fill[index] = slab;
        block = slab_take(slab);
    }
    return block;
}

/* Hot path: pop the class free list, or take the cold path when empty. */
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

/* -- Large objects -- */

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
    uint64_t span = align_up(YIAN_HDR_BYTES + payload + YIAN_SLAB_BYTES * 2, 4096);
    /* Reserve an extra window and two slabs to place the entire block within one 4 GiB window.
     * MAP_NORESERVE reserves virtual addresses; physical memory is committed only for written pages. */
    uint64_t total = span + YIAN_WINDOW_BYTES + YIAN_SLAB_BYTES * 2;
    void *raw = mmap(0, (size_t)total, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (raw == MAP_FAILED) {
        alloc_fail();
    }
    /* Chunks start at offset 64 in 64 KiB aligned regions, with LargeHeader at the base. */
    uintptr_t region = (uintptr_t)align_up((uintptr_t)raw + YIAN_SLAB_HEADER, YIAN_SLAB_BYTES);
    /* If [chunk,chunk+header+payload) crosses a window, move to the next window start, still 64 KiB aligned. */
    uintptr_t first = region + YIAN_SLAB_HEADER;
    if ((first >> 32) != ((first + YIAN_HDR_BYTES + payload - 1) >> 32)) {
        region = (uintptr_t)align_up(first, YIAN_WINDOW_BYTES);
    }
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

/* Cold path: large-object return, including budget-based eviction and page return. */
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
        /* Cache eviction returns physical pages but preserves virtual mappings for dangling reads. */
        void *victim = large_head;
        uint64_t victim_payload = large_header(victim)->payload;
        large_head = block_next(victim);
        release_pages((char *)victim + YIAN_HDR_BYTES, victim_payload);
        block_set_next(victim, 0);
        large_cached_bytes -= victim_payload;
    }
}

/* -- Compiler-facing API; headers and checks are compiler-owned --
 *
 * Both allocation entries are leaf functions: hot paths touch class heads/free blocks; slab replacement,
 * large objects, and OOM use YIAN_NOINLINE cold paths, avoiding frame/register-save overhead. */

void *__secl_pool_alloc(uint64_t requested) {
    if (requested == 0) {
        requested = 1; /* Match active_size normalization for empty requests. */
    }
    if (requested <= yian_class_bytes[YIAN_CLASS_COUNT - 1]) {
        if (__builtin_expect(!alloc_inited, 0)) {
            alloc_init_slow();
        }
        return class_pop(class_index(requested));
    }
    return large_alloc(requested);
}

/* The compiler passes class indices for known sizes, avoiding byte-size lookup. */
void *__secl_pool_alloc_class(uint32_t class_index_value) {
    if (class_index_value >= YIAN_CLASS_COUNT) { /* Terminate explicitly on ABI misuse/version mismatch. */
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

/* Self-test capacity hook: use the slab class or large chunk's payload capacity. */
uint64_t __secl_pool_payload(const void *block) {
    void *region = block_region(block);
    if (*(const uint64_t *)region == YIAN_SLAB_MAGIC) {
        return yian_class_bytes[((const Slab *)region)->class_index];
    }
    return ((const LargeHeader *)region)->payload;
}

/* -- Heap lock-entry allocation and release --
 *
 * Entries are {key:u32 @0,anchor_lo32:u32 @4}. Compiler/runtime use frame and constant slots directly.
 * Heap entries store next-free-index+1 in anchor, with global __secl_lock_free_head as head
 * (zero means empty). Allocation and release both increment key, so dangling pointers mismatch
 * even after release and before reuse.
 *
 * Compiler-emitted free-list fast paths pop an index, update the global head, then inline
 * key increment, anchor write, and word construction. LLVM sees key writes, allowing live checks
 * on fresh allocations to fold to true rather than remain in inner loops.
 * Only an empty free list requiring bump calls __secl_lock_bump_take.
 */
static _Noreturn void lock_fail(void) {
    static const uint8_t message[] = YIAN_METADATA_MESSAGE;
    __yian_runtime_fail(message, sizeof(message) - 1);
}

static inline void lock_store(uint64_t index, uint32_t key, uint32_t anchor) {
    __secl_lock_table[index] = (uint64_t)key | ((uint64_t)anchor << 32);
}

uint64_t __secl_lock_bump_take(void) {
    uint64_t index = __secl_lock_bump;
    if (index >= YIAN_LOCK_TABLE_SLOTS) {
        lock_fail();
    }
    __secl_lock_bump = index + 1;
    return index;
}

void __secl_lock_release(uint64_t word) {
    uint64_t index = word & 0xFFFFFFFFu;
    uint32_t key = (uint32_t)__secl_lock_table[index] + 1;
    lock_store(index, key, (uint32_t)__secl_lock_free_head);
    __secl_lock_free_head = index + 1;
}
