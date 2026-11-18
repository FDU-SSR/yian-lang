# libyaml compatibility runner

`run_yaml_suite.py` is the independent correctness driver for the YIAN libyaml
port. It compares the pinned C libyaml 0.2.5 parser with the YIAN port over
[yaml-test-suite](https://github.com/yaml/yaml-test-suite) case directories and
writes a self-contained report. It is not part of `scripts/run_tests.py`, the
compiler suites, `anx test` or the AWFY/BG benchmark sets.

The runner never builds, downloads, refreshes or installs anything. Provide a
checkout of the test-suite data branch whose case directories contain `in.yaml`.
The committed corpus used for the reference measurements is commit
`6ad3d2c62885d82fc349026c136ef560838fdf3d`; the runner records the actual commit
it sees.

## Usage

```sh
python3 bench/LIBYAML/libyaml/tests/run_yaml_suite.py SUITE \
    (--case ID ... | --all | --list) [options]
```

Positional arguments:

| Argument | Meaning |
| --- | --- |
| `SUITE` | yaml-test-suite data checkout. |
| `C_BINARY` | C oracle; defaults to `build/bench/cbin/LIBYAML_libyaml`. |
| `YIAN_BINARY` | Optional explicit YIAN binary. Without it the raw and fat builds are read from `build/bench/yraw/LIBYAML_libyaml` and `build/bench/yfat/LIBYAML_libyaml`. |

Options:

| Option | Meaning |
| --- | --- |
| `--case ID` | Select one exact suite-relative case directory such as `4FJ6` or `3RLN/02`. Repeatable. A parent directory or substring never expands to children. |
| `--all` | Select every case. Mutually exclusive with `--case`. |
| `--list` | Print the selected IDs without starting a subprocess. `--list` alone enumerates the whole suite. |
| `--mode tokens\|events\|documents` | Observation mode; default `documents`. |
| `--pointer-mode raw\|fat\|both` | YIAN pointer modes; default `both`. An explicit `YIAN_BINARY` requires `raw` or `fat`. |
| `--timeout SECONDS` | Per-run limit; default 5. |
| `--report-dir PATH` | Report directory; default `bench/LIBYAML/libyaml/reports`. |
| `--chunk-size N` | Feed the input through the read callback in chunks of at most `N` bytes; default 0 uses the string input. |
| `--fail-after N` | With a positive `--chunk-size`, deliver exactly `N` bytes and then report a read input error. Default `-1` disables the injection. |

One of `--case`, `--all` or `--list` is required. Invalid IDs, missing
binaries, conflicting options and malformed numeric options stop the whole
invocation with exit code 2 and no report.

## Observation protocol

Both programs accept:

```text
--inspect tokens|events|documents --file PATH [--chunk-size N] [--fail-after N]
```

`--inspect` uses a fresh parser for the requested mode and consumes the complete
stream. The default `--file PATH` form keeps the first-document tree checksum
used by the vendored benchmark corpus. Exit codes are `0` for a completed
observation, `1` for a reader/scanner/parser/composer error, `2` for usage, file
or tool errors, and `3` for the cycle rejection of the checksum entry point.

Output is UTF-8 JSON Lines. The first line is
`{"schema":"yaml-compat-v1","mode":"tokens"}` (with the actual mode). Token,
event and document records follow; every byte string is lowercase hex, every
`Mark` is `[index,line,column]` and options are `null` or a value. A run ends
with either `{"record":"end"}` or an `error` record carrying the error kind,
marks, text, raw offset and character value. A successful document observation
enumerates one-based node IDs and does not expand alias edges, so shared and
cyclic documents stay finite.

## Classification and exit status

A case is `matched_success` when the C oracle and every executed YIAN binary
exit 0, emit a valid `end` record and produce the same record list. It is
`matched_rejection` when both exit 1 and produce the same error record and the
same preceding records. Anything else is a failure: a difference, a
timeout, a crash, a malformed protocol, a tool error or a failing C oracle.
A rejection is never inferred from two arbitrary nonzero exit codes.

The runner exits 0 when every selected case matched, 1 when any case failed,
and 2 for usage or dependency errors.

## Reports

Each invocation atomically writes `correctness-<mode>-<pointer-mode>.json` and
the matching `.md` file below `--report-dir`. The JSON document has the fixed
top-level fields `schema` (`libyaml-report-v1`), `mode`, `pointer_modes`,
`selection`, `suite_commit`, `source_identity`, `binary_identities`, `cases`
and `totals`. Every case records its input SHA-256, the C/raw/fat legs
(`rc`, `terminal`, `output_sha256`), the result and a failure reason. Totals
count selected, matched-success, matched-rejection, failed and timed-out cases.
Unused legs are `null`; reports never persist full stdout or stderr.

The report directory is a local output location and is ignored by Git. A report
describes exactly the run that produced it; a single-case run is not a
whole-suite result.
