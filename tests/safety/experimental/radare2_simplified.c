#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    char type;
    char* data;
} pyc_object;

typedef struct {
    pyc_object* data;
} RefNode;

static pyc_object* get_none_object() {
    pyc_object* obj = calloc(1, sizeof(pyc_object));
    if (!obj) return NULL;
    obj->type = 'N';  // None 类型标识
    obj->data = strdup("None");
    return obj;
}

static void free_object(pyc_object* obj) {
    if (obj) {
        free(obj->data);
        free(obj);
    }
}

static RefNode* create_ref_node(pyc_object* data) {
    RefNode* node = malloc(sizeof(RefNode));
    if (node) node->data = data;
    return node;
}

pyc_object* get_object(unsigned char code) {
    char flag = code & 0x80;  // 提取 flag 位
    char type = code & ~0x80; // 提取 type 位
    
    pyc_object* ret = NULL;
    RefNode* ref = NULL;
    
    // 0x81 的 flag 位被设置 (flag != 0)
    if (flag) {
        ret = get_none_object(); // 创建初始对象
        ref = create_ref_node(ret); // 创建引用节点
    }

    // 0x81 的 type 位是 1
    if (type == 1) {
        // 保留原始对象不做任何修改
    }
    // 其他类型处理已删除

    // 处理 flag 被设置的情况
    if (flag && ref) {
        free_object(ref->data);  // 关键：释放初始对象
        ref->data = get_none_object(); // 创建新对象（非必要）
    }

    // 清理引用节点
    if (ref) {
        free_object(ref->data);  // 释放新创建的对象
        free(ref);
    }
    
    return ret; // 返回已被释放的初始对象指针
}

int main() {
    unsigned char input = 0x81; // flag 设置 + type 1
    
    pyc_object* obj = get_object(input);
    if (obj) {
        // Use-after-free: 访问已释放内存
        printf("对象类型: %c\n", obj->type);
        printf("对象数据: %s\n", obj->data);
        free_object(obj); // Double-free
    }
    return 0;
}