/*
 * profile.c: runtime function and call-path profiling.
 *
 * The compiler emits one descriptor per generated source callable and calls the
 * enter/leave hooks at the real function boundaries. This file owns the active
 * stack, path aggregation, monotonic elapsed time, and the JSON/text reports.
 * Without YIAN_RUNTIME_PROFILE the translation unit defines nothing.
 */

#ifdef YIAN_RUNTIME_PROFILE

#include "yian_profile.h"

#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* The compiler emits literal LLVM structs with this LP64 layout. */
_Static_assert(sizeof(YianProfileSlice) == 16u, "profile slice layout");
_Static_assert(sizeof(YianProfileFunction) == 136u, "profile descriptor layout");
_Static_assert(offsetof(YianProfileFunction, line) == 72u, "profile line offset");
_Static_assert(offsetof(YianProfileFunction, owner_type) == 88u, "profile owner offset");
_Static_assert(sizeof(YianProfileConfig) == 88u, "profile config layout");
_Static_assert(offsetof(YianProfileConfig, functions) == 80u, "profile config functions offset");

typedef struct {
    uint64_t node_id;
    uint64_t function_id;
    uint64_t start_ns;
    uint64_t direct_child_ns;
} ProfileFrame;

typedef struct {
    uint64_t node_id;
    uint64_t parent_id;
    uint64_t function_id;
    uint64_t calls;
    uint64_t inclusive_ns;
    uint64_t self_ns;
} ProfilePathNode;

typedef struct {
    uint64_t node_id;
    uint64_t depth;
} ProfileTreeItem;

typedef struct {
    int state; /* 0 new, 1 collecting, 2 finished, 3 exported */
    uint64_t resolution_ns;
    ProfileFrame *frames;
    size_t frame_count;
    size_t frame_capacity;
    ProfilePathNode *nodes;
    size_t node_count;
    size_t node_capacity;
    uint64_t *function_calls;
    uint64_t *function_inclusive;
    uint64_t *function_self;
} ProfileCollector;

static ProfileCollector collector;

static void profile_abort(const char *message) {
    static const char prefix[] = "yian: profile failure: ";
    const char newline[] = "\n";
    ssize_t ignored;
    ignored = write(2, prefix, sizeof(prefix) - 1);
    ignored = write(2, message, strlen(message));
    ignored = write(2, newline, sizeof(newline) - 1);
    (void)ignored;
    _exit(1);
}

static void *checked_realloc(void *pointer, size_t size) {
    void *result = realloc(pointer, size);
    if (result == 0 && size != 0) {
        profile_abort("allocation failed");
    }
    return result;
}

static uint64_t profile_now_ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        profile_abort("monotonic clock unavailable");
    }
    return (uint64_t)value.tv_sec * 1000000000u + (uint64_t)value.tv_nsec;
}

static uint64_t profile_clock_resolution_ns(void) {
    struct timespec value;
    if (clock_getres(CLOCK_MONOTONIC, &value) != 0) {
        return 0;
    }
    return (uint64_t)value.tv_sec * 1000000000u + (uint64_t)value.tv_nsec;
}

static void ensure_frame_capacity(void) {
    if (collector.frame_count < collector.frame_capacity) {
        return;
    }
    size_t capacity = collector.frame_capacity == 0 ? 64u : collector.frame_capacity * 2u;
    collector.frames = checked_realloc(collector.frames, capacity * sizeof(ProfileFrame));
    collector.frame_capacity = capacity;
}

static uint64_t find_or_create_node(uint64_t parent_id, uint64_t function_id) {
    size_t index = 0;
    while (index < collector.node_count) {
        ProfilePathNode *node = &collector.nodes[index];
        if (node->parent_id == parent_id && node->function_id == function_id) {
            return node->node_id;
        }
        index += 1u;
    }
    if (collector.node_count == collector.node_capacity) {
        size_t capacity = collector.node_capacity == 0 ? 64u : collector.node_capacity * 2u;
        collector.nodes = checked_realloc(collector.nodes, capacity * sizeof(ProfilePathNode));
        collector.node_capacity = capacity;
    }
    index = collector.node_count;
    collector.nodes[index].node_id = (uint64_t)index + 1u;
    collector.nodes[index].parent_id = parent_id;
    collector.nodes[index].function_id = function_id;
    collector.nodes[index].calls = 0;
    collector.nodes[index].inclusive_ns = 0;
    collector.nodes[index].self_ns = 0;
    collector.node_count += 1u;
    return (uint64_t)index + 1u;
}

void __yian_profile_init(void) {
    if (collector.state != 0) {
        return;
    }
    if (__yian_profile_config.abi_version != YIAN_PROFILE_ABI_VERSION) {
        profile_abort("unsupported runtime profile ABI version");
    }
    collector.resolution_ns = profile_clock_resolution_ns();
    uint32_t count = __yian_profile_config.descriptor_count;
    if (count != 0) {
        size_t bytes = (size_t)count * sizeof(uint64_t);
        collector.function_calls = checked_realloc(0, bytes);
        collector.function_inclusive = checked_realloc(0, bytes);
        collector.function_self = checked_realloc(0, bytes);
        memset(collector.function_calls, 0, bytes);
        memset(collector.function_inclusive, 0, bytes);
        memset(collector.function_self, 0, bytes);
    }
    collector.state = 1;
}

void __yian_profile_enter(uint64_t function_id) {
    if (collector.state == 0) {
        __yian_profile_init();
    }
    if (collector.state != 1) {
        return;
    }
    uint64_t parent_id = 0;
    if (collector.frame_count != 0) {
        parent_id = collector.frames[collector.frame_count - 1u].node_id;
    }
    uint64_t node_id = find_or_create_node(parent_id, function_id);
    collector.nodes[node_id - 1u].calls += 1u;
    ensure_frame_capacity();
    ProfileFrame *frame = &collector.frames[collector.frame_count];
    frame->node_id = node_id;
    frame->function_id = function_id;
    frame->start_ns = profile_now_ns();
    frame->direct_child_ns = 0;
    collector.frame_count += 1u;
}

void __yian_profile_leave(uint64_t function_id) {
    if (collector.state != 1 || collector.frame_count == 0) {
        return;
    }
    ProfileFrame *frame = &collector.frames[collector.frame_count - 1u];
    if (frame->function_id != function_id) {
        profile_abort("profiled call stack does not match");
    }
    uint64_t ended = profile_now_ns();
    uint64_t duration = ended >= frame->start_ns ? ended - frame->start_ns : 0;
    uint64_t self = duration >= frame->direct_child_ns ? duration - frame->direct_child_ns : 0;
    ProfilePathNode *node = &collector.nodes[frame->node_id - 1u];
    node->inclusive_ns += duration;
    node->self_ns += self;
    collector.frame_count -= 1u;
    if (collector.frame_count != 0) {
        collector.frames[collector.frame_count - 1u].direct_child_ns += duration;
    }
}

static void write_json_string(FILE *file, const YianProfileSlice *value) {
    const uint8_t *data = value->data;
    fputc('"', file);
    uint64_t index = 0;
    while (index < value->length) {
        uint8_t byte = data[index];
        if (byte == '"' || byte == '\\') {
            fputc('\\', file);
            fputc(byte, file);
        } else if (byte < 0x20u) {
            fprintf(file, "\\u%04x", (unsigned)byte);
        } else {
            fputc(byte, file);
        }
        index += 1u;
    }
    fputc('"', file);
}

static void write_text_slice(FILE *file, const YianProfileSlice *value) {
    uint64_t index = 0;
    while (index < value->length) {
        uint8_t byte = value->data[index];
        fputc(byte < 0x20u ? '?' : byte, file);
        index += 1u;
    }
}

static const YianProfileFunction *descriptor_for(uint64_t function_id) {
    if (function_id == 0 || function_id > (uint64_t)__yian_profile_config.descriptor_count) {
        return 0;
    }
    return &__yian_profile_config.functions[function_id - 1u];
}

static const char *kind_text(uint32_t kind) {
    if (kind == 1u) {
        return "method";
    }
    if (kind == 2u) {
        return "closure";
    }
    return "function";
}

static FILE *open_report(const char *name) {
    const YianProfileSlice *directory = &__yian_profile_config.report_dir;
    if (directory->length == 0) {
        return 0;
    }
    size_t name_length = strlen(name);
    size_t length = (size_t)directory->length + name_length + 2u;
    char *path = malloc(length);
    if (path == 0) {
        profile_abort("allocation failed");
    }
    memcpy(path, directory->data, (size_t)directory->length);
    path[directory->length] = '/';
    memcpy(path + directory->length + 1u, name, name_length + 1u);
    FILE *file = fopen(path, "wb");
    free(path);
    return file;
}

static uint64_t *sort_totals;
static int compare_descending(const void *left, const void *right) {
    uint64_t first = *(const uint64_t *)left;
    uint64_t second = *(const uint64_t *)right;
    uint64_t first_value = sort_totals[first];
    uint64_t second_value = sort_totals[second];
    if (first_value != second_value) {
        return first_value > second_value ? -1 : 1;
    }
    return first < second ? -1 : 1;
}

static void write_json_report(void) {
    FILE *file = open_report("profile.json");
    if (file == 0) {
        return;
    }
    const YianProfileConfig *config = &__yian_profile_config;
    fprintf(file, "{\"format\":1,\"kind\":\"yian-runtime-profile\",\"abi_version\":%" PRIu64 ",\n",
        (uint64_t)YIAN_PROFILE_ABI_VERSION);
    fputs("  \"run\":{\"id\":", file);
    write_json_string(file, &config->run_id);
    fputs(",\"report_dir\":", file);
    write_json_string(file, &config->report_dir);
    fputs(",\"program\":", file);
    write_json_string(file, &config->program);
    fputs(",\"pointer_mode\":", file);
    write_json_string(file, &config->pointer_mode);
    fprintf(file, ",\"optimization\":%u},\n", (unsigned)config->optimization);
    fprintf(file,
        "  \"measurement\":{\"clock\":\"CLOCK_MONOTONIC\",\"unit\":\"ns\",\"resolution_ns\":%" PRIu64
        ",\"method\":\"instrumentation_elapsed\",\"overhead_compensated\":false},\n",
        collector.resolution_ns);
    fputs("  \"functions\":[", file);
    uint32_t descriptor_index = 0;
    int first_function = 1;
    while (descriptor_index < config->descriptor_count) {
        uint64_t function_id = (uint64_t)descriptor_index + 1u;
        uint64_t calls = collector.function_calls[descriptor_index];
        if (calls != 0) {
            const YianProfileFunction *descriptor = &config->functions[descriptor_index];
            if (!first_function) {
                fputc(',', file);
            }
            first_function = 0;
            fprintf(file, "\n    {\"function_id\":%" PRIu64 ",\"display_name\":", function_id);
            write_json_string(file, &descriptor->display_name);
            fputs(",\"package\":", file);
            write_json_string(file, &descriptor->package);
            fputs(",\"module\":", file);
            write_json_string(file, &descriptor->module);
            fputs(",\"source_path\":", file);
            write_json_string(file, &descriptor->source_path);
            fprintf(file, ",\"line\":%u,\"column\":%u,\"kind\":\"%s\",\"owner_type\":",
                (unsigned)descriptor->line, (unsigned)descriptor->column, kind_text(descriptor->kind));
            write_json_string(file, &descriptor->owner_type);
            fputs(",\"concrete_arguments\":", file);
            write_json_string(file, &descriptor->concrete_arguments);
            fputs(",\"origin\":", file);
            write_json_string(file, &descriptor->origin);
            fprintf(file, ",\"calls\":%" PRIu64 ",\"inclusive_ns\":%" PRIu64 ",\"self_ns\":%" PRIu64 "}",
                calls, collector.function_inclusive[descriptor_index], collector.function_self[descriptor_index]);
        }
        descriptor_index += 1u;
    }
    fputs("\n  ],\n  \"call_tree\":[", file);
    size_t index = 0;
    while (index < collector.node_count) {
        const ProfilePathNode *node = &collector.nodes[index];
        const YianProfileFunction *descriptor = descriptor_for(node->function_id);
        if (index != 0) {
            fputc(',', file);
        }
        fprintf(file,
            "\n    {\"node_id\":%" PRIu64 ",\"parent_id\":%" PRIu64 ",\"function_id\":%" PRIu64
            ",\"display_name\":",
            node->node_id, node->parent_id, node->function_id);
        if (descriptor != 0) {
            write_json_string(file, &descriptor->display_name);
        } else {
            fputs("\"\"", file);
        }
        fprintf(file, ",\"calls\":%" PRIu64 ",\"inclusive_ns\":%" PRIu64 ",\"self_ns\":%" PRIu64 "}",
            node->calls, node->inclusive_ns, node->self_ns);
        index += 1u;
    }
    fputs("\n  ]\n}\n", file);
    fclose(file);
}

static void write_text_report(void) {
    FILE *file = open_report("summary.txt");
    if (file == 0) {
        return;
    }
    const YianProfileConfig *config = &__yian_profile_config;
    fputs("YIAN runtime profile\nrun: ", file);
    write_text_slice(file, &config->run_id);
    fputs("\nprogram: ", file);
    write_text_slice(file, &config->program);
    fputs("\npointer mode: ", file);
    write_text_slice(file, &config->pointer_mode);
    fprintf(file, "\noptimization: %u\n", (unsigned)config->optimization);
    fprintf(file,
        "clock: CLOCK_MONOTONIC\nunit: ns\nresolution_ns: %" PRIu64
        "\nmethod: instrumentation_elapsed\noverhead_compensated: false\n",
        collector.resolution_ns);
    uint32_t executed = 0;
    uint32_t descriptor_index = 0;
    while (descriptor_index < config->descriptor_count) {
        if (collector.function_calls[descriptor_index] != 0) {
            executed += 1u;
        }
        descriptor_index += 1u;
    }
    fprintf(file, "executed functions: %u of %u\n", (unsigned)executed, (unsigned)config->descriptor_count);
    fputs("note: elapsed time includes callees, I/O and waits; function inclusive time overlaps\n"
          "      under recursion; values are not a process wall-clock total and are not\n"
          "      compensated for instrumentation overhead.\n", file);

    uint64_t *order = 0;
    size_t count = 0;
    if (executed != 0) {
        order = checked_realloc(0, (size_t)executed * sizeof(uint64_t));
        descriptor_index = 0;
        while (descriptor_index < config->descriptor_count) {
            if (collector.function_calls[descriptor_index] != 0) {
                order[count] = (uint64_t)descriptor_index;
                count += 1u;
            }
            descriptor_index += 1u;
        }
    }

    if (count != 0) {
        sort_totals = collector.function_self;
        qsort(order, count, sizeof(uint64_t), compare_descending);
        fputs("\nHot spots by self time\n", file);
        size_t index = 0;
        while (index < count) {
            const YianProfileFunction *descriptor = &config->functions[order[index]];
            fprintf(file, "  %" PRIu64 ". self_ns=%" PRIu64 " inclusive_ns=%" PRIu64 " calls=%" PRIu64 "  ",
                (uint64_t)index + 1u, collector.function_self[order[index]],
                collector.function_inclusive[order[index]], collector.function_calls[order[index]]);
            if (descriptor->package.length != 0) {
                write_text_slice(file, &descriptor->package);
                fputs("::", file);
            }
            write_text_slice(file, &descriptor->display_name);
            fputs(" (", file);
            write_text_slice(file, &descriptor->source_path);
            fprintf(file, ":%u:%u)\n", (unsigned)descriptor->line, (unsigned)descriptor->column);
            index += 1u;
        }
        sort_totals = collector.function_inclusive;
        qsort(order, count, sizeof(uint64_t), compare_descending);
        fputs("\nHot spots by inclusive time\n", file);
        index = 0;
        while (index < count) {
            const YianProfileFunction *descriptor = &config->functions[order[index]];
            fprintf(file, "  %" PRIu64 ". inclusive_ns=%" PRIu64 " self_ns=%" PRIu64 " calls=%" PRIu64 "  ",
                (uint64_t)index + 1u, collector.function_inclusive[order[index]],
                collector.function_self[order[index]], collector.function_calls[order[index]]);
            if (descriptor->package.length != 0) {
                write_text_slice(file, &descriptor->package);
                fputs("::", file);
            }
            write_text_slice(file, &descriptor->display_name);
            fputs(" (", file);
            write_text_slice(file, &descriptor->source_path);
            fprintf(file, ":%u:%u)\n", (unsigned)descriptor->line, (unsigned)descriptor->column);
            index += 1u;
        }
        free(order);
    }

    fputs("\nCall tree (node_id: calls inclusive_ns self_ns name)\n", file);
    if (collector.node_count != 0) {
        ProfileTreeItem *stack = checked_realloc(0, collector.node_count * sizeof(ProfileTreeItem));
        size_t depth = 0;
        size_t index = collector.node_count;
        while (index > 0) {
            index -= 1u;
            if (collector.nodes[index].parent_id == 0) {
                stack[depth].node_id = collector.nodes[index].node_id;
                stack[depth].depth = 0;
                depth += 1u;
            }
        }
        size_t next = depth;
        while (next > 0) {
            next -= 1u;
            ProfileTreeItem item = stack[next];
            const ProfilePathNode *node = &collector.nodes[item.node_id - 1u];
            const YianProfileFunction *descriptor = descriptor_for(node->function_id);
            unsigned indent = (unsigned)(item.depth * 2u);
            fprintf(file, "%*snode %" PRIu64 ": calls=%" PRIu64 " inclusive_ns=%" PRIu64 " self_ns=%" PRIu64 " ",
                (int)indent, "", node->node_id, node->calls, node->inclusive_ns, node->self_ns);
            if (descriptor != 0) {
                if (descriptor->package.length != 0) {
                    write_text_slice(file, &descriptor->package);
                    fputs("::", file);
                }
                write_text_slice(file, &descriptor->display_name);
                fputs(" (", file);
                write_text_slice(file, &descriptor->source_path);
                fprintf(file, ":%u:%u)", (unsigned)descriptor->line, (unsigned)descriptor->column);
            }
            fputc('\n', file);
            /* Push children in reverse insertion order so the first child prints first. */
            index = collector.node_count;
            while (index > 0) {
                index -= 1u;
                if (collector.nodes[index].parent_id == node->node_id) {
                    if (next == collector.node_count) {
                        profile_abort("call tree stack overflow");
                    }
                    stack[next].node_id = collector.nodes[index].node_id;
                    stack[next].depth = item.depth + 1u;
                    next += 1u;
                }
            }
        }
        free(stack);
    }
    fclose(file);
}

void __yian_profile_finish(void) {
    if (collector.state != 1) {
        return;
    }
    collector.state = 2;
    uint32_t descriptor_index = 0;
    while (descriptor_index < __yian_profile_config.descriptor_count) {
        collector.function_calls[descriptor_index] = 0;
        collector.function_inclusive[descriptor_index] = 0;
        collector.function_self[descriptor_index] = 0;
        descriptor_index += 1u;
    }
    size_t index = 0;
    while (index < collector.node_count) {
        ProfilePathNode *node = &collector.nodes[index];
        if (node->function_id != 0 && node->function_id <= (uint64_t)__yian_profile_config.descriptor_count) {
            uint64_t function_index = node->function_id - 1u;
            collector.function_calls[function_index] += node->calls;
            collector.function_inclusive[function_index] += node->inclusive_ns;
            collector.function_self[function_index] += node->self_ns;
        }
        index += 1u;
    }
    write_json_report();
    write_text_report();
    free(collector.frames);
    free(collector.nodes);
    free(collector.function_calls);
    free(collector.function_inclusive);
    free(collector.function_self);
    collector.frames = 0;
    collector.nodes = 0;
    collector.function_calls = 0;
    collector.function_inclusive = 0;
    collector.function_self = 0;
    collector.frame_count = 0;
    collector.frame_capacity = 0;
    collector.node_count = 0;
    collector.node_capacity = 0;
    collector.state = 3;
}

#endif
