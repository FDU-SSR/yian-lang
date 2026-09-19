// bench/AWFY/c/cd.c — 碰撞检测 (CD) 的 C 参考实现
//
// 语义与规模与 bench/AWFY/an/cd.an 对齐; 只用于跨语言正确性对照, 不参与 fat/raw 计时。
// 上游: AWFY (are-we-fast-yet) benchmarks/Java/src/CD.java + cd/*.java
//       (MIT, Copyright (c) 2001-2016 Stefan Marr; 见仓库 LICENSE.md)
// 说明: 上游用 Java 泛型红黑树 (RedBlackTree<K,V>), 这里用一个带标签键的连通实现;
//       sin/cos/sqrt 直接用 libm (YIAN 侧无数学内建, 用自己的多项式实现, 见 .an 注释)。
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define MIN_X 0.0
#define MIN_Y 0.0
#define MAX_X 1000.0
#define MAX_Y 1000.0
#define MIN_Z 0.0
#define MAX_Z 10.0
#define PROXIMITY_RADIUS 1.0
#define GOOD_VOXEL_SIZE (PROXIMITY_RADIUS * 2.0)
#define NUM_FRAMES 200

typedef struct {
    double x, y, z;
} Vector3D;

typedef struct {
    double x, y;
} Vector2D;

static Vector3D v3(double x, double y, double z) {
    Vector3D v;
    v.x = x;
    v.y = y;
    v.z = z;
    return v;
}

static Vector3D v3_plus(Vector3D a, Vector3D b) { return v3(a.x + b.x, a.y + b.y, a.z + b.z); }
static Vector3D v3_minus(Vector3D a, Vector3D b) { return v3(a.x - b.x, a.y - b.y, a.z - b.z); }
static Vector3D v3_times(Vector3D a, double s) { return v3(a.x * s, a.y * s, a.z * s); }
static double v3_dot(Vector3D a, Vector3D b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static double v3_squared_magnitude(Vector3D a) { return v3_dot(a, a); }
static double v3_magnitude(Vector3D a) { return sqrt(v3_squared_magnitude(a)); }

static Vector2D v2(double x, double y) {
    Vector2D v;
    v.x = x;
    v.y = y;
    return v;
}

static Vector2D v2_plus(Vector2D a, Vector2D b) { return v2(a.x + b.x, a.y + b.y); }
static Vector2D v2_minus(Vector2D a, Vector2D b) { return v2(a.x - b.x, a.y - b.y); }

static int compare_numbers(double a, double b) {
    if (a == b) {
        return 0;
    }
    if (a < b) {
        return -1;
    }
    if (a > b) {
        return 1;
    }
    return (a == a) ? 1 : -1;  // NaN 视为最小
}

static int v2_compare(Vector2D a, Vector2D b) {
    int result = compare_numbers(a.x, b.x);
    return result != 0 ? result : compare_numbers(a.y, b.y);
}

// ── 键: 标签 + CallSign 值 或 Vector2D ──
typedef struct {
    int kind;  // 0 = CallSign, 1 = Vector2D
    int sign;
    Vector2D voxel;
} Key;

static Key key_sign(int value) {
    Key k;
    k.kind = 0;
    k.sign = value;
    k.voxel = v2(0.0, 0.0);
    return k;
}

static Key key_voxel(Vector2D value) {
    Key k;
    k.kind = 1;
    k.sign = 0;
    k.voxel = value;
    return k;
}

static int key_compare(Key a, Key b) {
    if (a.kind != b.kind) {
        return a.kind < b.kind ? -1 : 1;
    }
    if (a.kind == 0) {
        return (a.sign == b.sign) ? 0 : ((a.sign < b.sign) ? -1 : 1);
    }
    return v2_compare(a.voxel, b.voxel);
}

// ── 红黑树 (键 → void* 值; 移植自上游 RedBlackTree.java) ──
enum { COLOR_RED, COLOR_BLACK };

typedef struct RBNode {
    Key key;
    void *value;
    struct RBNode *left;
    struct RBNode *right;
    struct RBNode *parent;
    int color;
} RBNode;

typedef struct {
    RBNode *root;
} RBTree;

typedef struct {
    int is_new_entry;
    RBNode *new_node;
    void *old_value;
} InsertResult;

static RBNode *rb_node_new(Key key, void *value) {
    RBNode *node = malloc(sizeof(RBNode));
    node->key = key;
    node->value = value;
    node->left = NULL;
    node->right = NULL;
    node->parent = NULL;
    node->color = COLOR_RED;
    return node;
}

static RBNode *tree_minimum(RBNode *x) {
    RBNode *current = x;
    while (current->left != NULL) {
        current = current->left;
    }
    return current;
}

static RBNode *node_successor(RBNode *x) {
    if (x->right != NULL) {
        return tree_minimum(x->right);
    }
    RBNode *y = x->parent;
    while (y != NULL && x == y->right) {
        x = y;
        y = y->parent;
    }
    return y;
}

static void rb_left_rotate(RBTree *tree, RBNode *x) {
    RBNode *y = x->right;
    x->right = y->left;
    if (y->left != NULL) {
        y->left->parent = x;
    }
    y->parent = x->parent;
    if (x->parent == NULL) {
        tree->root = y;
    } else if (x == x->parent->left) {
        x->parent->left = y;
    } else {
        x->parent->right = y;
    }
    y->left = x;
    x->parent = y;
}

static void rb_right_rotate(RBTree *tree, RBNode *y) {
    RBNode *x = y->left;
    y->left = x->right;
    if (x->right != NULL) {
        x->right->parent = y;
    }
    x->parent = y->parent;
    if (y->parent == NULL) {
        tree->root = x;
    } else if (y == y->parent->left) {
        y->parent->left = x;
    } else {
        y->parent->right = x;
    }
    x->right = y;
    y->parent = x;
}

static InsertResult tree_insert(RBTree *tree, Key key, void *value) {
    InsertResult result;
    RBNode *y = NULL;
    RBNode *x = tree->root;
    while (x != NULL) {
        y = x;
        int comparison = key_compare(key, x->key);
        if (comparison < 0) {
            x = x->left;
        } else if (comparison > 0) {
            x = x->right;
        } else {
            result.is_new_entry = 0;
            result.new_node = NULL;
            result.old_value = x->value;
            x->value = value;
            return result;
        }
    }
    RBNode *z = rb_node_new(key, value);
    z->parent = y;
    if (y == NULL) {
        tree->root = z;
    } else if (key_compare(key, y->key) < 0) {
        y->left = z;
    } else {
        y->right = z;
    }
    result.is_new_entry = 1;
    result.new_node = z;
    result.old_value = NULL;
    return result;
}

static void *rb_put(RBTree *tree, Key key, void *value) {
    InsertResult insertion = tree_insert(tree, key, value);
    if (!insertion.is_new_entry) {
        return insertion.old_value;
    }
    RBNode *x = insertion.new_node;
    while (x != tree->root && x->parent->color == COLOR_RED) {
        if (x->parent == x->parent->parent->left) {
            RBNode *y = x->parent->parent->right;
            if (y != NULL && y->color == COLOR_RED) {
                x->parent->color = COLOR_BLACK;
                y->color = COLOR_BLACK;
                x->parent->parent->color = COLOR_RED;
                x = x->parent->parent;
            } else {
                if (x == x->parent->right) {
                    x = x->parent;
                    rb_left_rotate(tree, x);
                }
                x->parent->color = COLOR_BLACK;
                x->parent->parent->color = COLOR_RED;
                rb_right_rotate(tree, x->parent->parent);
            }
        } else {
            RBNode *y = x->parent->parent->left;
            if (y != NULL && y->color == COLOR_RED) {
                x->parent->color = COLOR_BLACK;
                y->color = COLOR_BLACK;
                x->parent->parent->color = COLOR_RED;
                x = x->parent->parent;
            } else {
                if (x == x->parent->left) {
                    x = x->parent;
                    rb_right_rotate(tree, x);
                }
                x->parent->color = COLOR_BLACK;
                x->parent->parent->color = COLOR_RED;
                rb_left_rotate(tree, x->parent->parent);
            }
        }
    }
    tree->root->color = COLOR_BLACK;
    return NULL;
}

static RBNode *find_node(RBTree *tree, Key key) {
    RBNode *current = tree->root;
    while (current != NULL) {
        int comparison = key_compare(key, current->key);
        if (comparison == 0) {
            return current;
        }
        current = comparison < 0 ? current->left : current->right;
    }
    return NULL;
}

static void remove_fixup(RBTree *tree, RBNode *x, RBNode *x_parent) {
    while (x != tree->root && (x == NULL || x->color == COLOR_BLACK)) {
        if (x == x_parent->left) {
            RBNode *w = x_parent->right;
            if (w->color == COLOR_RED) {
                w->color = COLOR_BLACK;
                x_parent->color = COLOR_RED;
                rb_left_rotate(tree, x_parent);
                w = x_parent->right;
            }
            if ((w->left == NULL || w->left->color == COLOR_BLACK) &&
                (w->right == NULL || w->right->color == COLOR_BLACK)) {
                w->color = COLOR_RED;
                x = x_parent;
                x_parent = x->parent;
            } else {
                if (w->right == NULL || w->right->color == COLOR_BLACK) {
                    w->left->color = COLOR_BLACK;
                    w->color = COLOR_RED;
                    rb_right_rotate(tree, w);
                    w = x_parent->right;
                }
                w->color = x_parent->color;
                x_parent->color = COLOR_BLACK;
                if (w->right != NULL) {
                    w->right->color = COLOR_BLACK;
                }
                rb_left_rotate(tree, x_parent);
                x = tree->root;
                x_parent = x->parent;
            }
        } else {
            RBNode *w = x_parent->left;
            if (w->color == COLOR_RED) {
                w->color = COLOR_BLACK;
                x_parent->color = COLOR_RED;
                rb_right_rotate(tree, x_parent);
                w = x_parent->left;
            }
            if ((w->right == NULL || w->right->color == COLOR_BLACK) &&
                (w->left == NULL || w->left->color == COLOR_BLACK)) {
                w->color = COLOR_RED;
                x = x_parent;
                x_parent = x->parent;
            } else {
                if (w->left == NULL || w->left->color == COLOR_BLACK) {
                    w->right->color = COLOR_BLACK;
                    w->color = COLOR_RED;
                    rb_left_rotate(tree, w);
                    w = x_parent->left;
                }
                w->color = x_parent->color;
                x_parent->color = COLOR_BLACK;
                if (w->left != NULL) {
                    w->left->color = COLOR_BLACK;
                }
                rb_right_rotate(tree, x_parent);
                x = tree->root;
                x_parent = x->parent;
            }
        }
    }
    if (x != NULL) {
        x->color = COLOR_BLACK;
    }
}

static void *rb_remove(RBTree *tree, Key key) {
    RBNode *z = find_node(tree, key);
    if (z == NULL) {
        return NULL;
    }
    RBNode *y = (z->left == NULL || z->right == NULL) ? z : node_successor(z);
    RBNode *x = (y->left != NULL) ? y->left : y->right;
    RBNode *x_parent;
    if (x != NULL) {
        x->parent = y->parent;
        x_parent = x->parent;
    } else {
        x_parent = y->parent;
    }
    if (y->parent == NULL) {
        tree->root = x;
    } else if (y == y->parent->left) {
        y->parent->left = x;
    } else {
        y->parent->right = x;
    }
    if (y != z) {
        if (y->color == COLOR_BLACK) {
            remove_fixup(tree, x, x_parent);
        }
        y->parent = z->parent;
        y->color = z->color;
        y->left = z->left;
        y->right = z->right;
        if (z->left != NULL) {
            z->left->parent = y;
        }
        if (z->right != NULL) {
            z->right->parent = y;
        }
        if (z->parent != NULL) {
            if (z->parent->left == z) {
                z->parent->left = y;
            } else {
                z->parent->right = y;
            }
        } else {
            tree->root = y;
        }
    } else if (y->color == COLOR_BLACK) {
        remove_fixup(tree, x, x_parent);
    }
    void *value = z->value;
    free(z);
    return value;
}

static void *rb_get(RBTree *tree, Key key) {
    RBNode *node = find_node(tree, key);
    return node == NULL ? NULL : node->value;
}

typedef void (*RBVisitor)(Key key, void *value, void *ctx);

static void rb_foreach(RBTree *tree, RBVisitor visit, void *ctx) {
    if (tree->root == NULL) {
        return;
    }
    RBNode *current = tree_minimum(tree->root);
    while (current != NULL) {
        visit(current->key, current->value, ctx);
        current = node_successor(current);
    }
}

// ── 释放整棵树: 后序遍历释放全部节点; 传入 free_value 时同时释放节点值 ──
typedef void (*RBValueFree)(void *value);

static void rb_free_subtree(RBNode *node, RBValueFree free_value) {
    if (node == NULL) {
        return;
    }
    rb_free_subtree(node->left, free_value);
    rb_free_subtree(node->right, free_value);
    if (free_value != NULL) {
        free_value(node->value);
    }
    free(node);
}

static void rb_free_all_with(RBTree *tree, RBValueFree free_value) {
    rb_free_subtree(tree->root, free_value);
    tree->root = NULL;
}

static void rb_free_all(RBTree *tree) { rb_free_all_with(tree, NULL); }

// ── CD 本体 ──
typedef struct {
    int callsign;
    Vector3D pos_one;
    Vector3D pos_two;
} Motion;

static Vector3D motion_delta(Motion m) { return v3_minus(m.pos_two, m.pos_one); }

static int find_intersection(Motion m1, Motion m2, Vector3D *out) {
    Vector3D init1 = m1.pos_one;
    Vector3D init2 = m2.pos_one;
    Vector3D vec1 = motion_delta(m1);
    Vector3D vec2 = motion_delta(m2);
    const double radius = PROXIMITY_RADIUS;

    double a = v3_squared_magnitude(v3_minus(vec2, vec1));
    if (a != 0.0) {
        double b = 2.0 * v3_dot(v3_minus(init1, init2), v3_minus(vec1, vec2));
        double c = -radius * radius + v3_squared_magnitude(v3_minus(init2, init1));
        double discr = b * b - 4.0 * a * c;
        if (discr < 0.0) {
            return 0;
        }
        double v1 = (-b - sqrt(discr)) / (2.0 * a);
        double v2 = (-b + sqrt(discr)) / (2.0 * a);
        if (v1 <= v2 && ((v1 <= 1.0 && 1.0 <= v2) || (v1 <= 0.0 && 0.0 <= v2) ||
                         (0.0 <= v1 && v2 <= 1.0))) {
            double v = (v1 <= 0.0) ? 0.0 : v1;
            Vector3D result1 = v3_plus(init1, v3_times(vec1, v));
            Vector3D result2 = v3_plus(init2, v3_times(vec2, v));
            Vector3D result = v3_times(v3_plus(result1, result2), 0.5);
            if (result.x >= MIN_X && result.x <= MAX_X && result.y >= MIN_Y && result.y <= MAX_Y &&
                result.z >= MIN_Z && result.z <= MAX_Z) {
                *out = result;
                return 1;
            }
        }
        return 0;
    }
    double dist = v3_magnitude(v3_minus(init2, init1));
    if (dist <= radius) {
        *out = v3_times(v3_plus(init1, init2), 0.5);
        return 1;
    }
    return 0;
}

static int is_in_voxel(Vector2D voxel, Motion motion) {
    if (voxel.x > MAX_X || voxel.x < MIN_X || voxel.y > MAX_Y || voxel.y < MIN_Y) {
        return 0;
    }
    Vector3D init = motion.pos_one;
    Vector3D fin = motion.pos_two;
    double v_s = GOOD_VOXEL_SIZE;
    double r = PROXIMITY_RADIUS / 2.0;

    double v_x = voxel.x;
    double x0 = init.x;
    double xv = fin.x - init.x;
    double v_y = voxel.y;
    double y0 = init.y;
    double yv = fin.y - init.y;

    double low_x = (v_x - r - x0) / xv;
    double high_x = (v_x + v_s + r - x0) / xv;
    if (xv < 0.0) {
        double tmp = low_x;
        low_x = high_x;
        high_x = tmp;
    }
    double low_y = (v_y - r - y0) / yv;
    double high_y = (v_y + v_s + r - y0) / yv;
    if (yv < 0.0) {
        double tmp = low_y;
        low_y = high_y;
        high_y = tmp;
    }

    return (((xv == 0.0 && v_x <= x0 + r && x0 - r <= v_x + v_s) ||
             (low_x <= 1.0 && 1.0 <= high_x) || (low_x <= 0.0 && 0.0 <= high_x) ||
             (0.0 <= low_x && high_x <= 1.0)) &&
            ((yv == 0.0 && v_y <= y0 + r && y0 - r <= v_y + v_s) ||
             ((low_y <= 1.0 && 1.0 <= high_y) || (low_y <= 0.0 && 0.0 <= high_y) ||
              (0.0 <= low_y && high_y <= 1.0))) &&
            (xv == 0.0 || yv == 0.0 || (low_y <= high_x && high_x <= high_y) ||
             (low_y <= low_x && low_x <= high_y) || (low_x <= low_y && high_y <= high_x)));
}

static Vector2D voxel_hash(Vector3D position) {
    int x_div = (int)(position.x / GOOD_VOXEL_SIZE);
    int y_div = (int)(position.y / GOOD_VOXEL_SIZE);
    double x = GOOD_VOXEL_SIZE * x_div;
    double y = GOOD_VOXEL_SIZE * y_div;
    if (position.x < 0) {
        x -= GOOD_VOXEL_SIZE;
    }
    if (position.y < 0) {
        y -= GOOD_VOXEL_SIZE;
    }
    return v2(x, y);
}

// 每个体素桶里的 motion 列表 (对应上游 Vector<Motion>)
typedef struct {
    Motion *items;
    int size;
    int capacity;
} MotionList;

static void list_append(MotionList *list, Motion motion) {
    if (list->size == list->capacity) {
        list->capacity = list->capacity == 0 ? 4 : list->capacity * 2;
        list->items = realloc(list->items, (size_t)list->capacity * sizeof(Motion));
    }
    list->items[list->size++] = motion;
}

// 释放体素桶: 先释放 items 缓冲, 再释放 MotionList 本身
static void motion_list_free(void *value) {
    MotionList *list = value;
    free(list->items);
    free(list);
}

static void put_into_map(RBTree *voxel_map, Vector2D voxel, Motion motion) {
    Key key = key_voxel(voxel);
    MotionList *list = rb_get(voxel_map, key);
    if (list == NULL) {
        list = malloc(sizeof(MotionList));
        list->items = NULL;
        list->size = 0;
        list->capacity = 0;
        rb_put(voxel_map, key, list);
    }
    list_append(list, motion);
}

static void recurse(RBTree *voxel_map, RBTree *seen, Vector2D next_voxel, Motion motion) {
    if (!is_in_voxel(next_voxel, motion)) {
        return;
    }
    static char marker = 0;
    if (rb_put(seen, key_voxel(next_voxel), &marker) == &marker) {
        return;
    }
    put_into_map(voxel_map, next_voxel, motion);

    Vector2D horizontal = v2(GOOD_VOXEL_SIZE, 0.0);
    Vector2D vertical = v2(0.0, GOOD_VOXEL_SIZE);
    recurse(voxel_map, seen, v2_minus(next_voxel, horizontal), motion);
    recurse(voxel_map, seen, v2_plus(next_voxel, horizontal), motion);
    recurse(voxel_map, seen, v2_minus(next_voxel, vertical), motion);
    recurse(voxel_map, seen, v2_plus(next_voxel, vertical), motion);
    recurse(voxel_map, seen, v2_minus(v2_minus(next_voxel, horizontal), vertical), motion);
    recurse(voxel_map, seen, v2_plus(v2_minus(next_voxel, horizontal), vertical), motion);
    recurse(voxel_map, seen, v2_minus(v2_plus(next_voxel, horizontal), vertical), motion);
    recurse(voxel_map, seen, v2_plus(v2_plus(next_voxel, horizontal), vertical), motion);
}

// 收集体素桶 (只为 forEach 用; 上游按树序收集, 碰撞计数与顺序无关)
typedef struct {
    MotionList **buckets;
    int size;
    int capacity;
} BucketVec;

static void collect_bucket(Key key, void *value, void *ctx) {
    (void)key;
    BucketVec *vec = ctx;
    MotionList *list = value;
    if (list->size > 1) {
        if (vec->size == vec->capacity) {
            vec->capacity = vec->capacity == 0 ? 8 : vec->capacity * 2;
            vec->buckets = realloc(vec->buckets, (size_t)vec->capacity * sizeof(MotionList *));
        }
        vec->buckets[vec->size++] = list;
    }
}

static void draw_motion_on_voxel_map(RBTree *voxel_map, Motion motion) {
    RBTree seen;
    seen.root = NULL;
    recurse(voxel_map, &seen, voxel_hash(motion.pos_one), motion);
    rb_free_all(&seen);
}

typedef struct {
    int callsign_a;
    int callsign_b;
    Vector3D position;
} Collision;

typedef struct {
    Collision *items;
    int size;
    int capacity;
} CollisionVec;

static void collisions_push(CollisionVec *vec, Collision collision) {
    if (vec->size == vec->capacity) {
        vec->capacity = vec->capacity == 0 ? 8 : vec->capacity * 2;
        vec->items = realloc(vec->items, (size_t)vec->capacity * sizeof(Collision));
    }
    vec->items[vec->size++] = collision;
}

// ── 检测器 ──
typedef struct {
    RBTree state;  // CallSign → Vector3D*
} Detector;

static void detector_init(Detector *detector) { detector->state.root = NULL; }

// 释放 state 中所有 Vector3D 值与树节点
static void detector_destroy(Detector *detector) { rb_free_all_with(&detector->state, free); }

// 收集 state 中本帧未出现的键 (上游 toRemove 的 forEach)
typedef struct {
    int *items;
    int size;
    RBTree *seen;
} RemoveCtx;

static void collect_removable(Key key, void *value, void *ctx) {
    (void)value;
    RemoveCtx *remove_ctx = ctx;
    if (rb_get(remove_ctx->seen, key) == NULL) {
        remove_ctx->items[remove_ctx->size++] = key.sign;
    }
}

typedef struct {
    int callsign;
    Vector3D position;
} Aircraft;

static int handle_new_frame(Detector *detector, Aircraft *frame, int frame_size) {
    Motion *motions = malloc((size_t)frame_size * sizeof(Motion));
    RBTree seen;
    seen.root = NULL;
    static char marker = 0;

    for (int i = 0; i < frame_size; i++) {
        Key key = key_sign(frame[i].callsign);
        // 上游存的是不可变 Vector3D 对象的引用; 这里必须存值拷贝 (帧数组会被复用)
        Vector3D *stored = malloc(sizeof(Vector3D));
        *stored = frame[i].position;
        Vector3D *old_position = rb_put(&detector->state, key, stored);
        Vector3D new_position = frame[i].position;
        rb_put(&seen, key, &marker);
        Motion motion;
        motion.callsign = frame[i].callsign;
        if (old_position == NULL) {
            motion.pos_one = new_position;  // 新出现的飞机按静止处理
        } else {
            motion.pos_one = *old_position;
            free(old_position);  // 被替换的旧位置已归本树所有
        }
        motion.pos_two = new_position;
        motions[i] = motion;
    }

    // 移除本帧不再出现的飞机
    int *to_remove = malloc((size_t)frame_size * sizeof(int));
    RemoveCtx remove_ctx = {to_remove, 0, &seen};
    rb_foreach(&detector->state, collect_removable, &remove_ctx);
    for (int i = 0; i < remove_ctx.size; i++) {
        free(rb_remove(&detector->state, key_sign(to_remove[i])));
    }
    free(to_remove);

    // 体素归并
    RBTree voxel_map;
    voxel_map.root = NULL;
    for (int i = 0; i < frame_size; i++) {
        draw_motion_on_voxel_map(&voxel_map, motions[i]);
    }
    BucketVec buckets = {NULL, 0, 0};
    rb_foreach(&voxel_map, collect_bucket, &buckets);

    CollisionVec collisions = {NULL, 0, 0};
    for (int b = 0; b < buckets.size; b++) {
        MotionList *list = buckets.buckets[b];
        for (int i = 0; i < list->size; i++) {
            for (int j = i + 1; j < list->size; j++) {
                Vector3D collision;
                if (find_intersection(list->items[i], list->items[j], &collision)) {
                    Collision c;
                    c.callsign_a = list->items[i].callsign;
                    c.callsign_b = list->items[j].callsign;
                    c.position = collision;
                    collisions_push(&collisions, c);
                }
            }
        }
    }

    int count = collisions.size;
    free(collisions.items);
    free(buckets.buckets);
    rb_free_all_with(&voxel_map, motion_list_free);
    rb_free_all(&seen);
    free(motions);
    return count;
}

static int benchmark(int num_aircrafts) {
    int actual_collisions = 0;
    Detector detector;
    detector_init(&detector);
    Aircraft *frame = malloc((size_t)num_aircrafts * sizeof(Aircraft));
    for (int i = 0; i < NUM_FRAMES; i++) {
        double time = i / 10.0;
        for (int k = 0; k < num_aircrafts; k += 2) {
            frame[k].callsign = k;
            frame[k].position = v3(time, cos(time) * 2 + k * 3, 10);
            frame[k + 1].callsign = k + 1;
            frame[k + 1].position = v3(time, sin(time) * 2 + k * 3, 10);
        }
        actual_collisions += handle_new_frame(&detector, frame, num_aircrafts);
    }
    free(frame);
    detector_destroy(&detector);
    return actual_collisions;
}

int main(int argc, char **argv) {
    int num_aircrafts = argc > 1 ? atoi(argv[1]) : 100;
    int iterations = argc > 2 ? atoi(argv[2]) : 1;
    int total = 0;
    for (int i = 0; i < iterations; i++) {
        total += benchmark(num_aircrafts);
    }
    printf("%d\n", total);
    return 0;
}
