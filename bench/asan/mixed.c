/* bench/asan/mixed.c — mixed.an 的语义对齐 C 版 (ASan 对照)
 *
 * 与 bench/mixed.an 逐项对应:
 *   - N = 60000 结构体数组 (单块 malloc, 对应单块 dyn), Item{id, score, next}
 *   - pass1 填充: arr[i].id/score = i, next = i+1; 链尾 next = UINT64_MAX 哨兵
 *   - pass2 指针游标求和 ×M = 1600 (while p < end, total += p->score, p++)
 *   - pass3 指针差: end - begin == N (断言)
 *   - pass4 链式扫描 (cur u64 下标 + arr[cur].score / arr[cur].next, 一次)
 *   - 断言: total == M*N*(N-1)/2; sum_next == 每趟游标和; guard == N
 *
 * 防优化屏障 (必要性实测: 裸 C -O2 将 1600 趟扫描折叠为 ~0.02s):
 *   - arr 用 volatile Item* 限定 → pass1/pass2/pass4 的逐元素读写不可消除,
 *     与 .an 逐元素过检查的访问模式一致; ASan 对 volatile 访存同样插桩。
 *   - g_scan 为 volatile 函数指针 → 扫描经不透明间接调用, LICM 不能把
 *     pass2 内层扫描提出 1600 趟外层循环, 保证 M 趟真实执行。
 *
 * 编译:  clang -O2 -fsanitize=address bench/asan/mixed.c -o build/bench/asan_mixed
 * 预期:  exit 0 (断言全过)
 */

#include <stdint.h>
#include <stdlib.h>

typedef struct Item {
    int64_t id;
    int64_t score;
    uint64_t next;
} Item;

/* pass2 单趟指针游标求和 (语义同 .an pass2) */
static int64_t scan(const volatile Item *begin, const volatile Item *end) {
    int64_t t = 0;
    const volatile Item *p = begin;
    while (p < end) {
        t += p->score;
        p++;
    }
    return t;
}

/* 不透明屏障: 阻止编译器把扫描结果作为循环不变量提升/折叠 */
static int64_t (*volatile g_scan)(const volatile Item *, const volatile Item *) = scan;

int main(void) {
    const uint64_t n = 60000;
    const uint64_t passes = 1600;

    volatile Item *arr = (volatile Item *)malloc(n * sizeof(Item));
    if (arr == NULL) {
        return 1;
    }

    /* ---- pass 1: 填充 ---- */
    for (uint64_t i = 0; i < n; i++) {
        arr[i].id = (int64_t)i;
        arr[i].score = (int64_t)i;
        arr[i].next = i + 1;
    }
    arr[n - 1].next = UINT64_MAX; /* 链尾哨兵 */

    /* ---- pass 2: 指针游标求和 ×passes ---- */
    volatile Item *begin = arr;
    volatile Item *end = begin + n;
    int64_t total = 0;
    for (uint64_t pass = 0; pass < passes; pass++) {
        total += g_scan(begin, end);
    }

    /* ---- pass 3: 指针差 ---- */
    int64_t count = end - begin;
    if (count != (int64_t)n) {
        return 1;
    }

    /* ---- pass 4: 链式扫描 (next 字段链接遍历, 一次) ---- */
    int64_t sum_next = 0;
    uint64_t cur = 0;
    uint64_t guard = 0;
    while (cur != UINT64_MAX && guard < n) {
        sum_next += arr[cur].score;
        cur = arr[cur].next;
        guard++;
    }

    /* ---- 闭式断言 ---- */
    int64_t expected = (int64_t)n * ((int64_t)n - 1) / 2 * (int64_t)passes;
    if (total != expected) {
        return 1;
    }
    if (sum_next != expected / (int64_t)passes) {
        return 1;
    }
    if (guard != n) {
        return 1;
    }

    free((void *)arr);
    return 0;
}
