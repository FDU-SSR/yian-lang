#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <assert.h>

// Linked-list node struct.
typedef struct Element {
    int val;
    struct Element* next;
} Element;

// Create a linked list.
Element* Element_make_list(int length) {
    if (length == 0) {
        return NULL;
    }
    Element* rest_of_list = Element_make_list(length - 1);
    Element* e = (Element*)malloc(sizeof(Element));
    e->val = length;
    e->next = rest_of_list;
    return e;
}

// Compute list length.
int Element_length_method(Element* self) {
    return self->next == NULL ? 1 : 1 + Element_length_method(self->next);
}

// Release the list except its head.
void Element_free_method(Element* self) {
    Element* current = self->next;
    while (current != NULL) {
        Element* next = current->next;
        free(current);
        current = next;
    }
}

// Compute list length (external function).
int Element_length(Element* e) {
    if (e == NULL) {
        return 0;
    }
    return Element_length_method(e);
}

// Test whether list x is shorter than list y.
bool is_shorter_than(Element* x, Element* y) {
    Element* x_tail = x;
    Element* y_tail = y;

    while (y_tail != NULL) {
        if (x_tail == NULL) {
            return true;
        }
        x_tail = x_tail->next;
        y_tail = y_tail->next;
    }
    return false;
}

// Recursive tail function.
Element* tail(Element* x, Element* y, Element* z) {
    if (is_shorter_than(y, x)) {
        Element* next_x = (x != NULL) ? x->next : NULL;
        Element* next_y = y->next;
        Element* next_z = (z != NULL) ? z->next : NULL;

        return tail(
            tail(next_x, y, z),
            tail(next_y, z, x),
            tail(next_z, x, y)
        );
    }

    return z;
}

// Release the entire list, including its head.
void free_list(Element* e) {
    if (e == NULL) {
        return;
    }
    Element_free_method(e);
    free(e);
}

// Benchmark function.
int benchmark_list() {
    Element* x = Element_make_list(15);
    Element* y = Element_make_list(10);
    Element* z = Element_make_list(6);

    Element* result = tail(x, y, z);
    int l = Element_length(result);

    free_list(x);
    free_list(y);
    free_list(z);

    return l;
}

// main function.
int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 94077;
    for (int i = 0; i < iterations; i++) {
        int result = benchmark_list();
        assert(result == 10 && "tail result length should be 10");
    }
    return 0;
}
