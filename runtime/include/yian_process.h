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

/* A long-lived child owns its parent-side pipe descriptors and wait state. */
void *yian_child_spawn(const char *program, char *const *arguments,
                       const char *directory, const int32_t stdio_modes[3],
                       int32_t *error);
int32_t yian_child_fd(void *child, int32_t stream);
int32_t yian_child_close_pipe(void *child, int32_t stream);
int64_t yian_child_read(void *child, int32_t stream, uint8_t *buffer, uint64_t length);
int64_t yian_child_write(void *child, int32_t stream, const uint8_t *buffer, uint64_t length);
int32_t yian_child_try_wait(void *child, int32_t *status, int32_t *signaled);
int32_t yian_child_wait(void *child, int32_t *status, int32_t *signaled);
int32_t yian_child_signal(void *child, int32_t signal_number);
int32_t yian_child_close(void *child);
void yian_child_drop(void *child);

/* poll() input interests are read=1/write=2; output flags add HUP=4/ERR=8. */
int64_t yian_poller_wait(const int32_t *descriptors, const uint32_t *interests,
                         uint64_t count, int64_t timeout_milliseconds,
                         uint32_t *ready);
int64_t yian_monotonic_nanos(void);
int64_t yian_process_executable_path(uint8_t *buffer, uint64_t capacity);
#endif
