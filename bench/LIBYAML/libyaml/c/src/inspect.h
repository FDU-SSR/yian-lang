#ifndef LIBYAML_BENCH_INSPECT_H
#define LIBYAML_BENCH_INSPECT_H

#include <stddef.h>

/*
 * Emit the yaml-compat-v1 JSON Lines observation of one file.
 *
 * mode is "tokens", "events" or "documents".  chunk_size == 0 feeds the whole
 * file through the string input; a positive chunk_size feeds at most that many
 * bytes per read callback.  fail_after == -1 disables fault injection; a value
 * >= 0 makes the read callback deliver exactly that many bytes and then report
 * an input error.
 *
 * Returns 0 when the requested observation completed, 1 for a reader,
 * scanner, parser or composer error, and 2 for usage, file or tool errors.
 * The caller owns nothing; the function opens and closes the file itself.
 */
int yaml_bench_inspect(const char *mode, const char *path,
                       size_t chunk_size, long long fail_after);

#endif
