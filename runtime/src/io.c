#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include "yian_io.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int32_t yian_io_error_kind(int32_t e) {
    switch (e) {
    case ENOENT: return 1;
    case EACCES: case EPERM: return 2;
    case EEXIST: return 3;
    case EINVAL: case EBADF: case ENAMETOOLONG: case EOVERFLOW: return 4;
    case EINTR: return 5;
    case ENOTDIR: return 6;
    case EISDIR: return 7;
    case ENOTEMPTY: return 8;
    case EAGAIN: return 9;
    case EPIPE: return 10;
    case ERANGE: return 11;
    default: return 0;
    }
}

int64_t yian_io_error_message(int32_t error, uint8_t *buffer, uint64_t capacity) {
    char local[256];
    const char *message = strerror_r(error, local, sizeof(local));
    size_t length = strlen(message);
    if (length > capacity) return -ERANGE;
    memcpy(buffer, message, length);
    return (int64_t)length;
}

int64_t yian_io_open(const char *path, uint32_t o) {
    if (!(o & 3u) || (o & ~63u) || ((o & 60u) && !(o & 2u)) ||
        ((o & 4u) && (!(o & 2u) || (o & 16u)))) return -EINVAL;
    int flags = (o & 3u) == 3u ? O_RDWR : (o & 2u) ? O_WRONLY : O_RDONLY;
    if (o & 4u) flags |= O_APPEND;
    if (o & 8u) flags |= O_CREAT;
    if (o & 16u) flags |= O_TRUNC;
    if (o & 32u) flags |= O_CREAT | O_EXCL;
    int fd;
    do { fd = open(path, flags | O_CLOEXEC, 0666); } while (fd < 0 && errno == EINTR);
    return fd < 0 ? -errno : fd;
}

int64_t yian_io_read(int32_t fd, uint8_t *b, uint64_t n) {
    if (n == 0) return 0;
    if (n > 0x7ffff000u) n = 0x7ffff000u;
    ssize_t r;
    do { r = read(fd, b, (size_t)n); } while (r < 0 && errno == EINTR);
    return r < 0 ? -errno : r;
}

int64_t yian_io_write(int32_t fd, const uint8_t *b, uint64_t n) {
    if (n == 0) return 0;
    if (n > 0x7ffff000u) n = 0x7ffff000u;
    /* 仅阻塞当前线程的 SIGPIPE，保留调用者已有的待决信号与掩码。 */
    sigset_t block, old, pending;
    sigemptyset(&block);
    sigaddset(&block, SIGPIPE);
    int mask_error = pthread_sigmask(SIG_BLOCK, &block, &old);
    if (mask_error) return -mask_error;
    sigpending(&pending);
    int existed = sigismember(&pending, SIGPIPE);
    ssize_t r;
    do { r = write(fd, b, (size_t)n); } while (r < 0 && errno == EINTR);
    int error = r < 0 ? errno : 0;
    if (error == EPIPE && !existed) {
        struct timespec timeout = {0, 0};
        while (sigtimedwait(&block, NULL, &timeout) < 0 && errno == EINTR) {}
    }
    pthread_sigmask(SIG_SETMASK, &old, NULL);
    return error ? -error : r;
}

int32_t yian_io_close(int32_t fd) {
    return close(fd) < 0 ? -errno : 0;
}
int64_t yian_io_seek(int32_t fd, int64_t offset, int32_t origin) {
    if (origin < 0 || origin > 2) return -EINVAL;
    int whence = origin == 0 ? SEEK_SET : origin == 1 ? SEEK_CUR : SEEK_END;
    off_t r = lseek(fd, (off_t)offset, whence);
    return r < 0 ? -errno : (int64_t)r;
}
int32_t yian_io_set_len(int32_t fd, uint64_t length) {
    if (length > INT64_MAX) return -EOVERFLOW;
    int r;
    do { r = ftruncate(fd, (off_t)length); } while (r < 0 && errno == EINTR);
    return r < 0 ? -errno : 0;
}
int32_t yian_io_sync(int32_t fd) {
    int r;
    do { r = fsync(fd); } while (r < 0 && errno == EINTR);
    return r < 0 ? -errno : 0;
}
static void metadata_fields(const struct stat *s, uint64_t *out) {
    out[0] = S_ISREG(s->st_mode) ? 1 : S_ISDIR(s->st_mode) ? 2 : S_ISLNK(s->st_mode) ? 3 : 4;
    out[1] = (uint64_t)s->st_size;
    out[2] = (uint64_t)s->st_mtim.tv_sec;
    out[3] = (uint64_t)s->st_mtim.tv_nsec;
}
int32_t yian_io_metadata(const char *path, int32_t follow, uint64_t *out) {
    struct stat s;
    int r = follow ? stat(path, &s) : lstat(path, &s);
    if (r < 0) return -errno;
    metadata_fields(&s, out);
    return 0;
}
int32_t yian_io_fmetadata(int32_t fd, uint64_t *out) {
    struct stat s;
    if (fstat(fd, &s) < 0) return -errno;
    metadata_fields(&s, out);
    return 0;
}
int32_t yian_io_mkdir(const char *path) { return mkdir(path, 0777) < 0 ? -errno : 0; }
int32_t yian_io_remove(const char *path, int32_t directory) {
    int r = directory ? rmdir(path) : unlink(path);
    return r < 0 ? -errno : 0;
}
int32_t yian_io_rename(const char *from, const char *to) {
    return rename(from, to) < 0 ? -errno : 0;
}
int64_t yian_io_current_dir(uint8_t *buffer, uint64_t capacity) {
    if (capacity > SIZE_MAX || capacity == 0) return -EINVAL;
    if (!getcwd((char *)buffer, (size_t)capacity)) return -errno;
    return (int64_t)strlen((char *)buffer);
}
int64_t yian_io_canonicalize(const char *path, uint8_t *buffer, uint64_t capacity) {
    char *resolved = realpath(path, NULL);
    if (!resolved) return -errno;
    size_t n = strlen(resolved);
    if (n > capacity) { free(resolved); return -ERANGE; }
    memcpy(buffer, resolved, n);
    free(resolved);
    return (int64_t)n;
}
void *yian_io_dir_open(const char *path, int32_t *error) {
    DIR *d = opendir(path);
    *error = d ? 0 : errno;
    return d;
}
int64_t yian_io_dir_next(void *directory, uint8_t *buffer, uint64_t capacity) {
    for (;;) {
        errno = 0;
        struct dirent *e = readdir((DIR *)directory);
        if (!e) return errno ? -errno : 0;
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        size_t n = strlen(e->d_name);
        if (n > capacity) return -ENAMETOOLONG;
        memcpy(buffer, e->d_name, n);
        return (int64_t)n;
    }
}
int32_t yian_io_dir_close(void *directory) {
    return closedir((DIR *)directory) < 0 ? -errno : 0;
}
