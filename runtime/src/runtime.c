/*
 * runtime.c — YIAN 运行时库的进程级对象、失败路径与 C 入口包装.
 *
 * 语义与"运行时在编译器里手拼 IR"的版本逐条对应; 细节约定见 yian_rt.h。
 */

#include "yian_rt.h"

#include <unistd.h>

int32_t __yian_argc = 0;
char **__yian_argv = 0;

uint64_t __yian_key_stack = 0;

/* 锁表: 帧段 [0, 2^20) + 字面量/环境常量槽(key = 0, 即 BSS 初值) + 堆段.
 * 堆段的快路径由编译器发射的 IR 直接改 bump/free_head 与表项. */
uint64_t __secl_lock_table[YIAN_LOCK_TABLE_SLOTS];
uint64_t __secl_lock_bump = YIAN_HEAP_LOCK_BASE;
uint64_t __secl_lock_free_head = 0;

uint64_t __secl_frame_lock_depth = 0;

/* 失败路径上的写入: 尽力写完, 但不重试、不分配. */
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

/* 语言入口: fn main() 发射为 void @__yian_main(). */
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
