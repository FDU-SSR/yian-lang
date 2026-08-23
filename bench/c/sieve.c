#include <stdio.h>
#include <assert.h>

// 埃拉托斯特尼筛法实现
int sieve(char* flags, int size) {
    int prime_count = 0;
    for (int i = 2; i < size; i++) {
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
int main() {
    int iter = 500;
    int SIEVE_SIZE = 2000000;
    char flags[SIEVE_SIZE];
    for (int iter = 0; iter < 500; iter++) {
        for (int i = 0; i < SIEVE_SIZE; i++) {
            flags[i] = 1; // 1表示true
        }
        int prime_count = sieve(flags, SIEVE_SIZE);
        assert(prime_count == 148933 && "prime_count is not correct");
    }
    printf("Test passed\n");
    return 0;
}