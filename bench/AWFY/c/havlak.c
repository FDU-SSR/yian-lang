// bench/AWFY/c/havlak.c — Havlak 循环识别 (AWFY 宏基准) 的 C 参考实现
//
// 语义与规模与 bench/AWFY/an/havlak.an 对齐 (INNER_ITERATIONS=1, ITER=2);
// 只用于跨语言正确性对照, 不参与 fat/raw 计时。main 打印校验值 "1605 5213"
// (上游 AWFY Havlak.verifyResult: innerIterations=1 → r[0]=1605, r[1]=5213),
// 并逐轮断言; 另与官方 15/150/1500 → (1647/2052/6102, 5213) 核对过 (见下)。
// 上游: AWFY (are-we-fast-yet) benchmarks/Java/src/Havlak.java + havlak/*.java
//       Havlak.java / BasicBlock / BasicBlockEdge / ControlFlowGraph / HavlakLoopFinder /
//       LoopStructureGraph / LoopTesterApp / SimpleLoop 为
//       `// Copyright 2011 Google Inc.` + Apache-2.0 (licenses/LICENSE-APACHE);
//       UnionFindNode.java 与 som/*.java 为 MIT (Copyright (c) 2001-2016 Stefan Marr /
//       AWFY AUTHORS.md, licenses/LICENSE-MIT)。
//
// 本文件逐行对应上游 Java; 唯二与语言相关的差异:
//   - 类层次: 上游 SimpleLoop 等是 final class (无子类), 这里用 struct; IdentitySet /
//     IdentityDictionary / Vector 用指针数组 + 线性扫描 (Set 本就线性扫描) 表达。
//   - findSet 的路径压缩按上游 AWFY 语义实现: `nodeList.forEach(iter -> iter.union(parent))`
//     里的 `parent` 是接收者的 parent 字段 (不是找到的根!), 这里原样保留。
//   另: BasicBlock.name 唯一, 故 IdentityDictionary<BasicBlock,Integer> number 落成
//   按 name 索引的 int 数组 (与 .an 一致)。
//
// 官方断言值核对 (clang -O2, 改动 main 的 INNER_ITERATIONS 后运行):
//   innerIterations=1→1605 5213; 15→1647 5213; 150→2052 5213; 1500→6102 5213。
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UNVISITED INT_MAX
#define MAXNONBACKPREDS (32 * 1024)

// ── 动态指针数组 (som.Vector<E> / som.Set<E> / som.IdentitySet<E>) ──
typedef struct {
    void **items;
    int size;
    int cap;
} PtrVec;

static void pv_reserve(PtrVec *v, int need) {
    if (need <= v->cap) {
        return;
    }
    int cap = v->cap == 0 ? 4 : v->cap;
    while (cap < need) {
        cap *= 2;
    }
    v->items = (void **)realloc(v->items, (size_t)cap * sizeof(void *));
    v->cap = cap;
}

static void pv_append(PtrVec *v, void *item) {
    pv_reserve(v, v->size + 1);
    v->items[v->size++] = item;
}

static int pv_has(PtrVec *v, void *item) {
    for (int i = 0; i < v->size; i++) {
        if (v->items[i] == item) {
            return 1;
        }
    }
    return 0;
}

// som.Set.add / IdentitySet.add: 已存在则不追加
static void pv_add(PtrVec *v, void *item) {
    if (!pv_has(v, item)) {
        pv_append(v, item);
    }
}

static void pv_free(PtrVec *v) {
    free(v->items);
    v->items = NULL;
    v->size = 0;
    v->cap = 0;
}

// ── 动态 int 数组 (som.Vector<Integer> / som.Set<Integer>) ──
typedef struct {
    int *items;
    int size;
    int cap;
} IntVec;

static void iv_reserve(IntVec *v, int need) {
    if (need <= v->cap) {
        return;
    }
    int cap = v->cap == 0 ? 4 : v->cap;
    while (cap < need) {
        cap *= 2;
    }
    v->items = (int *)realloc(v->items, (size_t)cap * sizeof(int));
    v->cap = cap;
}

static void iv_append(IntVec *v, int item) {
    iv_reserve(v, v->size + 1);
    v->items[v->size++] = item;
}

static void iv_add(IntVec *v, int item) {
    for (int i = 0; i < v->size; i++) {
        if (v->items[i] == item) {
            return;
        }
    }
    iv_append(v, item);
}

static void iv_free(IntVec *v) {
    free(v->items);
    v->items = NULL;
    v->size = 0;
    v->cap = 0;
}

// ── 图 (havlak/BasicBlock.java, BasicBlockEdge.java, ControlFlowGraph.java) ──
typedef struct BasicBlock {
    int name;
    PtrVec in_edges;   // Vector<BasicBlock>
    PtrVec out_edges;  // Vector<BasicBlock>
} BasicBlock;

typedef struct {
    BasicBlock *from;
    BasicBlock *to;
} BasicBlockEdge;

typedef struct {
    BasicBlock **blocks;  // basicBlockMap: 下标即 name
    int n_blocks;         // Vector.size() (= max name + 1)
    int cap_blocks;
    BasicBlock *start_node;
    PtrVec edge_list;
} ControlFlowGraph;

static BasicBlock *bb_new(int name) {
    BasicBlock *bb = (BasicBlock *)calloc(1, sizeof(BasicBlock));
    bb->name = name;
    return bb;
}

static ControlFlowGraph *cfg_new(void) {
    return (ControlFlowGraph *)calloc(1, sizeof(ControlFlowGraph));
}

static BasicBlock *cfg_create_node(ControlFlowGraph *cfg, int name) {
    BasicBlock *node;
    if (name < cfg->n_blocks && cfg->blocks[name] != NULL) {
        node = cfg->blocks[name];
    } else {
        node = bb_new(name);
        if (name >= cfg->cap_blocks) {
            int cap = cfg->cap_blocks == 0 ? 10 : cfg->cap_blocks;
            while (cap <= name) {
                cap *= 2;
            }
            cfg->blocks = (BasicBlock **)realloc(cfg->blocks, (size_t)cap * sizeof(BasicBlock *));
            for (int i = cfg->cap_blocks; i < cap; i++) {
                cfg->blocks[i] = NULL;
            }
            cfg->cap_blocks = cap;
        }
        cfg->blocks[name] = node;
        if (cfg->n_blocks < name + 1) {
            cfg->n_blocks = name + 1;
        }
    }
    if (cfg->n_blocks == 1) {
        cfg->start_node = node;
    }
    return node;
}

// BasicBlockEdge 构造: 建/取两端节点, 连边, 并登记到 edgeList (上游语义)
static void new_basic_block_edge(ControlFlowGraph *cfg, int from_name, int to_name) {
    BasicBlock *from = cfg_create_node(cfg, from_name);
    BasicBlock *to = cfg_create_node(cfg, to_name);
    pv_append(&from->out_edges, to);
    pv_append(&to->in_edges, from);
    BasicBlockEdge *edge = (BasicBlockEdge *)calloc(1, sizeof(BasicBlockEdge));
    edge->from = from;
    edge->to = to;
    pv_append(&cfg->edge_list, edge);
}

static void cfg_free(ControlFlowGraph *cfg) {
    for (int i = 0; i < cfg->edge_list.size; i++) {
        free(cfg->edge_list.items[i]);
    }
    pv_free(&cfg->edge_list);
    for (int i = 0; i < cfg->n_blocks; i++) {
        BasicBlock *bb = cfg->blocks[i];
        if (bb != NULL) {
            pv_free(&bb->in_edges);
            pv_free(&bb->out_edges);
            free(bb);
        }
    }
    free(cfg->blocks);
    free(cfg);
}

// ── 循环结构 (havlak/SimpleLoop.java, LoopStructureGraph.java) ──
typedef struct SimpleLoop {
    PtrVec basic_blocks;  // IdentitySet<BasicBlock>
    PtrVec children;      // IdentitySet<SimpleLoop>
    struct SimpleLoop *parent;
    int counter;
    int nesting_level;
    int depth_level;
    int is_root;
} SimpleLoop;

static SimpleLoop *loop_new(BasicBlock *bb, int is_reducible) {
    (void)is_reducible;  // 上游保存但从未读取
    SimpleLoop *loop = (SimpleLoop *)calloc(1, sizeof(SimpleLoop));
    if (bb != NULL) {
        pv_add(&loop->basic_blocks, bb);
    }
    return loop;
}

static void loop_add_node(SimpleLoop *loop, BasicBlock *bb) {
    pv_add(&loop->basic_blocks, bb);
}

static void loop_add_child_loop(SimpleLoop *loop, SimpleLoop *child) {
    pv_add(&loop->children, child);
}

static void loop_set_parent(SimpleLoop *loop, SimpleLoop *parent) {
    loop->parent = parent;
    loop_add_child_loop(parent, loop);
}

static void loop_free(SimpleLoop *loop) {
    pv_free(&loop->basic_blocks);
    pv_free(&loop->children);
    free(loop);
}

typedef struct {
    SimpleLoop *root;
    PtrVec loops;  // Vector<SimpleLoop>
    int loop_counter;
} LoopStructureGraph;

static LoopStructureGraph *lsg_new(void) {
    LoopStructureGraph *lsg = (LoopStructureGraph *)calloc(1, sizeof(LoopStructureGraph));
    lsg->loop_counter = 0;
    lsg->root = loop_new(NULL, 1);
    lsg->root->nesting_level = 0;
    lsg->root->is_root = 1;  // 上游 setNestingLevel(0) 的副作用
    lsg->root->counter = lsg->loop_counter;
    lsg->loop_counter += 1;
    pv_append(&lsg->loops, lsg->root);
    return lsg;
}

static SimpleLoop *lsg_create_new_loop(LoopStructureGraph *lsg, BasicBlock *bb, int is_reducible) {
    SimpleLoop *loop = loop_new(bb, is_reducible);
    loop->counter = lsg->loop_counter;
    lsg->loop_counter += 1;
    pv_append(&lsg->loops, loop);
    return loop;
}

static void lsg_calculate_level_rec(SimpleLoop *loop, int depth) {
    loop->depth_level = depth;
    for (int i = 0; i < loop->children.size; i++) {
        SimpleLoop *child = (SimpleLoop *)loop->children.items[i];
        lsg_calculate_level_rec(child, depth + 1);
        int cand = 1 + child->nesting_level;
        if (loop->nesting_level < cand) {
            loop->nesting_level = cand;
        }
    }
}

static void lsg_calculate_nesting_level(LoopStructureGraph *lsg) {
    for (int i = 0; i < lsg->loops.size; i++) {
        SimpleLoop *loop = (SimpleLoop *)lsg->loops.items[i];
        if (!loop->is_root) {
            if (loop->parent == NULL) {
                loop_set_parent(loop, lsg->root);
            }
        }
    }
    lsg_calculate_level_rec(lsg->root, 0);
}

static void lsg_free(LoopStructureGraph *lsg) {
    for (int i = 0; i < lsg->loops.size; i++) {
        loop_free((SimpleLoop *)lsg->loops.items[i]);
    }
    pv_free(&lsg->loops);
    free(lsg);
}

// ── Union/Find (havlak/UnionFindNode.java) ──
typedef struct UnionFindNode {
    struct UnionFindNode *parent;
    BasicBlock *bb;
    SimpleLoop *loop;
    int dfs_number;
} UnionFindNode;

static void uf_init_node(UnionFindNode *node, BasicBlock *bb, int dfs_number) {
    node->parent = node;
    node->bb = bb;
    node->dfs_number = dfs_number;
    node->loop = NULL;
}

// 上游 findSet: 末行 path compression 用的是 `this.parent` (AWFY 原样语义)
static UnionFindNode *uf_find_set(UnionFindNode *self) {
    PtrVec node_list;
    memset(&node_list, 0, sizeof(node_list));

    UnionFindNode *node = self;
    while (node != node->parent) {
        if (node->parent != node->parent->parent) {
            pv_append(&node_list, node);
        }
        node = node->parent;
    }

    for (int i = 0; i < node_list.size; i++) {
        ((UnionFindNode *)node_list.items[i])->parent = self->parent;
    }
    pv_free(&node_list);
    return node;
}

// ── Havlak 循环识别 (havlak/HavlakLoopFinder.java) ──
typedef enum {
    BB_TOP,
    BB_NONHEADER,
    BB_REDUCIBLE,
    BB_SELF,
    BB_IRREDUCIBLE,
    BB_DEAD,
    BB_LAST
} BasicBlockClass;

typedef struct {
    ControlFlowGraph *cfg;
    LoopStructureGraph *lsg;

    IntVec *non_back_preds;  // Vector<Set<Integer>>
    IntVec *back_preds;      // Vector<Vector<Integer>>
    int n_non_back;
    int n_back;
    int *number;             // IdentityDictionary<BasicBlock, Integer> → 按 name 索引
    int max_size;
    int *header;
    BasicBlockClass *type;
    int *last;
    UnionFindNode **nodes;
} HavlakLoopFinder;

static int is_ancestor(int w, int v, int *last) {
    return w <= v && v <= last[w];
}

static int do_dfs(HavlakLoopFinder *f, BasicBlock *current_node, int current) {
    uf_init_node(f->nodes[current], current_node, current);
    f->number[current_node->name] = current;

    int last_id = current;
    PtrVec *outer_blocks = &current_node->out_edges;
    for (int i = 0; i < outer_blocks->size; i++) {
        BasicBlock *target = (BasicBlock *)outer_blocks->items[i];
        if (f->number[target->name] == UNVISITED) {
            last_id = do_dfs(f, target, last_id + 1);
        }
    }
    f->last[current] = last_id;
    return last_id;
}

static void process_edges(HavlakLoopFinder *f, BasicBlock *node_w, int w) {
    if (node_w->in_edges.size > 0) {
        for (int i = 0; i < node_w->in_edges.size; i++) {
            BasicBlock *node_v = (BasicBlock *)node_w->in_edges.items[i];
            int v = f->number[node_v->name];
            if (v != UNVISITED) {
                if (is_ancestor(w, v, f->last)) {
                    iv_append(&f->back_preds[w], v);
                } else {
                    iv_add(&f->non_back_preds[w], v);
                }
            }
        }
    }
}

static void step_e_process_non_back_preds(HavlakLoopFinder *f, int w, PtrVec *node_pool,
                                          PtrVec *work_list, UnionFindNode *x) {
    IntVec *preds = &f->non_back_preds[x->dfs_number];
    // 上游 forEach: 迭代期间若 w == x.dfsNumber 会追加到同一集合, 这里同样重读 size
    for (int k = 0; k < preds->size; k++) {
        UnionFindNode *y = f->nodes[preds->items[k]];
        UnionFindNode *ydash = uf_find_set(y);

        if (!is_ancestor(w, ydash->dfs_number, f->last)) {
            f->type[w] = BB_IRREDUCIBLE;
            iv_add(&f->non_back_preds[w], ydash->dfs_number);
        } else {
            if (ydash->dfs_number != w) {
                if (!pv_has(node_pool, ydash)) {
                    pv_append(work_list, ydash);
                    pv_append(node_pool, ydash);
                }
            }
        }
    }
}

static void step_d(HavlakLoopFinder *f, int w, PtrVec *node_pool) {
    IntVec *preds = &f->back_preds[w];
    for (int i = 0; i < preds->size; i++) {
        int v = preds->items[i];
        if (v != w) {
            pv_append(node_pool, uf_find_set(f->nodes[v]));
        } else {
            f->type[w] = BB_SELF;
        }
    }
}

static void set_loop_attributes(HavlakLoopFinder *f, int w, PtrVec *node_pool, SimpleLoop *loop) {
    f->nodes[w]->loop = loop;
    for (int i = 0; i < node_pool->size; i++) {
        UnionFindNode *node = (UnionFindNode *)node_pool->items[i];
        f->header[node->dfs_number] = w;
        node->parent = f->nodes[w];
        if (node->loop != NULL) {
            loop_set_parent(node->loop, loop);
        } else {
            loop_add_node(loop, node->bb);
        }
    }
}

static void find_loops(HavlakLoopFinder *f) {
    if (f->cfg->start_node == NULL) {
        return;
    }

    int size = f->cfg->n_blocks;

    if (size > f->max_size) {
        free(f->header);
        free(f->type);
        free(f->last);
        free(f->nodes);
        f->header = (int *)calloc((size_t)size, sizeof(int));
        f->type = (BasicBlockClass *)calloc((size_t)size, sizeof(BasicBlockClass));
        f->last = (int *)calloc((size_t)size, sizeof(int));
        f->nodes = (UnionFindNode **)calloc((size_t)size, sizeof(UnionFindNode *));
        f->max_size = size;
    }

    // nonBackPreds/backPreds/number 每次清空重建 (上游 removeAll + append)
    for (int i = 0; i < f->n_non_back; i++) {
        iv_free(&f->non_back_preds[i]);
    }
    free(f->non_back_preds);
    for (int i = 0; i < f->n_back; i++) {
        iv_free(&f->back_preds[i]);
    }
    free(f->back_preds);
    f->non_back_preds = (IntVec *)calloc((size_t)size, sizeof(IntVec));
    f->back_preds = (IntVec *)calloc((size_t)size, sizeof(IntVec));
    f->n_non_back = size;
    f->n_back = size;
    free(f->number);
    f->number = (int *)malloc((size_t)size * sizeof(int));

    for (int i = 0; i < size; ++i) {
        f->nodes[i] = (UnionFindNode *)calloc(1, sizeof(UnionFindNode));
    }

    // initAllNodes: 全部标 UNVISITED + DFS 编号
    for (int i = 0; i < size; i++) {
        if (f->cfg->blocks[i] != NULL) {
            f->number[f->cfg->blocks[i]->name] = UNVISITED;
        }
    }
    do_dfs(f, f->cfg->start_node, 0);

    // identifyEdges
    for (int w = 0; w < size; w++) {
        f->header[w] = 0;
        f->type[w] = BB_NONHEADER;

        BasicBlock *node_w = f->nodes[w]->bb;
        if (node_w == NULL) {
            f->type[w] = BB_DEAD;
        } else {
            process_edges(f, node_w, w);
        }
    }

    // Start node is root of all other loops.
    f->header[0] = 0;

    // Step c
    for (int w = size - 1; w >= 0; w--) {
        PtrVec node_pool;
        memset(&node_pool, 0, sizeof(node_pool));

        BasicBlock *node_w = f->nodes[w]->bb;
        if (node_w != NULL) {
            step_d(f, w, &node_pool);

            PtrVec work_list;
            memset(&work_list, 0, sizeof(work_list));
            for (int i = 0; i < node_pool.size; i++) {
                pv_append(&work_list, node_pool.items[i]);
            }

            if (node_pool.size != 0) {
                f->type[w] = BB_REDUCIBLE;
            }

            int head = 0;
            while (head < work_list.size) {
                UnionFindNode *x = (UnionFindNode *)work_list.items[head];
                head++;

                int non_back_size = f->non_back_preds[x->dfs_number].size;
                if (non_back_size > MAXNONBACKPREDS) {
                    pv_free(&node_pool);
                    pv_free(&work_list);
                    return;
                }
                step_e_process_non_back_preds(f, w, &node_pool, &work_list, x);
            }

            if ((node_pool.size > 0) || (f->type[w] == BB_SELF)) {
                SimpleLoop *loop = lsg_create_new_loop(f->lsg, node_w, f->type[w] != BB_IRREDUCIBLE);
                set_loop_attributes(f, w, &node_pool, loop);
            }

            pv_free(&work_list);
        }
        pv_free(&node_pool);
    }
}

static void finder_free(HavlakLoopFinder *f) {
    if (f->non_back_preds != NULL) {
        for (int i = 0; i < f->n_non_back; i++) {
            iv_free(&f->non_back_preds[i]);
        }
        free(f->non_back_preds);
    }
    if (f->back_preds != NULL) {
        for (int i = 0; i < f->n_back; i++) {
            iv_free(&f->back_preds[i]);
        }
        free(f->back_preds);
    }
    free(f->number);
    free(f->header);
    free(f->type);
    free(f->last);
    if (f->nodes != NULL) {
        for (int i = 0; i < f->max_size; i++) {
            free(f->nodes[i]);
        }
        free(f->nodes);
    }
}

// ── 测试驱动 (havlak/LoopTesterApp.java) ──
typedef struct {
    ControlFlowGraph *cfg;
    LoopStructureGraph *lsg;
} LoopTesterApp;

static int build_diamond(LoopTesterApp *app, int start) {
    int bb0 = start;
    new_basic_block_edge(app->cfg, bb0, bb0 + 1);
    new_basic_block_edge(app->cfg, bb0, bb0 + 2);
    new_basic_block_edge(app->cfg, bb0 + 1, bb0 + 3);
    new_basic_block_edge(app->cfg, bb0 + 2, bb0 + 3);
    return bb0 + 3;
}

static void build_connect(LoopTesterApp *app, int start, int end) {
    new_basic_block_edge(app->cfg, start, end);
}

static int build_straight(LoopTesterApp *app, int start, int n) {
    for (int i = 0; i < n; i++) {
        build_connect(app, start + i, start + i + 1);
    }
    return start + n;
}

static int build_base_loop(LoopTesterApp *app, int from) {
    int header = build_straight(app, from, 1);
    int diamond1 = build_diamond(app, header);
    int d11 = build_straight(app, diamond1, 1);
    int diamond2 = build_diamond(app, d11);
    int footer = build_straight(app, diamond2, 1);
    build_connect(app, diamond2, d11);
    build_connect(app, diamond1, header);

    build_connect(app, footer, from);
    footer = build_straight(app, footer, 1);
    return footer;
}

static void run_find_loops(LoopTesterApp *app, LoopStructureGraph *loop_structure) {
    HavlakLoopFinder finder;
    memset(&finder, 0, sizeof(finder));
    finder.cfg = app->cfg;
    finder.lsg = loop_structure;
    find_loops(&finder);
    finder_free(&finder);
}

static void construct_simple_cfg(LoopTesterApp *app) {
    cfg_create_node(app->cfg, 0);
    build_base_loop(app, 0);
    cfg_create_node(app->cfg, 1);
    new_basic_block_edge(app->cfg, 0, 2);
}

static void add_dummy_loops(LoopTesterApp *app, int num_dummy_loops) {
    for (int dummyloop = 0; dummyloop < num_dummy_loops; dummyloop++) {
        run_find_loops(app, app->lsg);
    }
}

static void construct_cfg(LoopTesterApp *app, int par_loops, int ppar_loops, int pppar_loops) {
    int n = 2;

    for (int parlooptrees = 0; parlooptrees < par_loops; parlooptrees++) {
        cfg_create_node(app->cfg, n + 1);
        build_connect(app, 2, n + 1);
        n += 1;

        for (int i = 0; i < ppar_loops; i++) {
            int top = n;
            n = build_straight(app, n, 1);
            for (int j = 0; j < pppar_loops; j++) {
                n = build_base_loop(app, n);
            }
            int bottom = build_straight(app, n, 1);
            build_connect(app, n, top);
            n = bottom;
        }
        build_connect(app, n, 1);
    }
}

static void app_main(LoopTesterApp *app, int num_dummy_loops, int find_loop_iterations,
                     int par_loops, int ppar_loops, int pppar_loops, int *out_loops,
                     int *out_nodes) {
    construct_simple_cfg(app);
    add_dummy_loops(app, num_dummy_loops);
    construct_cfg(app, par_loops, ppar_loops, pppar_loops);

    run_find_loops(app, app->lsg);
    for (int i = 0; i < find_loop_iterations; i++) {
        LoopStructureGraph *tmp = lsg_new();
        run_find_loops(app, tmp);
        lsg_free(tmp);
    }

    lsg_calculate_nesting_level(app->lsg);
    *out_loops = app->lsg->loops.size;
    *out_nodes = app->cfg->n_blocks;
}

// 规模与 bench/AWFY/an/havlak.an 对齐: INNER=1 (上游官方断言值), ITER 轮
#define INNER_ITERATIONS 1
#define ITER 2

int main(void) {
    int find_loop_iterations = 50;
    int par_loops = 10;
    int ppar_loops = 10;
    int pppar_loops = 5;

    int loops = 0;
    int nodes = 0;
    int total_loops = 0;
    int total_nodes = 0;

    for (int it = 0; it < ITER; it++) {
        LoopTesterApp app;
        memset(&app, 0, sizeof(app));
        app.cfg = cfg_new();
        app.lsg = lsg_new();
        cfg_create_node(app.cfg, 0);

        app_main(&app, INNER_ITERATIONS, find_loop_iterations, par_loops, ppar_loops, pppar_loops,
                 &loops, &nodes);

        lsg_free(app.lsg);
        cfg_free(app.cfg);

        // 上游官方断言值 (AWFY Havlak.verifyResult): innerIterations=1 → 1605 / 5213
        if (loops != 1605 || nodes != 5213) {
            fprintf(stderr, "havlak: expected 1605 5213, got %d %d\n", loops, nodes);
            return 1;
        }
        total_loops += loops;
        total_nodes += nodes;
    }

    if (total_loops != 1605 * ITER || total_nodes != 5213 * ITER) {
        fprintf(stderr, "havlak: totals mismatch: %d %d\n", total_loops, total_nodes);
        return 1;
    }

    printf("%d %d\n", loops, nodes);
    return 0;
}
