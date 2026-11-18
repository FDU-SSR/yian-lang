#include "inspect.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "yaml.h"

/* One file delivered through the callback input, with optional fault injection. */
typedef struct inspect_input_s {
    const unsigned char *data;
    size_t length;
    size_t position;
    size_t chunk_size;
    long long fail_after;
} inspect_input_t;

static int inspect_read(void *context, unsigned char *buffer, size_t size,
                        size_t *length) {
    inspect_input_t *input = (inspect_input_t *)context;
    size_t remaining = input->position < input->length
                           ? input->length - input->position
                           : 0u;
    size_t count = size < input->chunk_size ? size : input->chunk_size;
    if (count > remaining) {
        count = remaining;
    }
    if (input->fail_after >= 0) {
        size_t delivered = input->position;
        size_t allowed = (size_t)input->fail_after > delivered
                             ? (size_t)input->fail_after - delivered
                             : 0u;
        if (allowed < count) {
            count = allowed;
        }
    }
    if (count == 0u) {
        if (input->fail_after >= 0 && input->position < input->length
                && input->position >= (size_t)input->fail_after) {
            return 0;
        }
        *length = 0u;
        return 1;
    }
    memcpy(buffer, input->data + input->position, count);
    input->position += count;
    *length = count;
    return 1;
}

static void print_hex(const unsigned char *bytes, size_t length) {
    static const char digits[] = "0123456789abcdef";
    size_t index;
    putchar('"');
    for (index = 0u; index < length; index++) {
        putchar(digits[(bytes[index] >> 4) & 0xFu]);
        putchar(digits[bytes[index] & 0xFu]);
    }
    putchar('"');
}

static void print_hex_string(const unsigned char *text) {
    if (text == NULL) {
        print_hex(NULL, 0u);
        return;
    }
    print_hex(text, strlen((const char *)text));
}

static void print_optional_hex(const unsigned char *text) {
    if (text == NULL) {
        fputs("null", stdout);
        return;
    }
    print_hex_string(text);
}

static void print_json_string(const char *text) {
    const unsigned char *cursor = (const unsigned char *)(text ? text : "");
    putchar('"');
    while (*cursor != 0u) {
        unsigned char byte = *cursor++;
        switch (byte) {
            case '"': fputs("\\\"", stdout); break;
            case '\\': fputs("\\\\", stdout); break;
            case '\b': fputs("\\b", stdout); break;
            case '\f': fputs("\\f", stdout); break;
            case '\n': fputs("\\n", stdout); break;
            case '\r': fputs("\\r", stdout); break;
            case '\t': fputs("\\t", stdout); break;
            default:
                if (byte < 0x20u) {
                    printf("\\u%04x", (unsigned)byte);
                } else {
                    putchar((int)byte);
                }
                break;
        }
    }
    putchar('"');
}

static void print_mark(yaml_mark_t mark) {
    printf("[%llu,%llu,%llu]", (unsigned long long)mark.index,
           (unsigned long long)mark.line, (unsigned long long)mark.column);
}

static const char *encoding_name(yaml_encoding_t encoding) {
    switch (encoding) {
        case YAML_UTF8_ENCODING: return "Utf8";
        case YAML_UTF16LE_ENCODING: return "Utf16Le";
        case YAML_UTF16BE_ENCODING: return "Utf16Be";
        default: return "Any";
    }
}

static const char *scalar_style_name(yaml_scalar_style_t style) {
    switch (style) {
        case YAML_PLAIN_SCALAR_STYLE: return "Plain";
        case YAML_SINGLE_QUOTED_SCALAR_STYLE: return "SingleQuoted";
        case YAML_DOUBLE_QUOTED_SCALAR_STYLE: return "DoubleQuoted";
        case YAML_LITERAL_SCALAR_STYLE: return "Literal";
        case YAML_FOLDED_SCALAR_STYLE: return "Folded";
        default: return "Any";
    }
}

static const char *sequence_style_name(yaml_sequence_style_t style) {
    switch (style) {
        case YAML_BLOCK_SEQUENCE_STYLE: return "Block";
        case YAML_FLOW_SEQUENCE_STYLE: return "Flow";
        default: return "Any";
    }
}

static const char *mapping_style_name(yaml_mapping_style_t style) {
    switch (style) {
        case YAML_BLOCK_MAPPING_STYLE: return "Block";
        case YAML_FLOW_MAPPING_STYLE: return "Flow";
        default: return "Any";
    }
}

static const char *token_name(yaml_token_type_t type) {
    switch (type) {
        case YAML_STREAM_START_TOKEN: return "StreamStart";
        case YAML_STREAM_END_TOKEN: return "StreamEnd";
        case YAML_VERSION_DIRECTIVE_TOKEN: return "VersionDirective";
        case YAML_TAG_DIRECTIVE_TOKEN: return "TagDirective";
        case YAML_DOCUMENT_START_TOKEN: return "DocumentStart";
        case YAML_DOCUMENT_END_TOKEN: return "DocumentEnd";
        case YAML_BLOCK_SEQUENCE_START_TOKEN: return "BlockSequenceStart";
        case YAML_BLOCK_MAPPING_START_TOKEN: return "BlockMappingStart";
        case YAML_BLOCK_END_TOKEN: return "BlockEnd";
        case YAML_FLOW_SEQUENCE_START_TOKEN: return "FlowSequenceStart";
        case YAML_FLOW_SEQUENCE_END_TOKEN: return "FlowSequenceEnd";
        case YAML_FLOW_MAPPING_START_TOKEN: return "FlowMappingStart";
        case YAML_FLOW_MAPPING_END_TOKEN: return "FlowMappingEnd";
        case YAML_BLOCK_ENTRY_TOKEN: return "BlockEntry";
        case YAML_FLOW_ENTRY_TOKEN: return "FlowEntry";
        case YAML_KEY_TOKEN: return "Key";
        case YAML_VALUE_TOKEN: return "Value";
        case YAML_ALIAS_TOKEN: return "Alias";
        case YAML_ANCHOR_TOKEN: return "Anchor";
        case YAML_TAG_TOKEN: return "Tag";
        case YAML_SCALAR_TOKEN: return "Scalar";
        default: return "None";
    }
}

static const char *event_name(yaml_event_type_t type) {
    switch (type) {
        case YAML_STREAM_START_EVENT: return "StreamStart";
        case YAML_STREAM_END_EVENT: return "StreamEnd";
        case YAML_DOCUMENT_START_EVENT: return "DocumentStart";
        case YAML_DOCUMENT_END_EVENT: return "DocumentEnd";
        case YAML_ALIAS_EVENT: return "Alias";
        case YAML_SCALAR_EVENT: return "Scalar";
        case YAML_SEQUENCE_START_EVENT: return "SequenceStart";
        case YAML_SEQUENCE_END_EVENT: return "SequenceEnd";
        case YAML_MAPPING_START_EVENT: return "MappingStart";
        case YAML_MAPPING_END_EVENT: return "MappingEnd";
        default: return "None";
    }
}

static const char *error_name(yaml_error_type_t error) {
    switch (error) {
        case YAML_READER_ERROR: return "Reader";
        case YAML_SCANNER_ERROR: return "Scanner";
        case YAML_PARSER_ERROR: return "Parser";
        case YAML_COMPOSER_ERROR: return "Composer";
        default: return NULL;
    }
}

static void print_token(const yaml_token_t *token) {
    fputs("{\"record\":\"token\",\"kind\":\"", stdout);
    fputs(token_name(token->type), stdout);
    fputs("\",\"start\":", stdout);
    print_mark(token->start_mark);
    fputs(",\"end\":", stdout);
    print_mark(token->end_mark);
    fputs(",\"data\":", stdout);
    switch (token->type) {
        case YAML_STREAM_START_TOKEN:
            printf("{\"encoding\":\"%s\"}", encoding_name(token->data.stream_start.encoding));
            break;
        case YAML_VERSION_DIRECTIVE_TOKEN:
            printf("{\"value\":{\"major\":%d,\"minor\":%d}}",
                   token->data.version_directive.major,
                   token->data.version_directive.minor);
            break;
        case YAML_TAG_DIRECTIVE_TOKEN:
            fputs("{\"value\":{\"handle\":", stdout);
            print_hex_string(token->data.tag_directive.handle);
            fputs(",\"prefix\":", stdout);
            print_hex_string(token->data.tag_directive.prefix);
            fputs("}}", stdout);
            break;
        case YAML_ALIAS_TOKEN:
            fputs("{\"value\":", stdout);
            print_hex_string(token->data.alias.value);
            fputc('}', stdout);
            break;
        case YAML_ANCHOR_TOKEN:
            fputs("{\"value\":", stdout);
            print_hex_string(token->data.anchor.value);
            fputc('}', stdout);
            break;
        case YAML_TAG_TOKEN:
            fputs("{\"handle\":", stdout);
            print_hex_string(token->data.tag.handle);
            fputs(",\"suffix\":", stdout);
            print_hex_string(token->data.tag.suffix);
            fputc('}', stdout);
            break;
        case YAML_SCALAR_TOKEN:
            fputs("{\"value\":", stdout);
            print_hex(token->data.scalar.value, token->data.scalar.length);
            printf(",\"style\":\"%s\"}", scalar_style_name(token->data.scalar.style));
            break;
        default:
            fputs("{}", stdout);
            break;
    }
    fputs("}\n", stdout);
}

static void print_event(const yaml_event_t *event) {
    fputs("{\"record\":\"event\",\"kind\":\"", stdout);
    fputs(event_name(event->type), stdout);
    fputs("\",\"start\":", stdout);
    print_mark(event->start_mark);
    fputs(",\"end\":", stdout);
    print_mark(event->end_mark);
    fputs(",\"data\":", stdout);
    switch (event->type) {
        case YAML_STREAM_START_EVENT:
            printf("{\"encoding\":\"%s\"}", encoding_name(event->data.stream_start.encoding));
            break;
        case YAML_DOCUMENT_START_EVENT: {
            yaml_tag_directive_t *directive;
            const yaml_version_directive_t *version = event->data.document_start.version_directive;
            fputs("{\"version\":", stdout);
            if (version == NULL) {
                fputs("null", stdout);
            } else {
                printf("{\"major\":%d,\"minor\":%d}", version->major, version->minor);
            }
            fputs(",\"tags\":[", stdout);
            for (directive = event->data.document_start.tag_directives.start;
                 directive < event->data.document_start.tag_directives.end; directive++) {
                if (directive != event->data.document_start.tag_directives.start) {
                    fputc(',', stdout);
                }
                fputs("{\"handle\":", stdout);
                print_hex_string(directive->handle);
                fputs(",\"prefix\":", stdout);
                print_hex_string(directive->prefix);
                fputc('}', stdout);
            }
            printf("],\"implicit\":%s}", event->data.document_start.implicit ? "true" : "false");
            break;
        }
        case YAML_DOCUMENT_END_EVENT:
            printf("{\"implicit\":%s}", event->data.document_end.implicit ? "true" : "false");
            break;
        case YAML_ALIAS_EVENT:
            fputs("{\"anchor\":", stdout);
            print_hex_string(event->data.alias.anchor);
            fputc('}', stdout);
            break;
        case YAML_SCALAR_EVENT:
            fputs("{\"anchor\":", stdout);
            print_optional_hex(event->data.scalar.anchor);
            fputs(",\"tag\":", stdout);
            print_optional_hex(event->data.scalar.tag);
            fputs(",\"value\":", stdout);
            print_hex(event->data.scalar.value, event->data.scalar.length);
            printf(",\"plain_implicit\":%s,\"quoted_implicit\":%s,\"style\":\"%s\"}",
                   event->data.scalar.plain_implicit ? "true" : "false",
                   event->data.scalar.quoted_implicit ? "true" : "false",
                   scalar_style_name(event->data.scalar.style));
            break;
        case YAML_SEQUENCE_START_EVENT:
            fputs("{\"anchor\":", stdout);
            print_optional_hex(event->data.sequence_start.anchor);
            fputs(",\"tag\":", stdout);
            print_optional_hex(event->data.sequence_start.tag);
            printf(",\"implicit\":%s,\"style\":\"%s\"}",
                   event->data.sequence_start.implicit ? "true" : "false",
                   sequence_style_name(event->data.sequence_start.style));
            break;
        case YAML_MAPPING_START_EVENT:
            fputs("{\"anchor\":", stdout);
            print_optional_hex(event->data.mapping_start.anchor);
            fputs(",\"tag\":", stdout);
            print_optional_hex(event->data.mapping_start.tag);
            printf(",\"implicit\":%s,\"style\":\"%s\"}",
                   event->data.mapping_start.implicit ? "true" : "false",
                   mapping_style_name(event->data.mapping_start.style));
            break;
        default:
            fputs("{}", stdout);
            break;
    }
    fputs("}\n", stdout);
}

static void print_document(const yaml_document_t *document, unsigned long long number) {
    yaml_node_t *node = NULL;
    size_t count = (size_t)(document->nodes.top - document->nodes.start);
    size_t index;
    yaml_tag_directive_t *directive;

    fputs("{\"record\":\"document\",\"number\":", stdout);
    printf("%llu", number);
    fputs(",\"version\":", stdout);
    if (document->version_directive == NULL) {
        fputs("null", stdout);
    } else {
        printf("{\"major\":%d,\"minor\":%d}", document->version_directive->major,
               document->version_directive->minor);
    }
    fputs(",\"tags\":[", stdout);
    for (directive = document->tag_directives.start;
         directive < document->tag_directives.end; directive++) {
        if (directive != document->tag_directives.start) {
            fputc(',', stdout);
        }
        fputs("{\"handle\":", stdout);
        print_hex_string(directive->handle);
        fputs(",\"prefix\":", stdout);
        print_hex_string(directive->prefix);
        fputc('}', stdout);
    }
    printf("],\"start_implicit\":%s,\"end_implicit\":%s,\"start\":",
           document->start_implicit ? "true" : "false",
           document->end_implicit ? "true" : "false");
    print_mark(document->start_mark);
    fputs(",\"end\":", stdout);
    print_mark(document->end_mark);
    fputs(",\"root\":1,\"nodes\":[", stdout);
    for (index = 0u; index < count; index++) {
        node = document->nodes.start + index;
        if (index != 0u) {
            fputc(',', stdout);
        }
        printf("{\"id\":%llu,\"kind\":", (unsigned long long)(index + 1u));
        if (node->type == YAML_SCALAR_NODE) {
            fputs("\"Scalar\",\"tag\":", stdout);
            print_hex_string(node->tag);
            fputs(",\"start\":", stdout);
            print_mark(node->start_mark);
            fputs(",\"end\":", stdout);
            print_mark(node->end_mark);
            fputs(",\"data\":{\"value\":", stdout);
            print_hex(node->data.scalar.value, node->data.scalar.length);
            printf(",\"style\":\"%s\"}}", scalar_style_name(node->data.scalar.style));
        } else if (node->type == YAML_SEQUENCE_NODE) {
            yaml_node_item_t *item;
            fputs("\"Sequence\",\"tag\":", stdout);
            print_hex_string(node->tag);
            fputs(",\"start\":", stdout);
            print_mark(node->start_mark);
            fputs(",\"end\":", stdout);
            print_mark(node->end_mark);
            fputs(",\"data\":{\"items\":[", stdout);
            for (item = node->data.sequence.items.start;
                 item < node->data.sequence.items.top; item++) {
                if (item != node->data.sequence.items.start) {
                    fputc(',', stdout);
                }
                printf("%d", *item);
            }
            printf("],\"style\":\"%s\"}}", sequence_style_name(node->data.sequence.style));
        } else if (node->type == YAML_MAPPING_NODE) {
            yaml_node_pair_t *pair;
            fputs("\"Mapping\",\"tag\":", stdout);
            print_hex_string(node->tag);
            fputs(",\"start\":", stdout);
            print_mark(node->start_mark);
            fputs(",\"end\":", stdout);
            print_mark(node->end_mark);
            fputs(",\"data\":{\"pairs\":[", stdout);
            for (pair = node->data.mapping.pairs.start;
                 pair < node->data.mapping.pairs.top; pair++) {
                if (pair != node->data.mapping.pairs.start) {
                    fputc(',', stdout);
                }
                printf("{\"key\":%d,\"value\":%d}", pair->key, pair->value);
            }
            printf("],\"style\":\"%s\"}}", mapping_style_name(node->data.mapping.style));
        } else {
            fputs("\"None\",\"tag\":\"\",\"start\":[0,0,0],\"end\":[0,0,0],\"data\":{}}", stdout);
        }
    }
    fputs("]}\n", stdout);
}

static void print_error_record(const yaml_parser_t *parser) {
    const char *kind = error_name(parser->error);
    fputs("{\"record\":\"error\",\"kind\":\"", stdout);
    fputs(kind ? kind : "Reader", stdout);
    fputs("\",\"problem\":", stdout);
    print_mark(parser->problem_mark);
    fputs(",\"context\":", stdout);
    print_mark(parser->context_mark);
    fputs(",\"problem_text\":", stdout);
    print_json_string(parser->problem);
    fputs(",\"context_text\":", stdout);
    print_json_string(parser->context);
    printf(",\"problem_offset\":%llu,\"problem_value\":%d}\n",
           (unsigned long long)parser->problem_offset, parser->problem_value);
}

static int inspect_tokens(yaml_parser_t *parser) {
    for (;;) {
        yaml_token_t token;
        if (!yaml_parser_scan(parser, &token)) {
            return 1;
        }
        print_token(&token);
        if (token.type == YAML_STREAM_END_TOKEN) {
            yaml_token_delete(&token);
            break;
        }
        yaml_token_delete(&token);
    }
    return 0;
}

static int inspect_events(yaml_parser_t *parser) {
    for (;;) {
        yaml_event_t event;
        if (!yaml_parser_parse(parser, &event)) {
            return 1;
        }
        print_event(&event);
        if (event.type == YAML_STREAM_END_EVENT) {
            yaml_event_delete(&event);
            break;
        }
        yaml_event_delete(&event);
    }
    return 0;
}

static int inspect_documents(yaml_parser_t *parser) {
    unsigned long long number = 0u;
    for (;;) {
        yaml_document_t document;
        size_t count;
        if (!yaml_parser_load(parser, &document)) {
            return 1;
        }
        count = (size_t)(document.nodes.top - document.nodes.start);
        if (count == 0u) {
            yaml_document_delete(&document);
            break;
        }
        number += 1u;
        print_document(&document, number);
        yaml_document_delete(&document);
        if (parser->stream_end_produced) {
            break;
        }
    }
    return 0;
}

int yaml_bench_inspect(const char *mode, const char *path, size_t chunk_size,
                       long long fail_after) {
    FILE *file = NULL;
    unsigned char *data = NULL;
    long size = 0;
    size_t check = 0u;
    inspect_input_t input;
    yaml_parser_t parser;
    int result = 2;
    int observed = 0;
    int mode_tokens;
    int mode_events;
    int mode_documents;
    int initialized = 0;

    if (mode == NULL || path == NULL) {
        fprintf(stderr, "inspect: missing mode or path\n");
        return 2;
    }
    mode_tokens = strcmp(mode, "tokens") == 0;
    mode_events = strcmp(mode, "events") == 0;
    mode_documents = strcmp(mode, "documents") == 0;
    if (!mode_tokens && !mode_events && !mode_documents) {
        fprintf(stderr, "inspect: unsupported mode %s\n", mode);
        return 2;
    }
    if (chunk_size == 0u && fail_after >= 0) {
        fprintf(stderr, "inspect: --fail-after requires a positive --chunk-size\n");
        return 2;
    }

    file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "inspect: cannot open %s\n", path);
        return 2;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fprintf(stderr, "inspect: cannot seek %s\n", path);
        fclose(file);
        return 2;
    }
    size = ftell(file);
    if (size < 0) {
        fprintf(stderr, "inspect: cannot measure %s\n", path);
        fclose(file);
        return 2;
    }
    rewind(file);
    data = (unsigned char *)malloc((size_t)size + 1u);
    if (data == NULL) {
        fprintf(stderr, "inspect: out of memory\n");
        fclose(file);
        return 2;
    }
    check = fread(data, 1u, (size_t)size, file);
    fclose(file);
    if (check != (size_t)size) {
        fprintf(stderr, "inspect: cannot read %s\n", path);
        free(data);
        return 2;
    }

    memset(&input, 0, sizeof(input));
    input.data = data;
    input.length = (size_t)size;
    input.chunk_size = chunk_size;
    input.fail_after = fail_after;

    memset(&parser, 0, sizeof(parser));
    if (!yaml_parser_initialize(&parser)) {
        fprintf(stderr, "inspect: cannot initialize parser\n");
        free(data);
        return 2;
    }
    initialized = 1;
    if (chunk_size == 0u) {
        yaml_parser_set_input_string(&parser, data, (size_t)size);
    } else {
        yaml_parser_set_input(&parser, inspect_read, &input);
    }

    printf("{\"schema\":\"yaml-compat-v1\",\"mode\":\"%s\"}\n", mode);
    if (mode_tokens) {
        observed = inspect_tokens(&parser);
    } else if (mode_events) {
        observed = inspect_events(&parser);
    } else {
        observed = inspect_documents(&parser);
    }
    if (observed != 0) {
        const char *kind = error_name(parser.error);
        if (kind == NULL) {
            fprintf(stderr, "inspect: parser stopped without a reader/scanner/parser/composer error\n");
            result = 2;
        } else {
            print_error_record(&parser);
            result = 1;
        }
    } else {
        fputs("{\"record\":\"end\"}\n", stdout);
        result = 0;
    }

    if (initialized) {
        yaml_parser_delete(&parser);
    }
    free(data);
    return result;
}
