#include <stdio.h>
#include <stdlib.h>
#include <assert.h>


// Struct definitions.
typedef struct array_tree {
    struct array_tree* children;
    int child_count;
} array_tree;

typedef struct Random {
    unsigned long long stage;
} Random;

// Global variables.
int count_arrays = 0;

// Initialize the random-number generator.
void Random_init(Random* r, unsigned long long seed) {
    r->stage = seed;
}

// Generate the next random value, matching official SOM Random.
unsigned long long Random_next(Random* r) {
    r->stage = (r->stage * 1309ULL + 13849ULL) & 65535ULL;
    return r->stage;
}

// Recursively build the tree.
array_tree* build_tree_depth(int depth, Random* random, int* out_count) {
    count_arrays++;

    if (depth == 1) {
        int n = Random_next(random) % 10 + 1;
        array_tree* arr = (array_tree*)malloc(n * sizeof(array_tree));

        for (int i = 0; i < n; i++) {
            arr[i].children = NULL;
            arr[i].child_count = 0;
        }
        *out_count = n;
        return arr;
    }

    int n = 4;
    array_tree* arr = (array_tree*)malloc(n * sizeof(array_tree));
    for (int i = 0; i < n; i++) {
        int child_count = 0;
        array_tree* child = build_tree_depth(depth - 1, random, &child_count);
        arr[i].children = child;
        arr[i].child_count = child_count;
    }
    *out_count = n;
    return arr;
}

// Release tree memory.
void free_tree(array_tree* arr, int count) {
    if (arr == NULL) {
        return;
    }
    for (int i = 0; i < count; i++) {
        if (arr[i].children != NULL && arr[i].child_count > 0) {
            free_tree(arr[i].children, arr[i].child_count);
        }
    }
    free(arr);
}

// Benchmark function.
int benchmark_storage(int root_depth) {
    Random random;
    Random_init(&random, 74755);

    count_arrays = 0;
    int root_count = 0;
    array_tree* result = build_tree_depth(root_depth, &random, &root_count);

    free_tree(result, root_count);

    return (int)count_arrays;
}

// main function.
int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 9885;
    for (int i = 0; i < iterations; i++) {
        int result = benchmark_storage(7);
        assert(result == 5461 && "storage count not expected");
    }
    return 0;
}
