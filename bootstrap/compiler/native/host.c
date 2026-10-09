#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

/* The native source is compiled by absolute path in both build drivers. */
int64_t yian_compiler_root(uint8_t *buffer, uint64_t capacity) {
    static const char source[] = __FILE__;
    static const char suffix[] = "/bootstrap/compiler/native/host.c";
    size_t length = sizeof(source) - 1;
    size_t suffix_length = sizeof(suffix) - 1;
    if (length < suffix_length || strcmp(source + length - suffix_length, suffix)) return -EINVAL;
    length -= suffix_length;
    if (length > capacity) return -ERANGE;
    memcpy(buffer, source, length);
    return (int64_t)length;
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
