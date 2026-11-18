#ifndef YIAN_BENCH_PCRE2_SHIM_H
#define YIAN_BENCH_PCRE2_SHIM_H

#include <stddef.h>

typedef struct bg_pcre_matcher bg_pcre_matcher;
typedef struct bg_pcre_pattern bg_pcre_pattern;

bg_pcre_matcher *bg_pcre_create_matcher(void);
bg_pcre_pattern *bg_pcre_compile(const unsigned char *pattern, size_t length);
int bg_pcre_find(bg_pcre_matcher *matcher, bg_pcre_pattern *pattern,
    const unsigned char *subject, size_t length, size_t offset,
    size_t *start, size_t *end);
void bg_pcre_destroy_pattern(bg_pcre_pattern *pattern);
void bg_pcre_destroy_matcher(bg_pcre_matcher *matcher);

#endif
