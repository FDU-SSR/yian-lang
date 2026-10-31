#include "shim.h"

#include <gmp.h>
#include <stdlib.h>

_Static_assert(sizeof(void *) == 8, "GMP benchmark binding requires a 64-bit pointer ABI");
_Static_assert(sizeof(long) == 8, "GMP benchmark binding requires LP64 long");
_Static_assert(sizeof(int64_t) == sizeof(long), "GMP signed integer ABI must match long");

struct bg_gmp_int {
    mpz_t value;
};

bg_gmp_int *bg_gmp_create(int64_t value) {
    bg_gmp_int *result = malloc(sizeof(*result));
    if (result == NULL) {
        return NULL;
    }
    mpz_init_set_si(result->value, (long)value);
    return result;
}

void bg_gmp_destroy(bg_gmp_int *value) {
    if (value == NULL) {
        return;
    }
    mpz_clear(value->value);
    free(value);
}

void bg_gmp_add(bg_gmp_int *dst, const bg_gmp_int *left, const bg_gmp_int *right) {
    mpz_add(dst->value, left->value, right->value);
}

void bg_gmp_mul(bg_gmp_int *dst, const bg_gmp_int *left, const bg_gmp_int *right) {
    mpz_mul(dst->value, left->value, right->value);
}

void bg_gmp_mul_si(bg_gmp_int *dst, const bg_gmp_int *value, int64_t factor) {
    mpz_mul_si(dst->value, value->value, (long)factor);
}

void bg_gmp_tdiv_q(bg_gmp_int *dst, const bg_gmp_int *num, const bg_gmp_int *den) {
    mpz_tdiv_q(dst->value, num->value, den->value);
}

int bg_gmp_cmp(const bg_gmp_int *left, const bg_gmp_int *right) {
    return mpz_cmp(left->value, right->value);
}

int64_t bg_gmp_get_si(const bg_gmp_int *value) {
    return (int64_t)mpz_get_si(value->value);
}
