#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

// Global counter.
int count = 0;

// Swap array elements using pointer arithmetic.
void swap_indices(int i, int j, int* v) {
    int* p = v + i;
    int* q = v + j;
    int tmp = *p;
    *p = *q;
    *q = tmp;
}

// Recursive permutation generator.
void permute_inner(int n, int* v) {
    count += 1;
    if (n != 0) {
        int n1 = n - 1;
        permute_inner(n1, v);
        for (int i = n1; i >= 0; i -= 1) {
            swap_indices(n1, i, v);
            permute_inner(n1, v);
            swap_indices(n1, i, v);
        }
    }
}

// Benchmark function.
int benchmark_permute() {
    count = 0;
    int* v = (int*)calloc(6, sizeof(int));

    permute_inner(6, v);
    free(v);
    return count;
}

// main function.
int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 70866;
    for (int i = 0; i < iterations; i++) {
        int result = benchmark_permute();
        assert(result == 8660 && "permute count not expected");
    }
    return 0;
}
