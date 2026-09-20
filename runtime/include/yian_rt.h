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

/* 堆块头字节数: 必须与 lockmech.py::BlockHeader.BYTES 一致.
 * 16 B = {lock, active_size}; 负载 = 基址 + YIAN_HDR_BYTES. */
#define YIAN_HDR_BYTES 16u

/* 4 GiB 窗口: 指针表示用"数据地址高 32 位 + 低 32 位字段"重建块首, 因此一个块的
 * [块首, 块首 + 块头 + 负载) 必须整体落在同一个窗口内. 64 KiB 的 slab 天然满足
 * (4 GiB 是 64 KiB 的整数倍), 大对象由 large_alloc 挑落点保证. */
#define YIAN_WINDOW_BYTES ((uint64_t)4 << 30)

/* 进程生命周期锁槽的键 (字面量锁与环境锁都永不失效). */
#define YIAN_LITERAL_KEY 1ull

/* wrapper main 的参数 ABI 校验失败时写出的诊断 (必须与
 * compiler/runtime_error.py 的 S002 消息逐字节一致). */
#define YIAN_ABI_FAIL_MESSAGE "yian: safety error [S002]: invalid memory access\n"

/* 分配失败时写出的诊断 (必须与 compiler/runtime_error.py 的 R002 消息逐字节一致). */
#define YIAN_OOM_MESSAGE "yian: runtime error [R002]: memory allocation failed\n"

/* 尺寸类表: 每档的负载容量 (B), 从 16 B 起按约 1.25 的比例到 49152 B. 类号就是
 * 表下标; 更大的请求走大对象路径.
 *
 * 编译器在尺寸已知的分配点直接传类号 (见 __secl_pool_alloc_class), 因此这张表是
 * ABI 的一部分: compiler/runtime_lib.py::CLASS_BYTES 是它的 Python 镜像,
 * runtime/build.py --check 断言两侧逐项相等. */
#define YIAN_CLASS_COUNT 36u
#define YIAN_CLASS_BYTES                                                                     \
    16, 20, 25, 32, 40, 50, 64, 80, 100, 128, 160, 200, 256, 320, 400, 512, 640, 800, 1024, \
        1280, 1600, 2048, 2560, 3200, 4096, 5120, 6400, 8192, 10240, 12800, 16384, 20480,    \
        25600, 32768, 40960, 49152

/* 堆分配器: 返回块基址 (锁槽地址), 负载 = 基址 + YIAN_HDR_BYTES;
 * 释放整块归还; 两者都不返回失败 (内存耗尽时按 R002 终止).
 *
 * __secl_pool_alloc 按请求字节数查表选类; __secl_pool_alloc_class 直接用编译器
 * 算好的类号 (省掉查表), class_index 必须落在 [0, YIAN_CLASS_COUNT). */
void *__secl_pool_alloc(uint64_t requested);
void *__secl_pool_alloc_class(uint32_t class_index);
void __secl_pool_release(void *block);

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
