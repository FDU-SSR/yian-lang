#ifndef YIAN_BENCH_GMP_SHIM_H
#define YIAN_BENCH_GMP_SHIM_H

#include <stdint.h>

typedef struct bg_gmp_int bg_gmp_int;

bg_gmp_int *bg_gmp_create(int64_t value);
void bg_gmp_destroy(bg_gmp_int *value);
void bg_gmp_add(bg_gmp_int *dst, const bg_gmp_int *left, const bg_gmp_int *right);
void bg_gmp_mul(bg_gmp_int *dst, const bg_gmp_int *left, const bg_gmp_int *right);
void bg_gmp_mul_si(bg_gmp_int *dst, const bg_gmp_int *value, int64_t factor);
void bg_gmp_tdiv_q(bg_gmp_int *dst, const bg_gmp_int *num, const bg_gmp_int *den);
int bg_gmp_cmp(const bg_gmp_int *left, const bg_gmp_int *right);
int64_t bg_gmp_get_si(const bg_gmp_int *value);

#endif
