#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

// Sieve of Eratosthenes.
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

// main function.
int main(int argc, char **argv) {
    // argv[1] controls only outer repetitions of complete work units.
    int iter_count = argc > 1 ? atoi(argv[1]) : 268657;
    int SIEVE_SIZE = 5000;
    for (int iter = 0; iter < iter_count; iter++) {
        char *flags = (char *)malloc((size_t)SIEVE_SIZE);
        assert(flags != NULL);
        for (int i = 0; i < SIEVE_SIZE; i++) {
            flags[i] = 1; // 1 represents true.
        }
        int prime_count = sieve(flags, SIEVE_SIZE);
        assert(prime_count == 669 && "prime_count is not correct");
        free(flags);
    }
    return 0;
}
