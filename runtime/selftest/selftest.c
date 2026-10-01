/*
 * selftest.c — 运行时库自测 (不依赖编译器产物).
 *
 * 覆盖: 全局对象初值、wrapper main 的参数 ABI 与调用、失败路径的 stderr 字节与退出码。
 * 链接方式: clang runtime/src/runtime.c runtime/selftest/selftest.c -o selftest
 * 运行时提供 main; 本文件提供 __yian_main, 正常路径返回 0, 失败路径 _exit(1).
 */

#include "yian_rt.h"
#include "yian_io.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/time.h>
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
    /* panic 分多次 write 输出, 单次 read 可能只拿到第一段——读到 EOF 再收尾. */
    size_t total = 0;
    while (total < cap - 1) {
        ssize_t got = read(fds[0], buf + total, cap - 1 - total);
        if (got <= 0) {
            break;
        }
        total += (size_t)got;
    }
    close(fds[0]);
    *out_len = total;
    buf[total] = '\0';
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

static volatile sig_atomic_t io_signals = 0;
static void io_signal_handler(int signal_number) {
    (void)signal_number;
    io_signals++;
}

static void check_io(void) {
    char root[] = "/tmp/yian-runtime-io-XXXXXX";
    if (!mkdtemp(root)) { check(0, "I/O fixture directory"); return; }
    char file[256], link[256], invalid[256], restricted[256];
    snprintf(file, sizeof(file), "%s/data", root);
    snprintf(link, sizeof(link), "%s/link", root);
    snprintf(invalid, sizeof(invalid), "%s/data/child", root);
    snprintf(restricted, sizeof(restricted), "%s/restricted", root);
    check(yian_io_open(file, 1) == -ENOENT, "open preserves ENOENT");
    check(yian_io_open(file, 9) == -EINVAL, "create requires write access");
    int fd = (int)yian_io_open(file, 2 | 8 | 32);
    check(fd >= 0, "exclusive creation");
    if (fd >= 0) {
        check(fcntl(fd, F_GETFD) & FD_CLOEXEC, "file descriptor close-on-exec");
        check(yian_io_open(file, 2 | 32) == -EEXIST, "exclusive collision preserves EEXIST");
        const uint8_t data[] = {0, 255, 128, 10};
        check(yian_io_write(fd, data, sizeof(data)) == 4, "binary write count");
        uint64_t fields[4];
        check(yian_io_fmetadata(fd, fields) == 0 && fields[0] == 1 && fields[1] == 4,
              "fixed-width file metadata");
        check(yian_io_set_len(fd, 2) == 0 && yian_io_sync(fd) == 0, "truncate and sync");
        check(yian_io_set_len(fd, UINT64_MAX) == -EOVERFLOW, "file length overflow");
        check(yian_io_close(fd) == 0, "file close");
    }
    fd = (int)yian_io_open(file, 1);
    if (fd >= 0) {
        uint8_t data[4] = {9, 9, 9, 9};
        check(yian_io_read(fd, data, sizeof(data)) == 2 && data[0] == 0 && data[1] == 255,
              "binary read and short count");
        check(yian_io_read(fd, data, sizeof(data)) == 0, "EOF");
        check(yian_io_seek(fd, 0, 0) == 0 && yian_io_read(fd, data, 1) == 1, "seek");
        check(yian_io_close(fd) == 0, "read descriptor close");
    } else { check(0, "open binary file"); }
    uint64_t fields[4] = {99, 99, 99, 99};
    check(yian_io_metadata(invalid, 1, fields) == -ENOTDIR && fields[0] == 99,
          "failed metadata preserves errno and does not publish fields");
    check(symlink("data", link) == 0, "symlink fixture");
    check(yian_io_metadata(link, 0, fields) == 0 && fields[0] == 3, "lstat link");
    check(yian_io_metadata(link, 1, fields) == 0 && fields[0] == 1, "stat follows link");
    uint8_t path[256];
    check(yian_io_canonicalize(link, path, sizeof(path)) == (int64_t)strlen(file) &&
          memcmp(path, file, strlen(file)) == 0, "canonicalize follows link");
    check(yian_io_canonicalize(link, path, 1) == -ERANGE, "canonicalize buffer size");
    check(yian_io_current_dir(path, 1) == -ERANGE, "current directory buffer size");
    int32_t error = 0;
    void *directory = yian_io_dir_open(root, &error);
    check(directory != NULL && error == 0, "directory open");
    if (directory) {
        int entries = 0;
        int64_t n;
        while ((n = yian_io_dir_next(directory, path, sizeof(path))) > 0) {
            check((n == 4 && !memcmp(path, "data", 4)) || (n == 4 && !memcmp(path, "link", 4)),
                  "directory names exclude dot entries");
            entries++;
        }
        check(n == 0 && entries == 2, "directory EOF");
        check(yian_io_dir_close(directory) == 0, "directory close");
    }
    check(yian_io_dir_open(file, &error) == NULL && error == ENOTDIR, "directory open error");
    check(yian_io_remove(root, 1) == -ENOTEMPTY, "nonempty directory error");

    fd = open(restricted, O_CREAT | O_WRONLY | O_TRUNC, 0000);
    check(fd >= 0, "permission fixture");
    if (fd >= 0) close(fd);
    chmod(root, 0755);
    pid_t child = fork();
    if (child == 0) {
        if (geteuid() == 0 && setuid(65534) != 0) _exit(2);
        _exit(yian_io_open(restricted, 1) == -EACCES ? 0 : 1);
    }
    int status = 0;
    if (child > 0) waitpid(child, &status, 0);
    check(child > 0 && WIFEXITED(status) && WEXITSTATUS(status) == 0,
          "permission denied independent of root test execution");

    int pipefd[2];
    if (pipe(pipefd) == 0) {
        close(pipefd[0]);
        sigset_t before, after;
        sigprocmask(SIG_SETMASK, NULL, &before);
        check(yian_io_write(pipefd[1], (const uint8_t *)"x", 1) == -EPIPE,
              "broken pipe returns error instead of terminating");
        sigprocmask(SIG_SETMASK, NULL, &after);
        check(sigismember(&before, SIGPIPE) == sigismember(&after, SIGPIPE), "SIGPIPE mask preserved");
        sigset_t blocked, pending;
        sigemptyset(&blocked);
        sigaddset(&blocked, SIGPIPE);
        sigprocmask(SIG_BLOCK, &blocked, &before);
        raise(SIGPIPE);
        check(yian_io_write(pipefd[1], (const uint8_t *)"x", 1) == -EPIPE, "broken pipe with pending signal");
        sigpending(&pending);
        check(sigismember(&pending, SIGPIPE), "preexisting SIGPIPE remains pending");
        int caught = 0;
        sigwait(&blocked, &caught);
        sigprocmask(SIG_SETMASK, &before, NULL);
        close(pipefd[1]);
    } else { check(0, "broken pipe fixture"); }

    if (pipe(pipefd) == 0) {
        struct sigaction action = {0}, old_action;
        action.sa_handler = io_signal_handler;
        sigemptyset(&action.sa_mask);
        sigaction(SIGALRM, &action, &old_action);
        child = fork();
        if (child == 0) {
            close(pipefd[0]);
            usleep(50000);
            _exit(write(pipefd[1], "x", 1) == 1 ? 0 : 1);
        }
        close(pipefd[1]);
        if (child > 0) {
            struct itimerval timer = {{0, 1000}, {0, 1000}}, stopped = {0};
            setitimer(ITIMER_REAL, &timer, NULL);
            uint8_t byte = 0;
            int64_t n = yian_io_read(pipefd[0], &byte, 1);
            setitimer(ITIMER_REAL, &stopped, NULL);
            check(n == 1 && byte == 'x' && io_signals > 0, "blocking read retries EINTR");
            waitpid(child, &status, 0);
            check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "EINTR writer child");
        } else { check(0, "EINTR child creation"); }
        close(pipefd[0]);
        sigaction(SIGALRM, &old_action, NULL);
    } else { check(0, "EINTR pipe fixture"); }
    if (pipe(pipefd) == 0) {
        int flags = fcntl(pipefd[1], F_GETFL);
        fcntl(pipefd[1], F_SETFL, flags | O_NONBLOCK);
        uint8_t fill[4096] = {0};
        while (write(pipefd[1], fill, sizeof(fill)) > 0) {}
        check(errno == EAGAIN, "full pipe fixture");
        fcntl(pipefd[1], F_SETFL, flags);
        struct sigaction action = {0}, old_action;
        action.sa_handler = io_signal_handler;
        sigemptyset(&action.sa_mask);
        sigaction(SIGALRM, &action, &old_action);
        child = fork();
        if (child == 0) {
            close(pipefd[1]);
            usleep(50000);
            ssize_t n;
            do { n = read(pipefd[0], fill, sizeof(fill)); } while (n > 0);
            _exit(n == 0 ? 0 : 1);
        }
        close(pipefd[0]);
        if (child > 0) {
            sig_atomic_t initial = io_signals;
            struct itimerval timer = {{0, 1000}, {0, 1000}}, stopped = {0};
            setitimer(ITIMER_REAL, &timer, NULL);
            int64_t n = yian_io_write(pipefd[1], (const uint8_t *)"x", 1);
            setitimer(ITIMER_REAL, &stopped, NULL);
            check(n == 1 && io_signals > initial, "blocking write retries EINTR");
            close(pipefd[1]);
            waitpid(child, &status, 0);
            check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "EINTR reader child");
        } else { close(pipefd[1]); check(0, "EINTR reader creation"); }
        sigaction(SIGALRM, &old_action, NULL);
    } else { check(0, "EINTR write pipe fixture"); }
    check(yian_io_error_message(EACCES, path, sizeof(path)) > 0 &&
          yian_io_error_message(EACCES, path, 1) == -ERANGE,
          "operating system error message with bounded output");
    check(yian_io_error_kind(EACCES) == 2 && yian_io_error_kind(ENOTDIR) == 6 &&
          yian_io_error_kind(EPIPE) == 10 && yian_io_error_kind(ERANGE) == 11,
          "stable error kind mapping");
    check(yian_io_remove(link, 0) == 0 && yian_io_remove(file, 0) == 0 &&
          yian_io_remove(restricted, 0) == 0 && yian_io_remove(root, 1) == 0,
          "I/O fixture cleanup");
}

void __yian_main(void) {
    char buf[256];
    size_t len = 0;

    /* 全局对象初值 */
    check(__secl_lock_table[YIAN_LITERAL_LOCK_INDEX] == 0, "literal lock slot starts at key 0");
    check(__secl_lock_table[YIAN_ENV_LOCK_INDEX] == 0, "env lock slot starts at key 0");
    check(__yian_key_stack == 0, "stack key counter starts at 0");
    check(__secl_frame_lock_depth == 0, "frame lock depth starts at 0");
    check(__secl_lock_table[0] == 0 && __secl_lock_table[YIAN_FRAME_LOCK_SLOTS - 1] == 0,
          "frame lock slots are zero-initialized");
    check(__secl_lock_bump == YIAN_HEAP_LOCK_BASE, "heap lock cursor starts after the reserved slots");
    check(__secl_lock_free_head == 0, "lock free list starts empty");

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
    check_io();

    if (failures != 0) {
        printf("selftest: %d failure(s)\n", failures);
        exit(1); /* 走 exit 而不是 _exit: 让 stdout 缓冲写出失败明细 */
    }
    printf("selftest: ok\n");
}
