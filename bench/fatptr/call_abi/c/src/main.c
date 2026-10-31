// bench/fatptr/c/call_abi.c: authoritative C reference for call_abi.
//
// Same algorithm/scale as bench/fatptr/an/call_abi.an: repeatedly call two functions with
// 8 B raw pointer fields. Call count and start index derive from argc, matching YIAN's
// 32+argv.len(); fast passes scale in argv[1], so argc is two in both languages.
// Bodies match YIAN; C explicitly uses noinline because clang has a more permissive threshold,
// ensuring actual calls on both sides.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct {
    int64_t value;
} Node;

__attribute__((noinline)) static int64_t mix_in(const Node *p, uint64_t n) {
    int64_t acc = 0;
    for (uint64_t k = 0; k < 8; k++) {
        int64_t a = p[n + k].value;
        int64_t b = p[n + k + 8].value;
        int64_t c = p[n + k + 16].value;
        int64_t d = p[n + k + 24].value;
        acc += a * 3 + b * 5 - c * 7 + d * 11;
    }
    return acc;
}

__attribute__((noinline)) static int64_t mix_wide(const Node *p, const Node *view, uint64_t n) {
    int64_t acc = 0;
    for (uint64_t k = 0; k < 8; k++) {
        int64_t a = view[n + k].value;
        int64_t b = view[n + k + 8].value;
        int64_t c = view[n + k + 16].value;
        int64_t d = view[n + k + 24].value;
        acc += a * 3 + b * 5 - c * 7 + d * 11;
    }
    return acc + p[n].value;
}

int main(int argc, char **argv) {
    const uint64_t calls = argc > 1 ? (uint64_t)atoll(argv[1]) : 4096;
    const uint64_t rounds = 64;
    const uint64_t base = 32 + (uint64_t)argc;
    const uint64_t size = base + calls + 32;
    Node *p = malloc((size_t)size * sizeof(Node));
    assert(p != NULL);
    for (uint64_t i = 0; i < size; i++) {
        p[i].value = (int64_t)i;
    }
    int64_t acc = 0;
    for (uint64_t round = 0; round < rounds; round++) {
        for (uint64_t i = 0; i < calls; i++) {
            uint64_t n = base + i;
            acc += mix_in(p, n) + mix_wide(p, p, n);
        }
    }
    const int64_t sum_n = (int64_t)calls * (int64_t)base + (int64_t)calls * ((int64_t)calls - 1) / 2;
    const int64_t per_round = 193 * sum_n + 3744 * (int64_t)calls;
    assert(acc == (int64_t)rounds * per_round);
    printf("call_abi pass\n");
    free(p);
    return 0;
}
