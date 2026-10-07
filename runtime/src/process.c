#define _GNU_SOURCE
#include "yian_process.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

struct captured_bytes {
    uint8_t *data;
    size_t length;
    size_t capacity;
};

struct process_output {
    struct captured_bytes streams[2];
    int status;
};

void yian_process_drop(void *opaque) {
    struct process_output *result = opaque;
    if (result == NULL) return;
    free(result->streams[0].data);
    free(result->streams[1].data);
    free(result);
}

/* Internal descriptors stay above stdio so spawn dup/close actions cannot alias their targets. */
static int above_stdio(int fd) {
    if (fd >= 3) return fd;
    int replacement = fcntl(fd, F_DUPFD_CLOEXEC, 3);
    int error = errno;
    close(fd);
    if (replacement < 0) errno = error;
    return replacement;
}

static int capture_pipe(int fds[2]) {
    if (pipe2(fds, O_CLOEXEC) < 0) return errno;
    fds[0] = above_stdio(fds[0]);
    if (fds[0] < 0) {
        int error = errno;
        close(fds[1]);
        fds[1] = -1;
        return error;
    }
    fds[1] = above_stdio(fds[1]);
    if (fds[1] < 0) {
        int error = errno;
        close(fds[0]);
        fds[0] = -1;
        return error;
    }
    return 0;
}

static int append_bytes(struct captured_bytes *bytes, const uint8_t *data, size_t length) {
    if (length > SIZE_MAX - bytes->length) return EOVERFLOW;
    size_t needed = bytes->length + length;
    if (needed > bytes->capacity) {
        size_t capacity = bytes->capacity ? bytes->capacity : 8192;
        while (capacity < needed) {
            if (capacity > SIZE_MAX / 2) {
                capacity = needed;
                break;
            }
            capacity *= 2;
        }
        uint8_t *replacement = realloc(bytes->data, capacity);
        if (replacement == NULL) return ENOMEM;
        bytes->data = replacement;
        bytes->capacity = capacity;
    }
    memcpy(bytes->data + bytes->length, data, length);
    bytes->length = needed;
    return 0;
}

/* Drain both streams before waiting; a child can fill either pipe independently. */
static int collect_output(struct process_output *result, int out_fd, int err_fd) {
    struct pollfd pipes[2] = {{out_fd, POLLIN, 0}, {err_fd, POLLIN, 0}};
    int remaining = 2;
    uint8_t buffer[8192];
    while (remaining != 0) {
        int ready;
        do { ready = poll(pipes, 2, -1); } while (ready < 0 && errno == EINTR);
        if (ready < 0) return errno;
        for (int index = 0; index < 2; ++index) {
            if (pipes[index].fd < 0 || pipes[index].revents == 0) continue;
            if (pipes[index].revents & POLLNVAL) return EBADF;
            ssize_t count;
            do { count = read(pipes[index].fd, buffer, sizeof(buffer)); }
            while (count < 0 && errno == EINTR);
            if (count < 0) return errno;
            if (count == 0) {
                pipes[index].fd = -1;
                --remaining;
            } else {
                int error = append_bytes(&result->streams[index], buffer, (size_t)count);
                if (error != 0) return error;
            }
        }
    }
    return 0;
}

void *yian_process_output(const char *program, char *const *arguments,
                          const char *directory, int32_t *reported_error) {
    int pipes[2][2] = {{-1, -1}, {-1, -1}};
    int input = -1;
    int error = 0;
    int actions_initialized = 0;
    pid_t child = -1;
    posix_spawn_file_actions_t actions;
    struct process_output *result = calloc(1, sizeof(*result));
    if (result == NULL) { error = ENOMEM; goto done; }
    error = capture_pipe(pipes[0]);
    if (error != 0) goto done;
    error = capture_pipe(pipes[1]);
    if (error != 0) goto done;
    input = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (input < 0) { error = errno; goto done; }
    input = above_stdio(input);
    if (input < 0) { error = errno; goto done; }
    error = posix_spawn_file_actions_init(&actions);
    if (error != 0) goto done;
    actions_initialized = 1;
    error = posix_spawn_file_actions_adddup2(&actions, input, STDIN_FILENO);
    if (error != 0) goto done;
    error = posix_spawn_file_actions_adddup2(&actions, pipes[0][1], STDOUT_FILENO);
    if (error != 0) goto done;
    error = posix_spawn_file_actions_adddup2(&actions, pipes[1][1], STDERR_FILENO);
    if (error != 0) goto done;
    if (directory != NULL) {
        error = posix_spawn_file_actions_addchdir_np(&actions, directory);
        if (error != 0) goto done;
    }
    error = posix_spawnp(&child, program, &actions, NULL, arguments, environ);
    if (error != 0) { child = -1; goto done; }
    close(pipes[0][1]); pipes[0][1] = -1;
    close(pipes[1][1]); pipes[1][1] = -1;
    error = collect_output(result, pipes[0][0], pipes[1][0]);
    if (error != 0) kill(child, SIGKILL);
    pid_t waited;
    do { waited = waitpid(child, &result->status, 0); }
    while (waited < 0 && errno == EINTR);
    if (waited < 0 && error == 0) error = errno;
    child = -1;

done:
    if (child > 0) {
        kill(child, SIGKILL);
        while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {}
    }
    if (actions_initialized) posix_spawn_file_actions_destroy(&actions);
    if (input >= 0) close(input);
    for (int stream = 0; stream < 2; ++stream) {
        for (int end = 0; end < 2; ++end) {
            if (pipes[stream][end] >= 0) close(pipes[stream][end]);
        }
    }
    *reported_error = -error;
    if (error != 0) { yian_process_drop(result); return NULL; }
    return result;
}

uint64_t yian_process_length(const void *opaque, int32_t stream) {
    const struct process_output *result = opaque;
    return stream >= 0 && stream < 2 ? result->streams[stream].length : 0;
}

int32_t yian_process_copy(const void *opaque, int32_t stream,
                         uint8_t *buffer, uint64_t capacity) {
    if (stream < 0 || stream >= 2) return -EINVAL;
    const struct process_output *result = opaque;
    const struct captured_bytes *bytes = &result->streams[stream];
    if (capacity < bytes->length) return -ERANGE;
    if (bytes->length != 0) memcpy(buffer, bytes->data, bytes->length);
    return 0;
}

int32_t yian_process_status(const void *opaque, int32_t *signaled) {
    const struct process_output *result = opaque;
    *signaled = WIFSIGNALED(result->status);
    return *signaled ? WTERMSIG(result->status) : WEXITSTATUS(result->status);
}

/* An owned YIAN copy is made before any caller can mutate its process environment. */
int64_t yian_process_env(const char *name, uint8_t *buffer, uint64_t capacity) {
    const char *value = getenv(name);
    if (value == NULL) return -ENOENT;
    size_t length = strlen(value);
    if (length > capacity || length > INT64_MAX) return -ERANGE;
    if (length != 0) memcpy(buffer, value, length);
    return (int64_t)length;
}

int64_t yian_process_temp_dir(const char *parent, uint8_t *buffer, uint64_t capacity) {
    const char suffix[] = "/yian-native-XXXXXX";
    size_t length = strlen(parent);
    if (length > SIZE_MAX - sizeof(suffix) || length + sizeof(suffix) > capacity) return -ERANGE;
    char *path = malloc(length + sizeof(suffix));
    if (path == NULL) return -ENOMEM;
    memcpy(path, parent, length);
    memcpy(path + length, suffix, sizeof(suffix));
    if (mkdtemp(path) == NULL) {
        int error = errno;
        free(path);
        return -error;
    }
    size_t total = length + sizeof(suffix) - 1;
    memcpy(buffer, path, total);
    free(path);
    return (int64_t)total;
}
