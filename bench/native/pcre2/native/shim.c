#include "shim.h"

#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>

#include <stdlib.h>

_Static_assert(sizeof(void *) == 8, "PCRE2 benchmark binding requires 64-bit pointers");
_Static_assert(sizeof(size_t) == 8, "PCRE2 size_t must map to YIAN u64");
_Static_assert(sizeof(PCRE2_SIZE) == sizeof(size_t), "PCRE2_SIZE must match size_t");

struct bg_pcre_matcher {
    pcre2_match_context *context;
    pcre2_jit_stack *stack;
    pcre2_match_data *data;
};

struct bg_pcre_pattern {
    pcre2_code *code;
};

bg_pcre_matcher *bg_pcre_create_matcher(void) {
    bg_pcre_matcher *result = calloc(1, sizeof(*result));
    if (result == NULL) {
        return NULL;
    }

    result->context = pcre2_match_context_create(NULL);
    if (result->context == NULL) {
        bg_pcre_destroy_matcher(result);
        return NULL;
    }
    result->stack = pcre2_jit_stack_create(16384, 16384, NULL);
    if (result->stack == NULL) {
        bg_pcre_destroy_matcher(result);
        return NULL;
    }
    result->data = pcre2_match_data_create(16, NULL);
    if (result->data == NULL) {
        bg_pcre_destroy_matcher(result);
        return NULL;
    }
    pcre2_jit_stack_assign(result->context, NULL, result->stack);
    return result;
}

bg_pcre_pattern *bg_pcre_compile(const unsigned char *pattern, size_t length) {
    static const unsigned char empty_pattern[1] = {0};
    if (pattern == NULL && length != 0) {
        return NULL;
    }
    if (pattern == NULL) {
        pattern = empty_pattern;
    }

    int error_code = 0;
    PCRE2_SIZE error_offset = 0;
    pcre2_code *code = pcre2_compile(pattern, (PCRE2_SIZE)length, 0,
        &error_code, &error_offset, NULL);
    if (code == NULL) {
        return NULL;
    }
    if (pcre2_jit_compile(code, PCRE2_JIT_COMPLETE) != 0) {
        pcre2_code_free(code);
        return NULL;
    }

    bg_pcre_pattern *result = malloc(sizeof(*result));
    if (result == NULL) {
        pcre2_code_free(code);
        return NULL;
    }
    result->code = code;
    return result;
}

int bg_pcre_find(bg_pcre_matcher *matcher, bg_pcre_pattern *pattern,
    const unsigned char *subject, size_t length, size_t offset,
    size_t *start, size_t *end) {
    static const unsigned char empty_subject[1] = {0};
    if (matcher == NULL || matcher->context == NULL || matcher->data == NULL
        || pattern == NULL || pattern->code == NULL || offset > length
        || start == NULL || end == NULL || (subject == NULL && length != 0)) {
        return -1;
    }
    if (subject == NULL) {
        subject = empty_subject;
    }

    int result = pcre2_jit_match(pattern->code, subject, (PCRE2_SIZE)length,
        (PCRE2_SIZE)offset, 0, matcher->data, matcher->context);
    if (result == PCRE2_ERROR_NOMATCH) {
        return 0;
    }
    if (result < 0) {
        return -1;
    }

    PCRE2_SIZE *vector = pcre2_get_ovector_pointer(matcher->data);
    if (vector == NULL || vector[0] == PCRE2_UNSET || vector[1] == PCRE2_UNSET
        || vector[0] > vector[1] || vector[1] > (PCRE2_SIZE)length) {
        return -1;
    }
    *start = (size_t)vector[0];
    *end = (size_t)vector[1];
    return 1;
}

void bg_pcre_destroy_pattern(bg_pcre_pattern *pattern) {
    if (pattern == NULL) {
        return;
    }
    pcre2_code_free(pattern->code);
    free(pattern);
}

void bg_pcre_destroy_matcher(bg_pcre_matcher *matcher) {
    if (matcher == NULL) {
        return;
    }
    if (matcher->context != NULL) {
        pcre2_match_context_free(matcher->context);
    }
    if (matcher->stack != NULL) {
        pcre2_jit_stack_free(matcher->stack);
    }
    if (matcher->data != NULL) {
        pcre2_match_data_free(matcher->data);
    }
    free(matcher);
}
