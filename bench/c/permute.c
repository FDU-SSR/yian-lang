#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

// 全局计数器
int count = 0;

// 交换数组元素（使用指针运算）
void swap_indices(int i, int j, int* v) {
    int* p = v + i;
    int* q = v + j;
    int tmp = *p;
    *p = *q;
    *q = tmp;
}

// 递归排列生成函数
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

// 性能测试函数
int benchmark_permute() {
    count = 0;
    int* v = (int*)malloc(sizeof(int) * 11);
    
    // 初始化数组（可选，但原代码没有初始化）
    for (int i = 0; i < 11; i++) {
        v[i] = i;
    }
    
    permute_inner(11, v);
    free(v);
    return count;
}

// 主函数
int main() {
    int result = benchmark_permute();
    int expected = 823059745;
    assert(result == expected && "permute count not expected");
    printf("Test passed! Permutation count: %d\n", result);
    return 0;
}