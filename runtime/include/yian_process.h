#ifndef YIAN_PROCESS_H
#define YIAN_PROCESS_H
#include <stdint.h>

/* Captured buffers remain owned by the result until yian_process_drop. */
void *yian_process_execute(const char *program, char *const *arguments,
                           const char *directory, const uint8_t *stdin_bytes,
                           uint64_t stdin_length, int32_t stdin_mode,
                           int32_t capture_output, int32_t interruptible,
                           int32_t *error);
uint64_t yian_process_length(const void *result, int32_t stream);
int32_t yian_process_copy(const void *result, int32_t stream,
                         uint8_t *buffer, uint64_t capacity);
int32_t yian_process_status(const void *result, int32_t *signaled);
void yian_process_drop(void *result);
int64_t yian_process_env(const char *name, uint8_t *buffer, uint64_t capacity);
int64_t yian_process_temp_dir(const char *parent, uint8_t *buffer, uint64_t capacity);
#endif
