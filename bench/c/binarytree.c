#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

typedef struct treeNode {
    struct treeNode* left;
    struct treeNode* right;
} treeNode;

treeNode* NewTreeNode(treeNode* left, treeNode* right) {
    treeNode* new = (treeNode*)malloc(sizeof(treeNode));
    if (new == NULL) {
        perror("Failed to allocate memory");
        exit(EXIT_FAILURE);
    }
    new->left = left;
    new->right = right;
    return new;
}

treeNode* BottomUpTree(uint32_t depth) {
    if (depth > 0) {
        treeNode* left_child = BottomUpTree(depth - 1);
        treeNode* right_child = BottomUpTree(depth - 1);
        return NewTreeNode(left_child, right_child);
    } else {
        return NewTreeNode(NULL, NULL);
    }
}

int64_t ItemCheck(treeNode* tree) {
    int res = 1;
    treeNode tmp = *tree;
    if (tmp.left != NULL){
        res += ItemCheck(tmp.left);
    }
    if (tmp.right != NULL) {
        res += ItemCheck(tmp.right);
    }
    return res;
}

void DeleteTree(treeNode* tree) {

    if (tree == NULL) {
        return;
    }
    treeNode tmp = *tree;
    if (tmp.left != NULL) {
        DeleteTree(tmp.left);
    }
    if (tmp.right != NULL) {
        DeleteTree(tmp.right);
    }
    free(tree);
}

int main() {
    uint32_t N = 20;
    uint32_t minDepth = 4;
    uint32_t maxDepth = N;
    int64_t check = 0;
    if ((minDepth + 2) > N) {
        maxDepth = minDepth + 2;
    }

    uint32_t stretchDepth = maxDepth + 1;

    treeNode* stretchTree = BottomUpTree(stretchDepth);
    DeleteTree(stretchTree);

    treeNode* longLivedTree = BottomUpTree(maxDepth);

    for (uint32_t depth = minDepth; depth <= maxDepth; depth += 2) {
        uint32_t iterations = 1 << (maxDepth - depth + minDepth);

        for (uint32_t i = 0; i < iterations; ++i) {
            treeNode* tempTree = BottomUpTree(depth);
            check += ItemCheck(tempTree);
            DeleteTree(tempTree);
        }
    }

    DeleteTree(longLivedTree);
    printf("%ld\n", check);
    return 0;
}