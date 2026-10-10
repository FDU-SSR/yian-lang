#define _GNU_SOURCE
#include "yian_process.h"
#include "yian_io.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
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

static int input_pipe(int fds[2]) {
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
    int flags = fcntl(fds[1], F_GETFL);
    if (flags < 0 || fcntl(fds[1], F_SETFL, flags | O_NONBLOCK) < 0) {
        int error = errno;
        close(fds[0]);
        close(fds[1]);
        fds[0] = fds[1] = -1;
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

static volatile sig_atomic_t process_interrupted;

static void record_interrupt(int signal_number) {
    (void)signal_number;
    process_interrupted = 1;
}

static int64_t monotonic_millis(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* Write stdin and drain both output streams together to avoid pipe backpressure deadlocks. */
static int collect_process(struct process_output *result, int output_fds[2],
                           int *input_fd, const uint8_t *input, size_t input_length,
                           pid_t *child, int interruptible, int *was_interrupted) {
    size_t input_offset = 0;
    int reaped = 0;
    int wait_status = 0;
    int interrupt_sent = 0;
    int64_t escalation_deadline = 0;
    uint8_t buffer[8192];
    while (output_fds[0] >= 0 || output_fds[1] >= 0 || *input_fd >= 0 || !reaped) {
        if (interruptible && process_interrupted && !interrupt_sent) {
            interrupt_sent = 1;
            *was_interrupted = 1;
            if (!reaped) {
                kill(*child, SIGINT);
                int64_t now = monotonic_millis();
                escalation_deadline = now < 0 ? 1 : now + 250;
            }
        }

        if (!reaped) {
            pid_t waited = waitpid(*child, &wait_status, WNOHANG);
            if (waited == *child) {
                *child = -1;
                reaped = 1;
            } else if (waited < 0 && errno != EINTR) {
                return errno;
            }
        }

        if (interrupt_sent && !reaped && escalation_deadline != 0) {
            int64_t now = monotonic_millis();
            if (now >= escalation_deadline) {
                kill(*child, SIGKILL);
                escalation_deadline = 0;
            }
        }

        if (reaped && output_fds[0] < 0 && output_fds[1] < 0 && *input_fd < 0) break;

        struct pollfd pipes[3] = {
            {output_fds[0], POLLIN, 0},
            {output_fds[1], POLLIN, 0},
            {*input_fd, POLLOUT, 0},
        };
        int no_descriptors = output_fds[0] < 0 && output_fds[1] < 0 && *input_fd < 0;
        int timeout = interruptible || no_descriptors ? 50 : -1;
        if (interrupt_sent && escalation_deadline != 0) {
            int64_t remaining = escalation_deadline - monotonic_millis();
            if (remaining < timeout || timeout < 0) timeout = remaining > 0 ? (int)remaining : 0;
        }
        int ready;
        ready = poll(pipes, 3, timeout);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) return errno;
        for (int index = 0; index < 2; ++index) {
            if (pipes[index].fd < 0 || pipes[index].revents == 0) continue;
            if (pipes[index].revents & POLLNVAL) return EBADF;
            ssize_t count;
            do { count = read(pipes[index].fd, buffer, sizeof(buffer)); } while (count < 0 && errno == EINTR && !process_interrupted);
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            if (count < 0) return errno;
            if (count == 0) {
                close(output_fds[index]);
                output_fds[index] = -1;
            } else {
                int error = append_bytes(&result->streams[index], buffer, (size_t)count);
                if (error != 0) return error;
            }
        }

        if (*input_fd >= 0 && input_offset < input_length && pipes[2].revents != 0) {
            if (pipes[2].revents & POLLNVAL) return EBADF;
            ssize_t count = write(*input_fd, input + input_offset,
                                  input_length - input_offset > sizeof(buffer) ? sizeof(buffer) : input_length - input_offset);
            if (count > 0) {
                input_offset += (size_t)count;
            } else if (count < 0 && errno == EINTR) {
                if (process_interrupted) continue;
            } else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                continue;
            } else if (count < 0 && errno == EPIPE) {
                close(*input_fd);
                *input_fd = -1;
            } else if (count < 0) {
                return errno;
            }
        }

        if (*input_fd >= 0 && input_offset == input_length) {
            close(*input_fd);
            *input_fd = -1;
        }
    }
    result->status = wait_status;
    if (interrupt_sent) return EINTR;
    return 0;
}

void *yian_process_execute(const char *program, char *const *arguments,
                           const char *directory, const uint8_t *stdin_bytes,
                           uint64_t stdin_length, int32_t stdin_mode,
                           int32_t capture_output, int32_t interruptible,
                           int32_t *reported_error) {
    int pipes[2][2] = {{-1, -1}, {-1, -1}};
    int input = -1;
    int input_pipe_fds[2] = {-1, -1};
    int error = 0;
    int actions_initialized = 0;
    int attributes_initialized = 0;
    int signal_installed = 0;
    int sigpipe_blocked = 0;
    int sigpipe_was_pending = 0;
    struct sigaction old_interrupt_action;
    sigset_t pipe_set, old_mask, pending_set;
    pid_t child = -1;
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    struct process_output *result = calloc(1, sizeof(*result));
    if (stdin_mode < 0 || stdin_mode > 2 || (stdin_mode == 2 && stdin_length != 0 && stdin_bytes == NULL)) {
        error = EINVAL;
        goto done;
    }
    if (result == NULL) { error = ENOMEM; goto done; }
    if (capture_output) {
        error = capture_pipe(pipes[0]);
        if (error != 0) goto done;
        error = capture_pipe(pipes[1]);
        if (error != 0) goto done;
    }
    if (stdin_mode == 0) {
        input = open("/dev/null", O_RDONLY | O_CLOEXEC);
        if (input < 0) { error = errno; goto done; }
        input = above_stdio(input);
        if (input < 0) { error = errno; goto done; }
    } else if (stdin_mode == 2) {
        error = input_pipe(input_pipe_fds);
        if (error != 0) goto done;
    }

    if (interruptible) {
        struct sigaction action;
        memset(&action, 0, sizeof(action));
        action.sa_handler = record_interrupt;
        sigemptyset(&action.sa_mask);
        process_interrupted = 0;
        if (sigaction(SIGINT, &action, &old_interrupt_action) < 0) { error = errno; goto done; }
        signal_installed = 1;
    }

    sigemptyset(&pipe_set);
    sigaddset(&pipe_set, SIGPIPE);
    int mask_error = pthread_sigmask(SIG_BLOCK, &pipe_set, &old_mask);
    if (mask_error != 0) { error = mask_error; goto done; }
    sigpipe_blocked = 1;
    if (sigpending(&pending_set) == 0) sigpipe_was_pending = sigismember(&pending_set, SIGPIPE) == 1;

    error = posix_spawn_file_actions_init(&actions);
    if (error != 0) goto done;
    actions_initialized = 1;
    error = posix_spawnattr_init(&attributes);
    if (error != 0) goto done;
    attributes_initialized = 1;
    error = posix_spawnattr_setsigmask(&attributes, &old_mask);
    if (error != 0) goto done;
    error = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSIGMASK);
    if (error != 0) goto done;
    if (stdin_mode == 0) {
        error = posix_spawn_file_actions_adddup2(&actions, input, STDIN_FILENO);
        if (error != 0) goto done;
        error = posix_spawn_file_actions_addclose(&actions, input);
        if (error != 0) goto done;
    } else if (stdin_mode == 2) {
        error = posix_spawn_file_actions_adddup2(&actions, input_pipe_fds[0], STDIN_FILENO);
        if (error != 0) goto done;
        for (int end = 0; end < 2; ++end) {
            error = posix_spawn_file_actions_addclose(&actions, input_pipe_fds[end]);
            if (error != 0) goto done;
        }
    }
    if (capture_output) {
        error = posix_spawn_file_actions_adddup2(&actions, pipes[0][1], STDOUT_FILENO);
        if (error != 0) goto done;
        error = posix_spawn_file_actions_adddup2(&actions, pipes[1][1], STDERR_FILENO);
        if (error != 0) goto done;
        for (int stream = 0; stream < 2; ++stream) {
            for (int end = 0; end < 2; ++end) {
                error = posix_spawn_file_actions_addclose(&actions, pipes[stream][end]);
                if (error != 0) goto done;
            }
        }
    }
    if (directory != NULL) {
        error = posix_spawn_file_actions_addchdir_np(&actions, directory);
        if (error != 0) goto done;
    }
    error = posix_spawnp(&child, program, &actions, &attributes, arguments, environ);
    if (error != 0) { child = -1; goto done; }
    if (stdin_mode == 0) { close(input); input = -1; }
    if (stdin_mode == 2) { close(input_pipe_fds[0]); input_pipe_fds[0] = -1; input = input_pipe_fds[1]; input_pipe_fds[1] = -1; }
    if (capture_output) {
        close(pipes[0][1]); pipes[0][1] = -1;
        close(pipes[1][1]); pipes[1][1] = -1;
    }
    int output_fds[2] = {pipes[0][0], pipes[1][0]};
    pipes[0][0] = pipes[1][0] = -1;
    int was_interrupted = 0;
    error = collect_process(result, output_fds, &input, stdin_bytes,
                            (size_t)stdin_length, &child, interruptible,
                            &was_interrupted);
    for (int stream = 0; stream < 2; ++stream) {
        if (output_fds[stream] >= 0) close(output_fds[stream]);
    }

done:
    if (child > 0) {
        kill(child, SIGKILL);
        while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {}
    }
    if (actions_initialized) posix_spawn_file_actions_destroy(&actions);
    if (attributes_initialized) posix_spawnattr_destroy(&attributes);
    if (input >= 0) close(input);
    for (int end = 0; end < 2; ++end) {
        if (input_pipe_fds[end] >= 0) close(input_pipe_fds[end]);
    }
    for (int stream = 0; stream < 2; ++stream) {
        for (int end = 0; end < 2; ++end) {
            if (pipes[stream][end] >= 0) close(pipes[stream][end]);
        }
    }
    if (sigpipe_blocked) {
        if (!sigpipe_was_pending) {
            struct timespec zero = {0, 0};
            while (sigtimedwait(&pipe_set, NULL, &zero) < 0 && errno == EINTR) {}
        }
        pthread_sigmask(SIG_SETMASK, &old_mask, NULL);
    }
    if (signal_installed) sigaction(SIGINT, &old_interrupt_action, NULL);
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

struct long_lived_child {
    pid_t process;
    int descriptors[3];
    int reaped;
    int wait_status;
    int closed;
};

static int set_nonblocking(int descriptor) {
    int flags = fcntl(descriptor, F_GETFL);
    if (flags < 0) return errno;
    if (fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) < 0) return errno;
    return 0;
}

static int stream_pipe(int descriptors[2], int parent_index) {
    if (pipe2(descriptors, O_CLOEXEC) < 0) return errno;
    descriptors[0] = above_stdio(descriptors[0]);
    if (descriptors[0] < 0) {
        int error = errno;
        close(descriptors[1]);
        descriptors[1] = -1;
        return error;
    }
    descriptors[1] = above_stdio(descriptors[1]);
    if (descriptors[1] < 0) {
        int error = errno;
        close(descriptors[0]);
        descriptors[0] = -1;
        return error;
    }
    return set_nonblocking(descriptors[parent_index]);
}

static void close_descriptor(int *descriptor) {
    if (*descriptor >= 0) {
        close(*descriptor);
        *descriptor = -1;
    }
}

static int child_record_status(struct long_lived_child *child, int wait_status) {
    child->wait_status = wait_status;
    child->reaped = 1;
    child->process = -1;
    return 1;
}

static int child_wait_nonblocking(struct long_lived_child *child) {
    if (child->reaped) return 1;
    int status = 0;
    pid_t result;
    do { result = waitpid(child->process, &status, WNOHANG); }
    while (result < 0 && errno == EINTR);
    if (result == 0) return 0;
    if (result < 0) return -errno;
    return child_record_status(child, status);
}

void *yian_child_spawn(const char *program, char *const *arguments,
                       const char *directory, const int32_t stdio_modes[3],
                       int32_t *reported_error) {
    int pipes[3][2] = {{-1, -1}, {-1, -1}, {-1, -1}};
    int null_descriptors[3] = {-1, -1, -1};
    int error = 0;
    int actions_initialized = 0;
    posix_spawn_file_actions_t actions;
    pid_t process = -1;
    struct long_lived_child *child = NULL;

    if (program == NULL || arguments == NULL || stdio_modes == NULL) {
        error = EINVAL;
        goto done;
    }
    for (int stream = 0; stream < 3; ++stream) {
        if (stdio_modes[stream] < 0 || stdio_modes[stream] > 2) {
            error = EINVAL;
            goto done;
        }
    }

    child = calloc(1, sizeof(*child));
    if (child == NULL) { error = ENOMEM; goto done; }
    child->process = -1;
    for (int stream = 0; stream < 3; ++stream) {
        child->descriptors[stream] = -1;
    }

    for (int stream = 0; stream < 3; ++stream) {
        if (stdio_modes[stream] == 2) {
            int parent_end = stream == 0 ? 1 : 0;
            error = stream_pipe(pipes[stream], parent_end);
            if (error != 0) goto done;
        } else if (stdio_modes[stream] == 0) {
            int flags = stream == 0 ? O_RDONLY : O_WRONLY;
            null_descriptors[stream] = open("/dev/null", flags | O_CLOEXEC);
            if (null_descriptors[stream] < 0) { error = errno; goto done; }
            null_descriptors[stream] = above_stdio(null_descriptors[stream]);
            if (null_descriptors[stream] < 0) { error = errno; goto done; }
        }
    }

    error = posix_spawn_file_actions_init(&actions);
    if (error != 0) goto done;
    actions_initialized = 1;
    for (int stream = 0; stream < 3; ++stream) {
        if (stdio_modes[stream] == 1) continue;
        int child_end = stdio_modes[stream] == 0
            ? null_descriptors[stream]
            : (stream == 0 ? pipes[stream][0] : pipes[stream][1]);
        int target = stream == 0 ? STDIN_FILENO : stream == 1 ? STDOUT_FILENO : STDERR_FILENO;
        error = posix_spawn_file_actions_adddup2(&actions, child_end, target);
        if (error != 0) goto done;
        if (stdio_modes[stream] == 0) {
            error = posix_spawn_file_actions_addclose(&actions, null_descriptors[stream]);
            if (error != 0) goto done;
        } else {
            for (int end = 0; end < 2; ++end) {
                error = posix_spawn_file_actions_addclose(&actions, pipes[stream][end]);
                if (error != 0) goto done;
            }
        }
    }
    if (directory != NULL) {
        error = posix_spawn_file_actions_addchdir_np(&actions, directory);
        if (error != 0) goto done;
    }
    error = posix_spawnp(&process, program, &actions, NULL, arguments, environ);
    if (error != 0) { process = -1; goto done; }

    child->process = process;
    process = -1;
    for (int stream = 0; stream < 3; ++stream) {
        if (stdio_modes[stream] == 2) {
            int parent_end = stream == 0 ? 1 : 0;
            int child_end = 1 - parent_end;
            close_descriptor(&pipes[stream][child_end]);
            child->descriptors[stream] = pipes[stream][parent_end];
            pipes[stream][parent_end] = -1;
        }
        close_descriptor(&null_descriptors[stream]);
    }

done:
    if (actions_initialized) posix_spawn_file_actions_destroy(&actions);
    for (int stream = 0; stream < 3; ++stream) {
        close_descriptor(&null_descriptors[stream]);
        close_descriptor(&pipes[stream][0]);
        close_descriptor(&pipes[stream][1]);
    }
    if (process > 0) {
        kill(process, SIGKILL);
        while (waitpid(process, NULL, 0) < 0 && errno == EINTR) {}
    }
    if (error != 0) {
        if (child != NULL) {
            for (int stream = 0; stream < 3; ++stream) close_descriptor(&child->descriptors[stream]);
            free(child);
        }
        *reported_error = error;
        return NULL;
    }
    *reported_error = 0;
    return child;
}

int32_t yian_child_fd(void *opaque, int32_t stream) {
    if (opaque == NULL || stream < 0 || stream >= 3) return -EINVAL;
    const struct long_lived_child *child = opaque;
    if (child->closed) return -EBADF;
    return child->descriptors[stream];
}

int32_t yian_child_close_pipe(void *opaque, int32_t stream) {
    if (opaque == NULL || stream < 0 || stream >= 3) return -EINVAL;
    struct long_lived_child *child = opaque;
    close_descriptor(&child->descriptors[stream]);
    return 0;
}

int64_t yian_child_read(void *opaque, int32_t stream, uint8_t *buffer, uint64_t length) {
    int32_t descriptor = yian_child_fd(opaque, stream);
    if (descriptor < 0) return descriptor;
    return yian_io_read(descriptor, buffer, length);
}

int64_t yian_child_write(void *opaque, int32_t stream, const uint8_t *buffer, uint64_t length) {
    int32_t descriptor = yian_child_fd(opaque, stream);
    if (descriptor < 0) return descriptor;
    return yian_io_write(descriptor, buffer, length);
}

static void child_status_fields(struct long_lived_child *child, int32_t *status,
                                int32_t *signaled) {
    *signaled = WIFSIGNALED(child->wait_status);
    *status = *signaled ? WTERMSIG(child->wait_status) : WEXITSTATUS(child->wait_status);
}

int32_t yian_child_try_wait(void *opaque, int32_t *status, int32_t *signaled) {
    if (opaque == NULL || status == NULL || signaled == NULL) return -EINVAL;
    struct long_lived_child *child = opaque;
    int result = child_wait_nonblocking(child);
    if (result <= 0) return result;
    child_status_fields(child, status, signaled);
    return 1;
}

int32_t yian_child_wait(void *opaque, int32_t *status, int32_t *signaled) {
    if (opaque == NULL || status == NULL || signaled == NULL) return -EINVAL;
    struct long_lived_child *child = opaque;
    if (!child->reaped) {
        int wait_status = 0;
        pid_t result;
        do { result = waitpid(child->process, &wait_status, 0); }
        while (result < 0 && errno == EINTR);
        if (result < 0) return -errno;
        child_record_status(child, wait_status);
    }
    child_status_fields(child, status, signaled);
    return 0;
}

int32_t yian_child_signal(void *opaque, int32_t signal_number) {
    if (opaque == NULL || signal_number <= 0) return -EINVAL;
    struct long_lived_child *child = opaque;
    if (child->reaped || child->process <= 0) return 0;
    if (kill(child->process, signal_number) == 0 || errno == ESRCH) return 0;
    return -errno;
}

int32_t yian_child_close(void *opaque) {
    if (opaque == NULL) return -EINVAL;
    struct long_lived_child *child = opaque;
    if (child->closed) return 0;
    child->closed = 1;
    for (int stream = 0; stream < 3; ++stream) close_descriptor(&child->descriptors[stream]);
    if (child->reaped) return 0;

    if (kill(child->process, SIGTERM) < 0 && errno != ESRCH) return -errno;
    struct timespec pause = {0, 5000000};
    int64_t now = monotonic_millis();
    if (now < 0) return -errno;
    int64_t deadline = now + 250;
    while (!child->reaped) {
        int result = child_wait_nonblocking(child);
        if (result < 0) return result;
        if (result > 0) break;
        now = monotonic_millis();
        if (now < 0) return -errno;
        if (now >= deadline) break;
        while (nanosleep(&pause, &pause) < 0 && errno == EINTR) {}
        pause.tv_sec = 0;
        pause.tv_nsec = 5000000;
    }
    if (!child->reaped) {
        if (kill(child->process, SIGKILL) < 0 && errno != ESRCH) return -errno;
        return yian_child_wait(child, &(int32_t){0}, &(int32_t){0});
    }
    return 0;
}

void yian_child_drop(void *opaque) {
    struct long_lived_child *child = opaque;
    if (child == NULL) return;
    if (!child->closed) yian_child_close(child);
    if (!child->reaped && child->process > 0) {
        kill(child->process, SIGKILL);
        while (waitpid(child->process, NULL, 0) < 0 && errno == EINTR) {}
    }
    for (int stream = 0; stream < 3; ++stream) close_descriptor(&child->descriptors[stream]);
    free(child);
}

int64_t yian_poller_wait(const int32_t *descriptors, const uint32_t *interests,
                         uint64_t count, int64_t timeout_milliseconds,
                         uint32_t *ready) {
    if (count > INT_MAX || timeout_milliseconds < -1 ||
        (count != 0 && (descriptors == NULL || interests == NULL || ready == NULL))) return -EINVAL;
    struct pollfd *poll_descriptors = count == 0 ? NULL : calloc((size_t)count, sizeof(*poll_descriptors));
    if (count != 0 && poll_descriptors == NULL) return -ENOMEM;
    for (uint64_t index = 0; index < count; ++index) {
        short events = 0;
        if (interests[index] & 1u) events |= POLLIN;
        if (interests[index] & 2u) events |= POLLOUT;
        if (interests[index] & ~3u) { free(poll_descriptors); return -EINVAL; }
        poll_descriptors[index] = (struct pollfd){descriptors[index], events, 0};
        ready[index] = 0;
    }
    int timeout = timeout_milliseconds > INT_MAX ? INT_MAX : (int)timeout_milliseconds;
    int result = poll(poll_descriptors, (nfds_t)count, timeout);
    int error = result < 0 ? errno : 0;
    if (result > 0) {
        for (uint64_t index = 0; index < count; ++index) {
            short flags = poll_descriptors[index].revents;
            uint32_t output = 0;
            if (flags & (POLLIN | POLLPRI)) output |= 1u;
            if (flags & POLLOUT) output |= 2u;
            if (flags & POLLHUP) output |= 4u;
            if (flags & (POLLERR | POLLNVAL)) output |= 8u;
            ready[index] = output;
        }
    }
    free(poll_descriptors);
    return error == 0 ? result : -error;
}

int64_t yian_monotonic_nanos(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) return -errno;
    if ((uint64_t)now.tv_sec > (uint64_t)INT64_MAX / 1000000000u) return -EOVERFLOW;
    return (int64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

int64_t yian_process_executable_path(uint8_t *buffer, uint64_t capacity) {
    if (buffer == NULL || capacity == 0 || capacity > SIZE_MAX) return -EINVAL;
    ssize_t length = readlink("/proc/self/exe", (char *)buffer, (size_t)capacity);
    if (length < 0) return -errno;
    if ((uint64_t)length == capacity) return -ERANGE;
    return (int64_t)length;
}
