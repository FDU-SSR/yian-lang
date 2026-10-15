#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

static int64_t copy_root(const char *root, uint8_t *buffer, uint64_t capacity) {
    size_t length = strlen(root);
    if (length > capacity) return -ERANGE;
    memcpy(buffer, root, length);
    return (int64_t)length;
}

static int is_directory(const char *path) {
    struct stat metadata;
    return stat(path, &metadata) == 0 && S_ISDIR(metadata.st_mode);
}

static int has_install_resources(const char *root) {
    char path[PATH_MAX];
    int length = snprintf(path, sizeof(path), "%s/lib/src", root);
    if (length < 0 || (size_t)length >= sizeof(path) || !is_directory(path)) return 0;

    length = snprintf(path, sizeof(path), "%s/runtime/src", root);
    if (length < 0 || (size_t)length >= sizeof(path) || !is_directory(path)) return 0;

    length = snprintf(path, sizeof(path), "%s/runtime/include", root);
    return length >= 0 && (size_t)length < sizeof(path) && is_directory(path);
}

/* Installed binaries find their copied resources relative to PREFIX/bin. */
static int64_t installed_root(uint8_t *buffer, uint64_t capacity) {
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0) return -errno;
    if ((size_t)length >= sizeof(executable) - 1) return -ENAMETOOLONG;
    executable[length] = '\0';

    char *program_separator = strrchr(executable, '/');
    if (!program_separator) return -ENOENT;
    *program_separator = '\0';

    char *bin_separator = strrchr(executable, '/');
    if (!bin_separator || strcmp(bin_separator + 1, "bin")) return -ENOENT;
    if (bin_separator == executable) executable[1] = '\0';
    else *bin_separator = '\0';

    char root[PATH_MAX];
    int root_length = snprintf(root, sizeof(root), "%s/share/yian", executable);
    if (root_length < 0 || (size_t)root_length >= sizeof(root)) return -ENAMETOOLONG;
    if (!has_install_resources(root)) return -ENOENT;
    return copy_root(root, buffer, capacity);
}

/* The native source is compiled by absolute path in both build drivers. */
int64_t yian_compiler_root(uint8_t *buffer, uint64_t capacity) {
    const char *configured_root = getenv("YIAN_ROOT");
    if (configured_root && configured_root[0]) {
        return copy_root(configured_root, buffer, capacity);
    }

    int64_t installed = installed_root(buffer, capacity);
    if (installed >= 0 || installed == -ERANGE) return installed;

    static const char source[] = __FILE__;
    static const char suffix[] = "/bootstrap/yianc/native/host.c";
    size_t length = sizeof(source) - 1;
    size_t suffix_length = sizeof(suffix) - 1;
    if (length < suffix_length || strcmp(source + length - suffix_length, suffix)) return -EINVAL;
    length -= suffix_length;
    char root[PATH_MAX];
    if (length >= sizeof(root)) return -ERANGE;
    memcpy(root, source, length);
    root[length] = '\0';
    return copy_root(root, buffer, capacity);
}

uint64_t yian_compiler_monotonic_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static int32_t range_contains(uint32_t code, const uint32_t ranges[][2], size_t count) {
    size_t begin = 0, end = count;
    while (begin < end) {
        size_t middle = begin + (end - begin) / 2;
        if (code < ranges[middle][0]) end = middle;
        else if (code > ranges[middle][1]) begin = middle + 1;
        else return 1;
    }
    return 0;
}

/* Locale-independent Unicode classification keeps CLI library-name validation stable. */
int32_t yian_compiler_is_alphanumeric(uint32_t code) {
    if (code < 128) {
        return (code >= 'a' && code <= 'z') || (code >= 'A' && code <= 'Z') ||
               (code >= '0' && code <= '9');
    }
    static const uint32_t ranges[][2] = {
#include "unicode_alnum.inc"
    };
    return range_contains(code, ranges, sizeof(ranges) / sizeof(ranges[0]));
}

int32_t yian_compiler_is_printable(uint32_t code) {
    if (code < 128) return code >= 32 && code < 127;
    static const uint32_t ranges[][2] = {
#include "unicode_printable.inc"
    };
    return range_contains(code, ranges, sizeof(ranges) / sizeof(ranges[0]));
}
