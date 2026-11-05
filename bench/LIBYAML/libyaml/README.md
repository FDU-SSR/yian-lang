# libyaml parse workload

The C reference uses the pinned upstream libyaml 0.2.5 parser
(`yaml_parser_load`) to parse a pinned real-world YAML corpus into a document
tree and traverses that tree. The corpus is three unmodified Kubernetes YAML
documents from the kube-prometheus project, fixed by commit and vendored under
`corpus/` with a license and provenance record. The YIAN implementation is a
module-for-module port of the same parse chain: a byte/callback reader with
UTF-8 and UTF-16 decoding and character marks, the incremental scanner with
simple keys, indentation, flow collections, directives, tags and every scalar
style, the 24-state parser with document/tag-directive scopes, and the loader
that builds the one-based node table with shared and cyclic alias edges. Each
layer transfers owned tokens, events and documents to the next and releases
them explicitly.

Both implementations read the identical vendored bytes. Every iteration parses
the three documents in the order recorded by `corpus/provenance.json` and hashes
every node; the corpus and iteration count are part of the benchmark definition,
so checksums and timings from a different corpus are not comparable.

The YIAN tree uses sibling indices and a tail index per node, so appending a
mapping/sequence child is O(1).

The reader decodes UTF-8 input in two ways: a maximal run of TAB, LF, CR or
printable ASCII bytes (0x20-0x7E) is reserved and copied into the decoded
buffer with one length update, while non-ASCII bytes and control bytes still
pass through the per-character decoder that validates encodings, character
classes, marks and reader errors. UTF-16 input always uses that decoder. The
scanner classifies blanks and breaks from one cached octet, and
`read_current` copies a single-byte printable character and advances the
reader position without re-reading its width; multi-byte characters keep the
width-driven path.

## Workload

- corpus: three real Kubernetes YAML documents from kube-prometheus
  (`kubernetes-alertmanagerconfig-crd.yaml`, `kubernetes-prometheus-rules.yaml`,
  `kubernetes-prometheus.yaml`);
- each file is a single YAML document; one iteration parses all three files in
  the recorded order and hashes every node;
- output: 32-bit checksum and total node count (specific to the corpus and
  iteration count);
- default scale: 64 iterations (fast: 8);
- the benchmark runner passes `--corpus <files>` plus the optional iteration
  count; the YIAN `// bench-scale` marker is also replaced by the runner.

### Corpus provenance

The corpus is vendored, not downloaded at runtime. `corpus/provenance.json`
records the upstream repository, commit
`b6c2f62b287524652592e6e2f7ea0d3054cb8ac6`, each upstream path and SHA-256, and
the Apache-2.0 license; `corpus/LICENSE` contains the license text. The files
are unmodified upstream YAML documents.

## Validation

Both binaries accept:

```text
--inspect tokens|events|documents --file PATH [--chunk-size N] [--fail-after N]
```

`--inspect` consumes the complete stream with a fresh parser and emits UTF-8
JSON Lines for the requested structure. `--chunk-size N` feeds the file through
a read callback in chunks of at most `N` bytes; `--fail-after N` requires a
positive chunk size and makes the callback deliver exactly `N` bytes before
reporting an input error. Exit codes are `0` for a completed observation, `1`
for a reader/scanner/parser/composer error, and `2` for usage, file or tool
errors.

The plain `--file PATH` mode parses the first document and prints its checksum
and node count. An empty stream prints `0 0` and exits 1. A document whose node
graph contains a cycle cannot be hashed as a tree: the program writes
`cannot compute tree checksum for cyclic document` to stderr and exits 3. Shared
(non-cyclic) nodes are still visited once per incoming edge. Documents returned
by `--inspect documents` may contain shared and cyclic graphs because the node
table enumerates nodes and edges rather than expanding aliases.

`tests/run_yaml_suite.py` drives the `--inspect` protocol over a
yaml-test-suite data checkout and writes independent reports. See
[tests/README.md](tests/README.md) for selectors, pass classification and
report fields. The runner does not build binaries; build the C and YIAN targets
first.

The YIAN entry point and the C harness keep the same parsing and traversal
workload: the vendored corpus and `--file` first-document path hash the same
nodes in the same order, and structural serialization never runs on the
benchmark path.

## Scope

This project covers the libyaml reader, scanner, parser and loader used by the
benchmark plus the independent compatibility runner. It does not provide the
emitter, dumper or writer, the public C API, or the upstream document-editing
constructors. Memory exhaustion follows the YIAN runtime rules; recoverable
out-of-memory handling is not part of this port. Input encoding selection,
character validity, escapes, tags, anchor reuse and error classification follow
the pinned upstream sources rather than a newer YAML specification revision.

## Source mapping

| YIAN module | Upstream source |
| --- | --- |
| `yian/src/yaml/types.an` | `yaml.h` mark, token-kind, event-kind, style, token, event and document-field declarations; `parser.c::MAX_NESTING_LEVEL` represented by the shared `Limits` object. |
| `yian/src/yaml/reader.an` | `reader.c::yaml_parser_determine_encoding`, `yaml_parser_update_raw_buffer`, `yaml_parser_update_buffer`; `yaml_private.h` character classes, `WIDTH`, `INPUT_RAW_BUFFER_SIZE`, `INPUT_BUFFER_SIZE` and `MAX_FILE_SIZE`; `scanner.c::SKIP` and `SKIP_LINE` advance the same cached character positions. |
| `yian/src/yaml/scanner.an` | `scanner.c` token fetchers and scanners (`fetch_more_tokens`, simple-key/indent handling, directives, anchors, tags, block/quoted/plain scalars); `yaml_private.h` `TOKEN_INIT`/`SCALAR_TOKEN_INIT` and queue/stack growth semantics; `SKIP`/`SKIP_LINE`/`READ`/`READ_LINE` character movement. |
| `yian/src/yaml/parser.an` | `parser.c` state dispatcher and all 24 state transitions, document start/end and directive processing, node properties and tag resolution; `PEEK_TOKEN`/`SKIP_TOKEN`; `MAX_NESTING_LEVEL` is the shared `Limits` object. |
| `yian/src/yaml/loader.an` | `loader.c::yaml_parser_load`, `yaml_parser_load_document`, `yaml_parser_load_nodes`, alias/anchor registration and `yaml_parser_load_{scalar,sequence,mapping,sequence_end,mapping_end}`; default `!!str`/`!!seq`/`!!map` tags and duplicate/undefined anchor composer errors; node tables keep the upstream one-based ids and append order. |

### Function and macro mapping

The tables below cover every parsing entry in the vendored reader, scanner,
parser and loader.  Names in the YIAN column are module functions or private
helpers; token/event initializer macros become direct `Token`/`Event` values
with a matching `TokenData`/`EventData` variant.

Reader and input:

| Upstream | YIAN |
| --- | --- |
| `api.c::yaml_parser_initialize`, `yaml_parser_delete`, `yaml_parser_set_input_string`, `yaml_parser_set_input`, `yaml_parser_set_encoding` | `reader_new`, `reader_delete`, `Input.Bytes`, `Input.Stream`, explicit `Encoding` argument. The public C API itself is not provided. |
| `reader.c::yaml_parser_determine_encoding` | `determine_encoding` |
| `reader.c::yaml_parser_update_raw_buffer` | `update_raw_buffer` |
| `reader.c::yaml_parser_update_buffer` | `reader_update_buffer` |
| `reader.c::yaml_parser_set_reader_error` | `fail` |
| `yaml_private.h` `INPUT_RAW_BUFFER_SIZE`, `INPUT_BUFFER_SIZE`, `MAX_FILE_SIZE` | `RAW_BUFFER_CAPACITY`, `BUFFER_CAPACITY`, `MAX_FILE_SIZE` |

`legal_ascii_run` and `append_ascii_run` have no upstream counterpart: they
copy a run of already-valid ASCII bytes into the decoded buffer in one
reservation, leaving non-ASCII and invalid bytes to `decode_char` and
`append_decoded`.

`Reader.cursor` is a native pointer to the decoded buffer at `position`. It is
rebuilt by `reader_new` and `reader_update_buffer` and advanced by
`reader_skip`, `reader_skip_line`, `read_current` and `read_line`. `byte_at`
reads through the cursor, so character access does not re-check the buffer
length; `reader.unread` is the availability contract and hot call sites compare
it directly before calling `cache`.

Scanner:

| Upstream | YIAN |
| --- | --- |
| `yaml_parser_scan` | `scanner_scan`, `scanner_scan_into`, `scanner_peek`, `scanner_ensure`, `scanner_head`, `scanner_skip` |
| `yaml_parser_fetch_more_tokens`, `yaml_parser_fetch_next_token` | `fetch_more_tokens`, `fetch_next_token` |
| `yaml_parser_set_scanner_error` | `set_scanner_error` |
| `yaml_parser_stale_simple_keys`, `yaml_parser_save_simple_key`, `yaml_parser_remove_simple_key` | `stale_simple_keys`, `save_simple_key`, `remove_simple_key` |
| `yaml_parser_increase_flow_level`, `yaml_parser_decrease_flow_level` | `increase_flow_level`, `decrease_flow_level` |
| `yaml_parser_roll_indent`, `yaml_parser_unroll_indent` | `roll_indent`, `unroll_indent` |
| `yaml_parser_fetch_stream_start`, `fetch_stream_end`, `fetch_directive`, `fetch_document_indicator`, `fetch_flow_collection_start`, `fetch_flow_collection_end`, `fetch_flow_entry`, `fetch_block_entry`, `fetch_key`, `fetch_value`, `fetch_anchor`, `fetch_tag`, `fetch_block_scalar`, `fetch_flow_scalar`, `fetch_plain_scalar` | Functions of the same names without the `yaml_parser_` prefix. |
| `yaml_parser_scan_to_next_token` | `scan_to_next_token` |
| `yaml_parser_scan_directive`, `scan_directive_name`, `scan_version_directive_value`, `scan_version_directive_number`, `scan_tag_directive_value` | Functions of the same names. |
| `yaml_parser_scan_anchor` | `scan_anchor` |
| `yaml_parser_scan_tag`, `scan_tag_handle`, `scan_tag_uri`, `scan_uri_escapes` | Functions of the same names. |
| `yaml_parser_scan_block_scalar`, `scan_block_scalar_breaks` | Functions of the same names. |
| `yaml_parser_scan_flow_scalar` | `scan_flow_scalar` |
| `yaml_parser_scan_plain_scalar` | `scan_plain_scalar` |
| `CACHE`, `SKIP`, `SKIP_LINE`, `READ`, `READ_LINE` | `cache`, `skip`, `skip_line`, `read_current`, `read_line` (character movement delegates to `reader_skip`/`reader_skip_line`; hot loops compare `unread` directly and call `cache` only when it is short). |
| `TOKEN_INIT` and the token initializer macros | `empty_token` plus `Token`/`TokenData` construction at the fetch site. |
| `CHECK`, `CHECK_AT`, `IS_*`, `AS_*`, `WIDTH` | `check`, `check_at`, `is_*`, `as_*`, `width_at`. |

`is_blank_at`, `is_breakz_at` and `is_blankz_at` test the ASCII candidates of
their upstream character-class macros with one `byte_at` read and keep the
multi-byte branches for C2 85 and E2 80 A8/A9. `read_current` copies a
single-byte printable character directly and advances the reader mark and
position as `reader_skip` would, without re-reading the octet. `scan_plain_scalar`
copies a run of ordinary printable ASCII with `plain_ascii_run` and
`append_plain_run` (one reservation, one mark advance), leaving `:` `#` `,` `[`
`]` `{` `}`, blanks, breaks and column-zero document indicators to the
character path.

Scanner internals report failure as `Result<_, i32>` and keep the first
`ParserError` in `Scanner.error`; token producers write into a `Token&` and
`fetch_*` enqueues that token. `scanner_skip` advances the queue head without
copying the consumed token.

Parser:

| Upstream | YIAN |
| --- | --- |
| `yaml_parser_parse` | `parser_parse`, `parser_parse_ref`, `parser_parse_into` |
| `yaml_parser_state_machine` | `state_machine` |
| `yaml_parser_set_parser_error`, `yaml_parser_set_parser_error_context` | `set_parser_error`, `set_parser_error_context` |
| `yaml_parser_parse_stream_start` | `parse_stream_start` |
| `yaml_parser_parse_document_start`, `parse_document_content`, `parse_document_end` | Functions of the same names. |
| `yaml_parser_parse_node` | `parse_node` |
| `yaml_parser_parse_block_sequence_entry`, `parse_indentless_sequence_entry`, `parse_block_mapping_key`, `parse_block_mapping_value` | Functions of the same names. |
| `yaml_parser_parse_flow_sequence_entry`, `parse_flow_sequence_entry_mapping_key/value/end`, `parse_flow_mapping_key`, `parse_flow_mapping_value` | Functions of the same names. |
| `yaml_parser_process_empty_scalar` | `empty_scalar_event` |
| `yaml_parser_process_directives`, `yaml_parser_append_tag_directive` | `process_directives`, `append_tag_directive` |
| `PEEK_TOKEN`, `SKIP_TOKEN` | `require_token` (`scanner_ensure` + `scanner_head`), `take_token` (`scanner_scan_into` into a `Token&`), `skip_token` (`scanner_skip`). |
| `MAX_NESTING_LEVEL`, `yaml_get_max_nest_level`, `yaml_set_max_nest_level` | `Limits` (`limits_new`, `limits_get`, `limits_set`) |
| Event initializer macros | `ok_event` plus `Event`/`EventData` construction at the state function. |

Parser state functions write the event into `Parser.event` through `ok_event`
and return a status with the first error kept in `Parser.error`.
`parser_parse_ref` exposes that slot to the loader without copying it;
`parser_parse_into` copies it for callers that need an owned event.

Loader:

| Upstream | YIAN |
| --- | --- |
| `yaml_parser_load` | `loader_load` (consumes `parser_parse_ref`) |
| `yaml_parser_load_document` | `loader_load` plus `load_nodes` |
| `yaml_parser_load_nodes` | `load_nodes` |
| `yaml_parser_load_alias` | Alias branch of `load_nodes` plus `add_node` |
| `yaml_parser_register_anchor` | `register_anchor` |
| `yaml_parser_load_node_add` | `add_node` |
| `yaml_parser_load_scalar`, `load_sequence`, `load_sequence_end`, `load_mapping`, `load_mapping_end` | Scalar, sequence-start, sequence-end, mapping-start and mapping-end branches of `load_nodes`. |
| `yaml_parser_set_composer_error`, `yaml_parser_set_composer_error_context` | `set_composer_error`, `set_composer_error_context` |
| `yaml_parser_delete_aliases` | `drop_aliases` |
| `yaml_document_delete` | `document_delete` |
| `yaml_document_get_node`, `yaml_document_get_root_node` | Direct one-based indexing of `Document.nodes`; a non-empty document's root is node 1. |
| `SCALAR_NODE_INIT`, `SEQUENCE_NODE_INIT`, `MAPPING_NODE_INIT` | `Node`/`NodeData` construction with the same tag defaults and marks. |

## Upstream

- project: libyaml
- version: 0.2.5
- commit: `90a56d4500aa1a1798514c5cb55c3ad4cb095f94`
- source: <https://github.com/yaml/libyaml>
- vendored files: `c/third_party/include/yaml.h`, `c/third_party/src/*.c`,
  `c/third_party/src/yaml_private.h`, `c/third_party/License` (MIT)
