/*
 * yian_rt.h — YIAN 运行时库的对外 ABI.
 *
 * 运行时的职责边界:
 *   - 提供进程级全局对象 (参数、锁槽、键计数器、帧锁影子栈) 供编译器内联代码读写;
 *   - 提供失败路径 (runtime_fail / panic) 与 C 入口包装 main;
 *   - 不参与任何检查判定: 键生成、锁槽写入、SENTINEL 失效、live 判定都在编译器发射的
 *     内联代码里。
 *
 * 本头文件是 ABI 常量的唯一真值; compiler/codegen/cfg/lockmech.py 是它的 Python 镜像,
 * 两者由 runtime/build.py --check 断言一致。
 */

#ifndef YIAN_RT_H
#define YIAN_RT_H

#include <stdint.h>

/* 帧锁影子栈: 槽位数与槽宽必须与 lockmech.py::FrameLockArena 一致. */
#define YIAN_FRAME_LOCK_SLOTS (1u << 20)
#define YIAN_FRAME_LOCK_SLOT_BYTES 8u

/* 进程生命周期锁槽的键 (字面量锁与环境锁都永不失效). */
#define YIAN_LITERAL_KEY 1ull

/* wrapper main 的参数 ABI 校验失败时写出的诊断 (必须与
 * compiler/runtime_error.py 的 S002 消息逐字节一致). */
#define YIAN_ABI_FAIL_MESSAGE "yian: safety error [S002]: invalid memory access\n"

/* 进程参数: 由 wrapper main 校验后写入. */
extern int32_t __yian_argc;
extern char **__yian_argv;

/* 进程级锁槽与键计数器 (内联代码直接 load/store). */
extern uint64_t __yian_env_lock;
extern uint64_t __yian_lit_lock;
extern uint64_t __yian_key_heap;
extern uint64_t __yian_key_stack;

/* 帧锁影子栈 (BSS; 页按首次触达常驻). */
extern uint64_t __secl_frame_locks[YIAN_FRAME_LOCK_SLOTS];
extern uint64_t __secl_frame_lock_depth;

/* 失败路径: 只写 stderr 并终止, 不分配内存、不返回. */
void __yian_runtime_fail(const uint8_t *message, uint64_t length)
    __attribute__((noreturn, cold, noinline));
void __yian_panic(const uint8_t *message, uint64_t length)
    __attribute__((noreturn, cold, noinline));

#endif /* YIAN_RT_H */
