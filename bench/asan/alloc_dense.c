/* bench/asan/alloc_dense.c — alloc_dense.an 的语义对齐 C 版 (ASan 对照)
 *
 * 与 bench/alloc_dense.an 逐项对应:
 *   Phase A (R1 = 220000 轮 Vec<i32> churn):
 *     - 每轮从容量 0 push 32 次; 成长序列 0→4→8→16→32 与 lib/core/raw_vec.an
 *       grow() 一致 (cap==0 → 4, 否则 ×2), 每轮 4 次 realloc
 *       (malloc 新块 + memmove 拷贝 + free 旧块), 循环末尾 free 缓冲区 (对应 drop)
 *     - pop 32 次, 累加回弹值 (和 496/轮, 同 .an)
 *   Phase B (R2 = 150000 轮 dyn[32] i32 分配→写→读→del):
 *     - 每轮 malloc(32) + 32 写 + 2 读 + free
 *  断言: checksum == 496*R1 + 31*R2 (防优化掉 + 正确性对照)
 *
 * 防优化屏障 (必要性实测: 裸 C -O2 因 malloc/free 不逃逸 + 值编译期可知,
 *   整程序被折叠为 ~0s):
 *   元素缓冲用 volatile int32_t* 限定 → 每次 push/pop 写/读都是不可消除的
 *   真实访存, 与 .an 逐元素过检查的访问模式一致; ASan 对 volatile 访存同样插桩。
 *   realloc 拷贝用 memmove (同 .an 的 stdlib memmove, 批量拷贝不逐元素检查)。
 *
 * 编译:  clang -O2 -fsanitize=address bench/asan/alloc_dense.c -o build/bench/asan_alloc_dense
 * 预期:  exit 0 (断言全过)
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    int64_t checksum = 0;

    /* ---- Phase A: Vec<i32> push/pop churn ---- */
    const uint64_t rounds_a = 220000;
    for (uint64_t r = 0; r < rounds_a; r++) {
        uint64_t len = 0;
        uint64_t cap = 0;
        volatile int32_t *data = NULL;

        /* push 32 次 (grow: 0→4→8→16→32, 同 raw_vec.an grow()) */
        for (uint64_t i = 0; i < 32; i++) {
            if (len == cap) {
                uint64_t new_cap = (cap == 0) ? 4 : cap * 2;
                volatile int32_t *new_data =
                    (volatile int32_t *)malloc(new_cap * sizeof(int32_t));
                if (new_data == NULL) {
                    return 1;
                }
                if (len > 0) {
                    memmove((void *)new_data, (const void *)data,
                            len * sizeof(int32_t));
                }
                free((void *)data);
                data = new_data;
                cap = new_cap;
            }
            data[len] = (int32_t)i;
            len++;
        }

        /* pop 32 次, 累加 (同 .an: popped.unwrap() 求和) */
        for (uint64_t j = 0; j < 32; j++) {
            len--;
            checksum += data[len];
        }

        /* Vec drop → 缓冲区 Delete */
        free((void *)data);
    }

    /* ---- Phase B: dyn[32] i32 分配 + del 循环 ---- */
    const uint64_t rounds_b = 150000;
    for (uint64_t r2 = 0; r2 < rounds_b; r2++) {
        volatile int32_t *block =
            (volatile int32_t *)malloc(32 * sizeof(int32_t));
        if (block == NULL) {
            return 1;
        }
        for (uint64_t k = 0; k < 32; k++) {
            block[k] = (int32_t)k;
        }
        /* 保留块存活两次读, 防止分配/填充被优化掉 */
        checksum += block[0] + block[31];
        free((void *)block);
    }

    int64_t expected =
        (int64_t)496 * (int64_t)rounds_a + (int64_t)31 * (int64_t)rounds_b;
    if (checksum != expected) {
        return 1;
    }
    return 0;
}
