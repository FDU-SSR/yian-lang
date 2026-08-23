#include <stdio.h>
#include <stdlib.h>
#include <assert.h>


// 常量定义
#define LCG_A 1103515245
#define LCG_C 12345
// 定义结构体
typedef struct array_tree {
    struct array_tree* children;
    int child_count;
} array_tree;

typedef struct Random {
    unsigned int stage;
} Random;

// 全局变量
int count_arrays = 0;

// 初始化随机数生成器
void Random_init(Random* r, unsigned int seed) {
    r->stage = seed;
}

// 生成下一个随机数
unsigned int Random_next(Random* r) {
    r->stage = (unsigned int)(r->stage * LCG_A + LCG_C);
    return r->stage;
}

// 递归构建树
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

    int n = 3;
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

// 释放树的内存
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

// 基准测试函数
int benchmark_storage(int root_depth) {
    Random random;
    Random_init(&random, 12345);

    count_arrays = 0;
    int root_count = 0;
    array_tree* result = build_tree_depth(root_depth, &random, &root_count);

    free_tree(result, root_count);

    return (int)count_arrays;
}

// 主函数
int main() {
    int root_depth = 16;
    int result = benchmark_storage(root_depth);
    printf("Storage count: %d\n", result);
    int expected = 21523360;
    assert(result == expected && "storage count not expected");
    printf("Test passed!\n");
    return 0;
}