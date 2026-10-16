/*
 * runtime.c: process globals, failure paths, and the C entry wrapper for YIAN.
 *
 * ABI contracts are defined in yian_rt.h.
 */

#include "yian_rt.h"

#include <unistd.h>

int32_t __yian_argc = 0;
char **__yian_argv = 0;

uint64_t __yian_key_stack = 0;

/* Lock table: frames [0,2^20), constant slots (key zero, the BSS initial value), and heap.
 * Compiler-emitted IR directly updates bump/free_head and heap entries on fast paths. */
uint64_t __secl_lock_table[YIAN_LOCK_TABLE_SLOTS];
uint64_t __secl_lock_bump = YIAN_HEAP_LOCK_BASE;
uint64_t __secl_lock_free_head = 0;

uint64_t __secl_frame_lock_depth = 0;

/* Failure-path writes are best effort, without retries or allocation. */
static void write_all(const uint8_t *bytes, uint64_t length) {
    while (length > 0) {
        ssize_t written = write(2, bytes, (size_t)length);
        if (written <= 0) {
            return;
        }
        bytes += written;
        length -= (uint64_t)written;
    }
}

void __yian_runtime_fail(const uint8_t *message, uint64_t length) {
    write_all(message, length);
    _exit(1);
}

void __yian_panic(const uint8_t *message, uint64_t length) {
    static const uint8_t prefix[] = "yian: panic: ";
    static const uint8_t newline[] = "\n";
    write_all(prefix, sizeof(prefix) - 1);
    write_all(message, length);
    write_all(newline, sizeof(newline) - 1);
    _exit(1);
}

/* Language entry: fn main() emits void @__yian_main(). */
extern void __yian_main(void);

int main(int argc, char **argv) {
    if (argc < 0 || argv == 0) {
        static const uint8_t abi_fail[] = YIAN_ABI_FAIL_MESSAGE;
        write_all(abi_fail, sizeof(abi_fail) - 1);
        _exit(1);
    }
    __yian_argc = (int32_t)argc;
    __yian_argv = argv;
    __yian_main();
    return 0;
}
