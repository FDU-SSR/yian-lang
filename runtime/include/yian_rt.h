/*
 * yian_rt.h: external ABI of the YIAN runtime.
 *
 * Runtime responsibilities:
 *   - Provide process globals (arguments, lock slots, key counter, frame shadow stack) for emitted inline code;
 *   - Provide failure paths (runtime_fail/panic) and the C main wrapper;
 *   - Check decisions, key generation, slot writes, SENTINEL invalidation, and live predicates belong
 *     to compiler-emitted inline code.
 *
 * This header is authoritative for the C runtime ABI and allocation size classes.
 * Compiler protocol constants and failure messages live in
 * yianc/src/codegen/abi/runtime.an. runtime/build.py --check-consistency-only
 * compares those fixed declarations and asks Clang to verify the integer assertions.
 */

#ifndef YIAN_RT_H
#define YIAN_RT_H

#include <stdint.h>

/* Lock entries occupy 8 B: {key:u32 @0,anchor_lo32:u32 @4}. Index ranges encode kind:
 * [0,YIAN_FRAME_LOCK_SLOTS) holds frame slots indexed by depth, followed by two constant slots,
 * then allocator-issued heap slots. Slot count/width must match runtime.an. */
#define YIAN_FRAME_LOCK_SLOTS (1u << 20)
#define YIAN_FRAME_LOCK_SLOT_BYTES 8u
#define YIAN_LOCK_TABLE_SLOTS ((uint64_t)1 << 26)
#define YIAN_LITERAL_LOCK_INDEX YIAN_FRAME_LOCK_SLOTS
#define YIAN_ENV_LOCK_INDEX (YIAN_FRAME_LOCK_SLOTS + 1u)
#define YIAN_HEAP_LOCK_BASE (YIAN_FRAME_LOCK_SLOTS + 2u)

/* Heap header bytes must match runtime.an::HEADER_BYTES.
 * 8 B = {extent:u32,pad:u32}; payload = base + YIAN_HDR_BYTES. */
#define YIAN_HDR_BYTES 8u

/* 4 GiB windows: pointer representation reconstructs block bases from data's high 32 bits and low-bit fields.
 * The entire [base,base+header+payload) must lie in one window. Aligned 64 KiB slabs naturally satisfy this
 * because 4 GiB is a multiple of 64 KiB; large_alloc chooses a suitable location for larger objects. */
#define YIAN_WINDOW_BYTES ((uint64_t)4 << 30)

/* The compiler emits constant-slot words (lockmech.py LITERAL_WORD/ENV_WORD: key zero and constant indices).
 * Runtime treats these slots as permanently valid and does not construct their words,
 * so no word macros are defined here. */

/* Diagnostic for the main wrapper's argument-ABI failure; must match
 * yianc/src/codegen/abi/runtime.an S002 byte-for-byte. */
#define YIAN_ABI_FAIL_MESSAGE "yian: safety error [S002]: invalid memory access\n"

/* Allocation-failure diagnostic; must match runtime.an R002 byte-for-byte. */
#define YIAN_OOM_MESSAGE "yian: runtime error [R002]: memory allocation failed\n"
/* Lock-table exhaustion message matching runtime.an R003. */
#define YIAN_METADATA_MESSAGE "yian: runtime error [R003]: safety metadata exhausted\n"

/* Size-class payload capacities in bytes, from 16 to 49152 at approximately 1.25 ratios.
 * The table index is the class number; larger requests use the large-object path.
 *
 * The compiler passes class indices for known sizes (see __secl_pool_alloc_class), making this table
 * part of the ABI. This C table is the sole allocation size-class source;
 * runtime/build.py checks its count and monotonicity with Clang. */
#define YIAN_CLASS_COUNT 36u
#define YIAN_CLASS_BYTES                                                                     \
    16, 20, 25, 32, 40, 50, 64, 80, 100, 128, 160, 200, 256, 320, 400, 512, 640, 800, 1024, \
        1280, 1600, 2048, 2560, 3200, 4096, 5120, 6400, 8192, 10240, 12800, 16384, 20480,    \
        25600, 32768, 40960, 49152

/* Heap allocator: returns block base (lock-slot address); payload = base + YIAN_HDR_BYTES.
 * Release returns the entire block; neither operation returns failure (OOM terminates with R002).
 *
 * __secl_pool_alloc looks up a class by bytes; __secl_pool_alloc_class uses a compiler-computed
 * class_index in [0,YIAN_CLASS_COUNT), avoiding lookup. */
void *__secl_pool_alloc(uint64_t requested);
void *__secl_pool_alloc_class(uint32_t class_index);
void __secl_pool_release(void *block);

/* Process arguments validated and written by the main wrapper. */
extern int32_t __yian_argc;
extern char **__yian_argv;

/* Read-only accessor for standard-library argument APIs; FFI cannot read the global directly. */
int32_t yian_argc(void);

/* Process-wide frame-key counter, accessed directly by inline load/store. */
extern uint64_t __yian_key_stack;

/* Lock table in BSS, resident on first touch: frames, constant slots, and heap. */
extern uint64_t __secl_lock_table[YIAN_LOCK_TABLE_SLOTS];
extern uint64_t __secl_frame_lock_depth;
/* Heap allocation state: free-list head (zero means empty, otherwise index+1) and bump cursor.
 * Emitted fast paths modify these three globals directly, exposing entry key/anchor writes to LLVM. */
extern uint64_t __secl_lock_bump;
extern uint64_t __secl_lock_free_head;

/* Compiler-emitted fast paths allocate from the free list; this entry takes fresh indices when empty
 * and reports R003 on exhaustion. Release increments the word's entry key and returns it to the list. */
uint64_t __secl_lock_bump_take(void);
void __secl_lock_release(uint64_t word);

/* Failure paths write stderr and terminate without allocation or return. */
void __yian_runtime_fail(const uint8_t *message, uint64_t length)
    __attribute__((noreturn, cold, noinline));
void __yian_panic(const uint8_t *message, uint64_t length)
    __attribute__((noreturn, cold, noinline));

#endif /* YIAN_RT_H */
