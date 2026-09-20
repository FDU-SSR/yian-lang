/*
 * selftest.c — 运行时库自测 (不依赖编译器产物).
 *
 * 覆盖: 全局对象初值、wrapper main 的参数 ABI 与调用、失败路径的 stderr 字节与退出码。
 * 链接方式: clang runtime/src/runtime.c runtime/selftest/selftest.c -o selftest
 * 运行时提供 main; 本文件提供 __yian_main, 正常路径返回 0, 失败路径 _exit(1).
 */

#include "yian_rt.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static int failures = 0;

static void check(int ok, const char *what) {
    if (!ok) {
        printf("FAIL: %s\n", what);
        failures++;
    }
}

/* 在子进程里调用失败函数, 把 stderr 收进 buf, 返回退出码. */
static int run_fail_child(int panic_mode, char *buf, size_t cap, size_t *out_len) {
    int fds[2];
    if (pipe(fds) != 0) {
        return -1;
    }
    pid_t pid = fork();
    if (pid == 0) {
        close(fds[0]);
        dup2(fds[1], 2);
        close(fds[1]);
        if (panic_mode) {
            __yian_panic((const uint8_t *)"test-message", 12);
        } else {
            __yian_runtime_fail((const uint8_t *)"test-message", 12);
        }
        _exit(7); /* 失败函数不返回; 到达这里说明实现有误 */
    }
    close(fds[1]);
    ssize_t got = read(fds[0], buf, cap - 1);
    close(fds[0]);
    *out_len = got > 0 ? (size_t)got : 0;
    buf[*out_len] = '\0';
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        return -1;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

/* 分配器自测钩子: 块的可用负载容量 (块头本身不再记录容量). */
extern uint64_t __secl_pool_payload(const void *block);

static void check_allocator(void) {
    /* 大对象整块必须落在同一个 4 GiB 窗口内: 指针表示用数据地址高 32 位加低 32 位
     * 字段重建块首, 跨窗口会让重建出的块首偏一个窗口. */
    static const uint64_t window_sizes[] = {49153, 262144, 1048576, 8 * 1024 * 1024};
    for (size_t i = 0; i < sizeof(window_sizes) / sizeof(window_sizes[0]); i++) {
        void *block = __secl_pool_alloc(window_sizes[i]);
        uintptr_t first = (uintptr_t)block;
        uintptr_t last = first + YIAN_HDR_BYTES + window_sizes[i] - 1;
        check((first >> 32) == (last >> 32), "large chunk stays within one 4 GiB window");
        __secl_pool_release(block);
    }

    /* 大对象缓存: 缓存为空时, 独占尺寸的 chunk 释放后复用同一块. */
    void *large = __secl_pool_alloc(300000);
    __secl_pool_release(large);
    void *large_again = __secl_pool_alloc(300000);
    check(large_again == large, "freed large chunk is reused from the cache");
    __secl_pool_release(large_again);

    /* 请求尺寸覆盖所有尺寸类与大对象: 块地址 16 对齐、容量足够、负载可读写. */
    static const uint64_t sizes[] = {1, 8, 16, 17, 100, 1024, 4096, 20000, 49152, 49153, 262144, 1048576};
    for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
        uint64_t requested = sizes[i];
        void *block = __secl_pool_alloc(requested);
        check(((uintptr_t)block & 15u) == 0, "allocated block is 16-byte aligned");
        check(__secl_pool_payload(block) >= requested, "allocated block capacity covers the request");
        if (requested <= 49152) {
            /* 尺寸类必须是最小的够用档: 容量不应超过请求的两倍. */
            check(__secl_pool_payload(block) < requested * 2 + 16, "class is the smallest covering class");
        }
        memset((char *)block + YIAN_HDR_BYTES, 0x5a, (size_t)requested);
        check(*((unsigned char *)block + YIAN_HDR_BYTES) == 0x5a, "payload is writable");
        __secl_pool_release(block);
    }

    /* 同尺寸 churn: 重复申请/释放同一档应复用同一块. */
    void *first = __secl_pool_alloc(64);
    __secl_pool_release(first);
    check(__secl_pool_alloc(64) == first, "freed class block is reused");

    /* 类号入口 (编译器在尺寸已知的分配点直接传类号): 逐档容量正确且可复用. */
    static const uint32_t classes[] = {YIAN_CLASS_BYTES};
    check(sizeof(classes) / sizeof(classes[0]) == YIAN_CLASS_COUNT, "class table matches the count macro");
    for (uint32_t index = 0; index < YIAN_CLASS_COUNT; index++) {
        void *block = __secl_pool_alloc_class(index);
        check(((uintptr_t)block & 15u) == 0, "class-indexed block is 16-byte aligned");
        check(__secl_pool_payload(block) == classes[index], "class-indexed block has the class capacity");
        __secl_pool_release(block);
        check(__secl_pool_alloc_class(index) == block, "class-indexed block is reused");
        __secl_pool_release(block);
    }

    /* 混合尺寸: 保留一批小块, 交替申请/释放大块 (旧池首适配的病态模式). */
    enum { SMALL_COUNT = 256 };
    void *small[SMALL_COUNT];
    for (int i = 0; i < SMALL_COUNT; i++) {
        small[i] = __secl_pool_alloc(64);
    }
    void *previous_large = 0;
    for (int round = 0; round < 32; round++) {
        void *big = __secl_pool_alloc(40000);
        if (previous_large != 0) {
            __secl_pool_release(previous_large);
        }
        __secl_pool_release(small[round % SMALL_COUNT]);
        small[round % SMALL_COUNT] = __secl_pool_alloc(64);
        previous_large = big;
    }
    __secl_pool_release(previous_large);
    for (int i = 0; i < SMALL_COUNT; i++) {
        __secl_pool_release(small[i]);
    }

    /* 归还物理页后地址空间仍在: 悬垂读已释放块的锁槽不得触发段错误. */
    enum { RELEASE_COUNT = 20000 };
    void **blocks = malloc(sizeof(void *) * RELEASE_COUNT);
    check(blocks != 0, "selftest can allocate its own bookkeeping array");
    if (blocks != 0) {
        for (int i = 0; i < RELEASE_COUNT; i++) {
            blocks[i] = __secl_pool_alloc(64);
            *(uint64_t *)blocks[i] = ~(uint64_t)0; /* 编译器释放前写 SENTINEL */
        }
        for (int i = 0; i < RELEASE_COUNT; i++) {
            __secl_pool_release(blocks[i]);
        }
        /* 归还后读回锁槽: 只要不崩溃即可 (madvise 后读回 0). */
        volatile uint64_t stale = *(volatile uint64_t *)blocks[0];
        (void)stale;
        /* 复用仍然可用 */
        void *again = __secl_pool_alloc(64);
        check(((uintptr_t)again & 15u) == 0, "allocator still works after returning pages");
        __secl_pool_release(again);
        free(blocks);
    }

    /* 超过常驻额度后 slab 会归还物理页: 释放后再读锁槽仍不得崩溃. */
    enum { BIG_COUNT = 4096 };
    void **big = malloc(sizeof(void *) * BIG_COUNT);
    check(big != 0, "selftest can allocate bookkeeping for slab reclamation");
    if (big != 0) {
        for (int i = 0; i < BIG_COUNT; i++) {
            big[i] = __secl_pool_alloc(49152);
            memset((char *)big[i] + YIAN_HDR_BYTES, 0x3c, 4096); /* 触达页 */
        }
        for (int i = 0; i < BIG_COUNT; i++) {
            __secl_pool_release(big[i]);
        }
        volatile uint64_t stale = *(volatile uint64_t *)big[0];
        (void)stale;
        void *reused = __secl_pool_alloc(49152);
        check(((uintptr_t)reused & 15u) == 0, "allocator reuses after returning slab pages");
        __secl_pool_release(reused);
        free(big);
    }
}

void __yian_main(void) {
    char buf[256];
    size_t len = 0;

    /* 全局对象初值 */
    check(__yian_env_lock == YIAN_LITERAL_KEY, "env lock initial key");
    check(__yian_lit_lock == YIAN_LITERAL_KEY, "literal lock initial key");
    check(__yian_key_heap == 0, "heap key counter starts at 0");
    check(__yian_key_stack == 0, "stack key counter starts at 0");
    check(__secl_frame_lock_depth == 0, "frame lock depth starts at 0");
    check(__secl_frame_locks[0] == 0 && __secl_frame_locks[YIAN_FRAME_LOCK_SLOTS - 1] == 0,
          "frame lock arena is zero-initialized");

    /* wrapper main 已经把参数写进全局 */
    check(__yian_argc >= 1, "wrapper main stored argc");
    check(__yian_argv != 0 && __yian_argv[0] != 0, "wrapper main stored argv");

    /* 失败路径: 原样写 stderr, 退出码 1 */
    int rc = run_fail_child(0, buf, sizeof(buf), &len);
    check(rc == 1, "runtime_fail exits with 1");
    check(len == 12 && memcmp(buf, "test-message", 12) == 0, "runtime_fail writes message verbatim");

    /* panic: 前缀 + 消息 + 换行, 退出码 1 */
    rc = run_fail_child(1, buf, sizeof(buf), &len);
    check(rc == 1, "panic exits with 1");
    check(strcmp(buf, "yian: panic: test-message\n") == 0, "panic writes prefix, message and newline");

    /* 参数 ABI 失败消息与 compiler/runtime_error.py 的 S002 文本一致由构建脚本断言. */
    check(strcmp(YIAN_ABI_FAIL_MESSAGE, "yian: safety error [S002]: invalid memory access\n") == 0,
          "ABI failure message text");

    check_allocator();

    if (failures != 0) {
        printf("selftest: %d failure(s)\n", failures);
        exit(1); /* 走 exit 而不是 _exit: 让 stdout 缓冲写出失败明细 */
    }
    printf("selftest: ok\n");
}
