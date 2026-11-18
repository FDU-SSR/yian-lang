#ifndef YIAN_PROFILE_H
#define YIAN_PROFILE_H

#include <stdint.h>

/* Runtime profiling ABI. The compiler emits the immutable config and one
 * descriptor per instrumented source callable; the C runtime owns collection
 * and report serialization. All strings are UTF-8 byte slices without a
 * terminating NUL and stay alive for the process lifetime. */
#define YIAN_PROFILE_ABI_VERSION 1

typedef struct {
    const uint8_t *data;
    uint64_t length;
} YianProfileSlice;

typedef struct {
    uint64_t function_id;
    YianProfileSlice display_name;
    YianProfileSlice package;
    YianProfileSlice module;
    YianProfileSlice source_path;
    uint32_t line;    /* 1-based source line */
    uint32_t column;  /* 1-based column counted in Unicode scalar values */
    uint32_t kind;    /* 0 function, 1 method, 2 closure */
    uint32_t reserved;
    YianProfileSlice owner_type;
    YianProfileSlice concrete_arguments;
    YianProfileSlice origin;
} YianProfileFunction;

typedef struct {
    uint64_t abi_version;
    YianProfileSlice report_dir;
    YianProfileSlice run_id;
    YianProfileSlice program;
    YianProfileSlice pointer_mode;
    uint32_t optimization;
    uint32_t descriptor_count;
    const YianProfileFunction *functions;
} YianProfileConfig;

extern const YianProfileConfig __yian_profile_config;

void __yian_profile_init(void);
void __yian_profile_enter(uint64_t function_id);
void __yian_profile_leave(uint64_t function_id);
void __yian_profile_finish(void);

#endif
