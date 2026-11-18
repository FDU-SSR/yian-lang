// bench/fatptr/c/chase.c: authoritative C reference for chase.
//
// Same algorithm/scale as bench/fatptr/an/chase.an: chase a singly linked list with 16 B nodes.
// Node count is argv[1], defaulting to the YIAN marker value.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct Node {
    int64_t value;
    struct Node *next;
} Node;

int main(int argc, char **argv) {
    int64_t nodes = argc > 1 ? atoll(argv[1]) : 4000000;
    const int64_t rounds = 8;
    Node *head = NULL;
    for (int64_t i = nodes; i > 0; i--) {
        Node *node = malloc(sizeof(Node));
        if (node == NULL) {
            return 2;
        }
        node->value = i;
        node->next = head;
        head = node;
    }
    int64_t acc = 0;
    for (int64_t round = 0; round < rounds; round++) {
        for (Node *cur = head; cur != NULL; cur = cur->next) {
            acc += cur->value;
        }
    }
    assert(acc == rounds * (nodes * (nodes + 1) / 2) && "chase checksum");
    printf("chase pass\n");
    while (head != NULL) {
        Node *next = head->next;
        free(head);
        head = next;
    }
    return 0;
}
