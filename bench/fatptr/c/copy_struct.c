// bench/fatptr/c/copy_struct.c — copy_struct 的 C 参考 (三态权威值)
//
// 与 bench/fatptr/an/copy_struct.an 同算法同规模: 结构体数组逐元素浅拷贝,
// 元素含一个 8 B 裸引用字段与一个 8 B 裸视图字段; 规模 (轮数) 经 argv[1] 传入
// (缺省与 .an 的标记值一致)。
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct {
    int64_t value;
} Payload;

typedef struct {
    int64_t value;
    Payload *ref;
    Payload *view;
} Item;

static int64_t sum_item(Item src) {
    return src.value;
}

int main(int argc, char **argv) {
    const int64_t items = 100000;
    int64_t rounds = argc > 1 ? atoll(argv[1]) : 1024;
    Payload *payloads = malloc((size_t)items * sizeof(Payload));
    Item *src = malloc((size_t)items * sizeof(Item));
    Item *dst = malloc((size_t)items * sizeof(Item));
    if (payloads == NULL || src == NULL || dst == NULL) {
        return 2;
    }
    for (int64_t i = 0; i < items; i++) {
        payloads[i].value = i;
        src[i].value = i;
        src[i].ref = &payloads[i];
        src[i].view = payloads;
    }
    int64_t acc = 0;
    for (int64_t round = 0; round < rounds; round++) {
        for (int64_t i = 0; i < items; i++) {
            Item item = src[i];
            dst[i] = item;
            acc += sum_item(item);
        }
    }
    int64_t expect = rounds * (items * (items - 1) / 2);
    assert(acc == expect && "copy_struct checksum");
    int64_t check = 0;
    for (int64_t i = 0; i < items; i++) {
        check += dst[i].value + dst[i].ref->value + dst[i].view[i].value;
    }
    assert(check == 3 * (items * (items - 1) / 2) && "copy_struct dst");
    printf("copy_struct pass\n");
    free(dst);
    free(src);
    free(payloads);
    return 0;
}
