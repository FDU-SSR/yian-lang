#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "inspect.h"
#include "yaml.h"

static uint64_t update(uint64_t hash, const unsigned char *bytes, size_t length) {
    for (size_t i = 0; i < length; i++) {
        hash = (hash * 31u + bytes[i]) & 0xFFFFFFFFu;
    }
    return hash;
}

static void walk_node(yaml_document_t *document, yaml_node_t *node, uint64_t *hash, uint64_t *nodes) {
    uint64_t kind = node->type == YAML_MAPPING_NODE ? 1u : (node->type == YAML_SEQUENCE_NODE ? 2u : 3u);
    *hash = (*hash * 31u + kind) & 0xFFFFFFFFu;
    *nodes += 1u;
    if (node->type == YAML_SCALAR_NODE) {
        *hash = update(*hash, node->data.scalar.value, node->data.scalar.length);
        return;
    }
    if (node->type == YAML_SEQUENCE_NODE) {
        for (yaml_node_item_t *item = node->data.sequence.items.start;
             item < node->data.sequence.items.top; item++) {
            walk_node(document, yaml_document_get_node(document, *item), hash, nodes);
        }
        return;
    }
    for (yaml_node_pair_t *pair = node->data.mapping.pairs.start;
         pair < node->data.mapping.pairs.top; pair++) {
        walk_node(document, yaml_document_get_node(document, pair->key), hash, nodes);
        walk_node(document, yaml_document_get_node(document, pair->value), hash, nodes);
    }
}

/* Depth-first checksum with an explicit current-path mark.  Re-entering a node
 * that is still on the path is a cycle; revisiting a completed shared node is
 * not. */
static int walk_node_checked(yaml_document_t *document, yaml_node_t *node,
        uint64_t *hash, uint64_t *nodes, unsigned char *on_path) {
    size_t index = (size_t)(node - document->nodes.start) + 1u;
    if (on_path[index]) {
        return 0;
    }
    on_path[index] = 1u;
    uint64_t kind = node->type == YAML_MAPPING_NODE ? 1u : (node->type == YAML_SEQUENCE_NODE ? 2u : 3u);
    *hash = (*hash * 31u + kind) & 0xFFFFFFFFu;
    *nodes += 1u;
    if (node->type == YAML_SCALAR_NODE) {
        *hash = update(*hash, node->data.scalar.value, node->data.scalar.length);
        on_path[index] = 0u;
        return 1;
    }
    if (node->type == YAML_SEQUENCE_NODE) {
        for (yaml_node_item_t *item = node->data.sequence.items.start;
             item < node->data.sequence.items.top; item++) {
            if (!walk_node_checked(document, yaml_document_get_node(document, *item),
                        hash, nodes, on_path)) {
                return 0;
            }
        }
        on_path[index] = 0u;
        return 1;
    }
    for (yaml_node_pair_t *pair = node->data.mapping.pairs.start;
         pair < node->data.mapping.pairs.top; pair++) {
        if (!walk_node_checked(document, yaml_document_get_node(document, pair->key),
                    hash, nodes, on_path)) {
            return 0;
        }
        if (!walk_node_checked(document, yaml_document_get_node(document, pair->value),
                    hash, nodes, on_path)) {
            return 0;
        }
    }
    on_path[index] = 0u;
    return 1;
}

static int parse_once_checked(const char *text, size_t length, uint64_t *hash,
        uint64_t *nodes, int *cyclic) {
    yaml_parser_t parser;
    yaml_document_t document;
    *cyclic = 0;
    if (!yaml_parser_initialize(&parser)) {
        return 0;
    }
    yaml_parser_set_input_string(&parser, (const unsigned char *)text, length);
    if (!yaml_parser_load(&parser, &document)) {
        yaml_parser_delete(&parser);
        return 0;
    }
    yaml_node_t *root = yaml_document_get_root_node(&document);
    if (root == NULL) {
        yaml_document_delete(&document);
        yaml_parser_delete(&parser);
        return 0;
    }
    size_t count = (size_t)(document.nodes.top - document.nodes.start);
    unsigned char *on_path = (unsigned char *)calloc(count + 1u, 1u);
    if (on_path == NULL) {
        yaml_document_delete(&document);
        yaml_parser_delete(&parser);
        return 0;
    }
    int ok = walk_node_checked(&document, root, hash, nodes, on_path);
    free(on_path);
    yaml_document_delete(&document);
    yaml_parser_delete(&parser);
    if (!ok) {
        *cyclic = 1;
    }
    return ok;
}

static int parse_once(const char *text, size_t length, uint64_t *hash, uint64_t *nodes) {
    yaml_parser_t parser;
    yaml_document_t document;
    if (!yaml_parser_initialize(&parser)) {
        return 0;
    }
    yaml_parser_set_input_string(&parser, (const unsigned char *)text, length);
    if (!yaml_parser_load(&parser, &document)) {
        yaml_parser_delete(&parser);
        return 0;
    }
    yaml_node_t *root = yaml_document_get_root_node(&document);
    if (root == NULL) {
        yaml_document_delete(&document);
        yaml_parser_delete(&parser);
        return 0;
    }
    walk_node(&document, root, hash, nodes);
    yaml_document_delete(&document);
    yaml_parser_delete(&parser);
    return 1;
}

static int parse_file(const char *path) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return 1;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return 1;
    }
    long size = ftell(file);
    if (size < 0) {
        fclose(file);
        return 1;
    }
    rewind(file);
    char *text = (char *)malloc((size_t)size + 1u);
    if (text == NULL) {
        fclose(file);
        return 1;
    }
    size_t check = fread(text, 1u, (size_t)size, file);
    fclose(file);
    if (check != (size_t)size) {
        free(text);
        return 1;
    }
    uint64_t hash = 0u;
    uint64_t nodes = 0u;
    int cyclic = 0;
    int ok = parse_once_checked(text, (size_t)size, &hash, &nodes, &cyclic);
    if (cyclic) {
        fprintf(stderr, "cannot compute tree checksum for cyclic document\n");
        free(text);
        return 3;
    }
    printf("%llu %llu\n", (unsigned long long)hash, (unsigned long long)nodes);
    free(text);
    return ok ? 0 : 1;
}

static int read_file(const char *path, char **text, size_t *length) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return 0;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return 0;
    }
    long size = ftell(file);
    if (size < 0) {
        fclose(file);
        return 0;
    }
    rewind(file);
    char *buffer = (char *)malloc((size_t)size + 1u);
    if (buffer == NULL) {
        fclose(file);
        return 0;
    }
    size_t check = fread(buffer, 1u, (size_t)size, file);
    fclose(file);
    if (check != (size_t)size) {
        free(buffer);
        return 0;
    }
    buffer[size] = '\0';
    *text = buffer;
    *length = (size_t)size;
    return 1;
}

static int parse_number(const char *text, unsigned long long *value);

static int parse_corpus(int argc, char **argv) {
    int iterations = 64;
    int path_count = argc - 2;
    unsigned long long value = 0u;
    if (path_count > 0 && parse_number(argv[argc - 1], &value)) {
        if (value > 0x7FFFFFFFull) {
            return 2;
        }
        iterations = (int)value;
        path_count -= 1;
    }
    if (path_count <= 0) {
        fprintf(stderr, "corpus: at least one YAML file is required\n");
        return 2;
    }
    char **texts = (char **)calloc((size_t)path_count, sizeof(char *));
    size_t *lengths = (size_t *)calloc((size_t)path_count, sizeof(size_t));
    if (texts == NULL || lengths == NULL) {
        free(texts);
        free(lengths);
        return 1;
    }
    int loaded = 0;
    for (int index = 0; index < path_count; index++) {
        if (!read_file(argv[2 + index], &texts[index], &lengths[index])) {
            fprintf(stderr, "corpus: cannot read %s\n", argv[2 + index]);
            break;
        }
        loaded += 1;
    }
    uint64_t hash = 0u;
    uint64_t nodes = 0u;
    int ok = loaded == path_count;
    for (int iteration = 0; ok && iteration < iterations; iteration++) {
        for (int index = 0; index < path_count; index++) {
            if (!parse_once(texts[index], lengths[index], &hash, &nodes)) {
                ok = 0;
                break;
            }
        }
    }
    for (int index = 0; index < loaded; index++) {
        free(texts[index]);
    }
    free(texts);
    free(lengths);
    if (!ok) {
        return 1;
    }
    printf("%llu %llu\n", (unsigned long long)hash, (unsigned long long)nodes);
    return 0;
}

static int parse_number(const char *text, unsigned long long *value) {
    unsigned long long result = 0u;
    const char *cursor = text;
    if (cursor == NULL || *cursor == '\0') {
        return 0;
    }
    while (*cursor != '\0') {
        if (*cursor < '0' || *cursor > '9') {
            return 0;
        }
        if (result > (0xFFFFFFFFFFFFFFFFull - (unsigned long long)(*cursor - '0')) / 10ull) {
            return 0;
        }
        result = result * 10ull + (unsigned long long)(*cursor - '0');
        cursor++;
    }
    *value = result;
    return 1;
}

static int inspect_mode(int argc, char **argv) {
    const char *mode;
    const char *path = NULL;
    size_t chunk_size = 0u;
    long long fail_after = -1;
    int index;

    if (argc < 3) {
        fprintf(stderr, "inspect: --inspect requires tokens, events or documents\n");
        return 2;
    }
    mode = argv[2];

    for (index = 3; index < argc; index++) {
        if (strcmp(argv[index], "--file") == 0) {
            if (index + 1 >= argc) {
                fprintf(stderr, "inspect: --file requires a path\n");
                return 2;
            }
            path = argv[++index];
        } else if (strcmp(argv[index], "--chunk-size") == 0) {
            unsigned long long value = 0u;
            if (index + 1 >= argc || !parse_number(argv[++index], &value)) {
                fprintf(stderr, "inspect: --chunk-size requires a non-negative integer\n");
                return 2;
            }
            chunk_size = (size_t)value;
        } else if (strcmp(argv[index], "--fail-after") == 0) {
            unsigned long long value = 0u;
            if (index + 1 >= argc) {
                fprintf(stderr, "inspect: --fail-after requires an integer\n");
                return 2;
            }
            index++;
            if (strcmp(argv[index], "-1") == 0) {
                fail_after = -1;
            } else if (!parse_number(argv[index], &value)) {
                fprintf(stderr, "inspect: --fail-after requires -1 or a non-negative integer\n");
                return 2;
            } else {
                fail_after = (long long)value;
            }
        } else {
            fprintf(stderr, "inspect: unknown argument %s\n", argv[index]);
            return 2;
        }
    }
    if (path == NULL) {
        fprintf(stderr, "inspect: --file PATH is required\n");
        return 2;
    }
    return yaml_bench_inspect(mode, path, chunk_size, fail_after);
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--inspect") == 0) {
        return inspect_mode(argc, argv);
    }
    if (argc > 2 && strcmp(argv[1], "--file") == 0) {
        return parse_file(argv[2]);
    }
    if (argc > 2 && strcmp(argv[1], "--corpus") == 0) {
        return parse_corpus(argc, argv);
    }
    fprintf(stderr, "usage: %s --inspect MODE --file PATH | --file PATH | --corpus PATH [PATH ...] [ITERATIONS]\n", argv[0]);
    return 2;
}
