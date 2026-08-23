#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <assert.h>

// 链表节点结构体
typedef struct Element {
    int val;
    struct Element* next;
} Element;

// 创建链表
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

// 计算链表长度
int Element_length_method(Element* self) {
    int len = 1;
    Element* current = self->next;
    while (current != NULL) {
        len += 1;
        current = current->next;
    }
    return len;
}

// 释放链表（不释放头节点）
void Element_free_method(Element* self) {
    Element* current = self->next;
    while (current != NULL) {
        Element* next = current->next;
        free(current);
        current = next;
    }
}

// 计算链表长度（外部函数）
int Element_length(Element* e) {
    if (e == NULL) {
        return 0;
    }
    return Element_length_method(e);
}

// 判断链表x是否比链表y短
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

// 递归的tail函数
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

// 释放整个链表（包括头节点）
void free_list(Element* e) {
    if (e == NULL) {
        return;
    }
    Element_free_method(e);
    free(e);
}

// 性能测试函数
int benchmark_list() {
    Element* x = Element_make_list(45);
    Element* y = Element_make_list(30);
    Element* z = Element_make_list(20);

    Element* result = tail(x, y, z);
    int l = Element_length(result);

    free_list(x);
    free_list(y);
    free_list(z);

    return l;
}

// 主函数
int main() {
    int result = benchmark_list();
    assert(result == 30 && "tail result length should be 30");
    printf("Test passed! List length: %d\n", result);
    return 0;
}