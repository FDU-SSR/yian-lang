/*
 * selftest.c — 运行时库自测 (不依赖编译器产物).
 *
 * 覆盖: 全局对象初值、wrapper main 的参数 ABI 与调用、失败路径的 stderr 字节与退出码。
 * 链接方式: clang runtime/src/runtime.c runtime/selftest/selftest.c -o selftest
 * 运行时提供 main; 本文件提供 __yian_main, 正常路径返回 0, 失败路径 _exit(1).
 */

#include "yian_rt.h"

#include <stdio.h>
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

    if (failures != 0) {
        printf("selftest: %d failure(s)\n", failures);
        _exit(1);
    }
    printf("selftest: ok\n");
}
