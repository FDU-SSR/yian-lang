/* bench/asan/ptr_traverse.c — ptr_traverse.an 的语义对齐 C 版 (ASan 对照)
 *
 * 与 bench/ptr_traverse.an 逐项对应:
 *   - N = 10000 结点满二叉树, 每个结点独立 malloc (对应每结点一次 dyn 分配)
 *   - 建树连边: 结点 i 的左/右孩子为 2i+1 / 2i+2 (同 .an)
 *   - P = 5000 趟递归求和遍历 (tree_sum 递归, 语义同 .an)
 *   - 断言: total == N*(N-1)/2 * P (防优化掉 + 正确性对照)
 *   - 逐结点 free (对应逐结点 del)
 *
 * 防优化屏障 (必要性实测: 裸 C -O2 将 5000 趟遍历折叠为 ~0s):
 *   g_tree_sum 为 volatile 函数指针, 经其间接调用 → 编译器无法证明调用纯函数,
 *   LICM 不能把遍历提出循环, 保证 5000 趟真实执行 (每趟开销 = 一次间接调用, 可忽略)。
 *   结点数据无需 volatile: 递归遍历经不透明调用消费, 建树写不能被消除;
 *   递归的指针链式访问本身不可被折叠。
 *
 * 编译:  clang -O2 -fsanitize=address bench/asan/ptr_traverse.c -o build/bench/asan_ptr_traverse
 * 预期:  exit 0 (断言全过)
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct TreeNode {
    int64_t value;
    struct TreeNode *left;
    struct TreeNode *right;
} TreeNode;

static int64_t tree_sum(TreeNode *n) {
    if (n == NULL) {
        return 0;
    }
    return tree_sum(n->left) + tree_sum(n->right) + n->value;
}

/* 不透明屏障: 阻止编译器把 tree_sum(nodes[0]) 当作循环不变量提升/折叠 */
static int64_t (*volatile g_tree_sum)(TreeNode *) = tree_sum;

int main(void) {
    const uint64_t n_nodes = 10000;
    const uint64_t passes = 5000;

    TreeNode **nodes = (TreeNode **)malloc(n_nodes * sizeof(TreeNode *));
    if (nodes == NULL) {
        return 1;
    }

    for (uint64_t i = 0; i < n_nodes; i++) {
        nodes[i] = (TreeNode *)malloc(sizeof(TreeNode));
        if (nodes[i] == NULL) {
            return 1;
        }
        nodes[i]->value = (int64_t)i;
        nodes[i]->left = NULL;
        nodes[i]->right = NULL;
    }

    for (uint64_t j = 0; j < n_nodes; j++) {
        uint64_t left_idx = 2 * j + 1;
        uint64_t right_idx = 2 * j + 2;
        if (left_idx < n_nodes) {
            nodes[j]->left = nodes[left_idx];
        }
        if (right_idx < n_nodes) {
            nodes[j]->right = nodes[right_idx];
        }
    }

    int64_t total = 0;
    for (uint64_t pass = 0; pass < passes; pass++) {
        total += g_tree_sum(nodes[0]);
    }

    int64_t expected = (int64_t)n_nodes * ((int64_t)n_nodes - 1) / 2 * (int64_t)passes;
    if (total != expected) {
        fprintf(stderr, "ptr_traverse: tree traversal sum mismatch\n");
        return 1;
    }

    for (uint64_t k = 0; k < n_nodes; k++) {
        free(nodes[k]);
    }
    free(nodes);
    return 0;
}
