#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

// 埃拉托斯特尼筛法实现
int sieve(char* flags, int size) {
    int prime_count = 0;
    for (int i = 2; i <= size; i++) {
        if (flags[i - 1]) {
            prime_count = prime_count + 1;
            int k = i + i;
            while (k <= size) {
                flags[k - 1] = 0;
                k += i;
            }
        }
    }
    return prime_count;
}

// 主函数
int main(int argc, char **argv) {
    // argv[1] 只控制完整工作单元的外层重复次数。
    int iter_count = argc > 1 ? atoi(argv[1]) : 268657;
    int SIEVE_SIZE = 5000;
    for (int iter = 0; iter < iter_count; iter++) {
        char *flags = (char *)malloc((size_t)SIEVE_SIZE);
        assert(flags != NULL);
        for (int i = 0; i < SIEVE_SIZE; i++) {
            flags[i] = 1; // 1表示true
        }
        int prime_count = sieve(flags, SIEVE_SIZE);
        assert(prime_count == 669 && "prime_count is not correct");
        free(flags);
    }
    return 0;
}
