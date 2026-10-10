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
    /* Block SIGPIPE only in the current thread, preserving the caller's pending signals and mask. */
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

/* The caller owns the guard and must restore the exact flags before releasing it. */
int32_t yian_io_nonblocking_begin(int32_t fd, int32_t *original_flags) {
    if (original_flags == NULL) return -EINVAL;
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0) return -errno;
    if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) return -errno;
    *original_flags = flags;
    return 0;
}

int32_t yian_io_nonblocking_end(int32_t fd, int32_t original_flags) {
    if (fcntl(fd, F_SETFL, original_flags) < 0) return -errno;
    return 0;
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

static char *join_path(const char *base, const char *tail) {
    size_t base_length = strlen(base);
    size_t tail_length = strlen(tail);
    int separator = base_length != 0 && base[base_length - 1] != '/';
    if (base_length > SIZE_MAX - tail_length - (size_t)separator - 1) {
        errno = ENAMETOOLONG;
        return NULL;
    }
    char *joined = malloc(base_length + (size_t)separator + tail_length + 1);
    if (joined == NULL) { errno = ENOMEM; return NULL; }
    memcpy(joined, base, base_length);
    size_t offset = base_length;
    if (separator) joined[offset++] = '/';
    memcpy(joined + offset, tail, tail_length + 1);
    return joined;
}

static char *append_component(const char *base, const char *component) {
    if (!strcmp(component, ".")) return strdup(base);
    if (!strcmp(component, "..")) {
        char *parent = strdup(base);
        if (parent == NULL) { errno = ENOMEM; return NULL; }
        size_t length = strlen(parent);
        while (length > 1 && parent[length - 1] == '/') parent[--length] = '\0';
        while (length > 1 && parent[length - 1] != '/') parent[--length] = '\0';
        while (length > 1 && parent[length - 1] == '/') parent[--length] = '\0';
        if (length == 1) parent[1] = '\0';
        return parent;
    }
    return join_path(base, component);
}

static char *resolve_path_allow_missing(const char *path, unsigned depth, int *error) {
    if (depth > 40) { *error = ELOOP; return NULL; }
    char *absolute = NULL;
    if (path[0] == '/') {
        absolute = strdup(path);
    } else {
        char *cwd = getcwd(NULL, 0);
        if (cwd == NULL) { *error = errno; return NULL; }
        absolute = join_path(cwd, path);
        free(cwd);
    }
    if (absolute == NULL) { *error = errno; return NULL; }

    char *current = strdup("/");
    if (current == NULL) { free(absolute); *error = ENOMEM; return NULL; }
    int missing = 0;
    char *cursor = absolute;
    while (*cursor != '\0') {
        while (*cursor == '/') ++cursor;
        if (*cursor == '\0') break;
        char *start = cursor;
        while (*cursor != '\0' && *cursor != '/') ++cursor;
        char saved = *cursor;
        *cursor = '\0';
        char *rest = saved == '\0' ? cursor : cursor + 1;

        if (!strcmp(start, ".")) {
            *cursor = saved;
            if (saved != '\0') cursor = rest;
            continue;
        }
        if (!strcmp(start, "..")) {
            char *parent = append_component(current, start);
            if (parent == NULL) { *error = errno; free(current); free(absolute); return NULL; }
            free(current);
            current = parent;
            if (missing) {
                char *resolved = realpath(current, NULL);
                if (resolved != NULL) { free(current); current = resolved; missing = 0; }
                else if (errno != ENOENT) { *error = errno; free(current); free(absolute); return NULL; }
            }
            *cursor = saved;
            if (saved != '\0') cursor = rest;
            continue;
        }

        if (missing) {
            char *next = append_component(current, start);
            if (next == NULL) { *error = errno; free(current); free(absolute); return NULL; }
            free(current);
            current = next;
        } else {
            char *candidate = join_path(current, start);
            if (candidate == NULL) { *error = errno; free(current); free(absolute); return NULL; }
            char *resolved = realpath(candidate, NULL);
            if (resolved != NULL) {
                free(current);
                current = resolved;
            } else {
                int path_error = errno;
                struct stat info;
                int stat_status = lstat(candidate, &info);
                if (stat_status == 0 && S_ISLNK(info.st_mode)) {
                    char target[PATH_MAX + 1];
                    ssize_t target_length = readlink(candidate, target, PATH_MAX);
                    if (target_length < 0) {
                        *error = errno; free(candidate); free(current); free(absolute); return NULL;
                    }
                    target[target_length] = '\0';
                    char *target_path = target[0] == '/' ? strdup(target) : join_path(current, target);
                    if (target_path == NULL) {
                        *error = errno; free(candidate); free(current); free(absolute); return NULL;
                    }
                    char *combined = join_path(target_path, rest);
                    free(target_path);
                    free(candidate);
                    free(current);
                    free(absolute);
                    if (combined == NULL) { *error = errno; return NULL; }
                    char *resolved_target = resolve_path_allow_missing(combined, depth + 1, error);
                    free(combined);
                    return resolved_target;
                }
                int stat_error = errno;
                free(candidate);
                if (path_error != ENOENT || stat_status == 0 || stat_error != ENOENT) {
                    *error = path_error != ENOENT ? path_error : stat_error;
                    free(current); free(absolute); return NULL;
                }
                char *next = append_component(current, start);
                if (next == NULL) { *error = errno; free(current); free(absolute); return NULL; }
                free(current);
                current = next;
                missing = 1;
            }
        }

        *cursor = saved;
        if (saved != '\0') cursor = rest;
    }
    free(absolute);
    return current;
}

int64_t yian_io_resolve(const char *path, uint8_t *buffer, uint64_t capacity) {
    int error = 0;
    char *resolved = resolve_path_allow_missing(path, 0, &error);
    if (resolved == NULL) return -error;
    size_t length = strlen(resolved);
    if (length > capacity || length > INT64_MAX) { free(resolved); return -ERANGE; }
    memcpy(buffer, resolved, length);
    free(resolved);
    return (int64_t)length;
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
