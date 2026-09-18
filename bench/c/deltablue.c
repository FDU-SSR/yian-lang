// bench/c/deltablue.c — DeltaBlue 增量约束求解器 (AWFY 宏基准) 的 C 参考实现
//
// 语义与规模与 bench/shootout/deltablue.an 对齐; 只用于跨语言正确性对照, 不参与
// fat/raw 计时。
// 上游: AWFY (are-we-fast-yet) benchmarks/Java/src/DeltaBlue.java + deltablue/*.java
//       (MIT, Copyright (c) 2001-2016 Stefan Marr; 源自 Mario Wolczko 的 Java/Smalltalk
//        版本, 见 AWFY LICENSE.md)
// 说明: 上游的约束类层次 (AbstractConstraint -> Unary/Binary -> Equality/Scale/Stay/Edit)
//       在 .an 里是 tagged enum + match, 在 C 里等价为一个带 kind 标签的结构体 + switch;
//       强度 Strength 用 arithmeticValue (int) 直接表示。
// 校验值: N=100, ITER=14000 时输出 4993240000 (= PER_RUN 356660 * 14000), 与 .an 断言一致。
// 用法: ./deltablue [N [ITER]]   (默认 N=100, ITER=14000)
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

// ── Strength (上游 deltablue/Strength.java) ──
#define S_ABSOLUTE_WEAKEST 10000
#define S_REQUIRED (-800)
#define S_STRONG_DEFAULT (-200)
#define S_DEFAULT 0
#define S_PREFERRED (-400)

static bool strength_stronger(int a, int b) { return a < b; }
static bool strength_weaker(int a, int b) { return a > b; }
static int strength_weakest(int a, int b) { return a > b ? a : b; }

// ── Direction (上游 deltablue/Direction.java); UNSET 表示上游的 null ──
typedef enum { FLOW_FORWARD, FLOW_BACKWARD, FLOW_UNSET } Flow;

typedef struct Variable Variable;
typedef struct Constraint Constraint;

// ── Variable (上游 deltablue/Variable.java) ──
struct Variable {
    int value;
    Constraint **constraints;
    int constraint_count;
    int constraint_capacity;
    Constraint *determined_by;  // 上游 null 表示无
    int mark;
    int walk_strength;
    bool stay;
};

// ── Constraint (上游 AbstractConstraint + Unary/Binary + Equality/Scale/Stay/Edit) ──
typedef enum { C_EQUALITY, C_SCALE, C_STAY, C_EDIT } ConstraintKind;

struct Constraint {
    ConstraintKind kind;
    uint64_t id;    // 唯一 id, 替代上游的引用身份比较
    int strength;   // Strength.arithmeticValue
    Variable *v1;   // Equality/Scale: 源
    Variable *v2;   // Equality/Scale: 目的
    Variable *scale;
    Variable *offset;
    Variable *output;  // Stay/Edit
    Flow direction;    // Equality/Scale
    bool satisfied;    // Stay/Edit
};

// ── Planner ──
typedef struct {
    int current_mark;
    uint64_t next_id;
} Planner;

// ── 动态数组 (对应 std.core.vec 的 Vec<Constraint&> / Vec<Variable&>) ──
typedef struct {
    Constraint **items;
    int count;
    int capacity;
} ConstraintVec;

typedef struct {
    Variable **items;
    int count;
    int capacity;
} VariableVec;

static void cvec_push(ConstraintVec *v, Constraint *c) {
    if (v->count == v->capacity) {
        v->capacity = v->capacity == 0 ? 4 : v->capacity * 2;
        v->items = realloc(v->items, (size_t)v->capacity * sizeof(Constraint *));
    }
    v->items[v->count++] = c;
}

static void vvec_push(VariableVec *v, Variable *x) {
    if (v->count == v->capacity) {
        v->capacity = v->capacity == 0 ? 4 : v->capacity * 2;
        v->items = realloc(v->items, (size_t)v->capacity * sizeof(Variable *));
    }
    v->items[v->count++] = x;
}

// ── Variable 操作 ──
static Variable *variable_new(int value) {
    Variable *v = malloc(sizeof(Variable));
    v->value = value;
    v->constraints = NULL;
    v->constraint_count = 0;
    v->constraint_capacity = 0;
    v->determined_by = NULL;
    v->mark = 0;
    v->walk_strength = S_ABSOLUTE_WEAKEST;
    v->stay = true;
    return v;
}

static void variable_add_constraint(Variable *v, Constraint *c) {
    if (v->constraint_count == v->constraint_capacity) {
        v->constraint_capacity = v->constraint_capacity == 0 ? 4 : v->constraint_capacity * 2;
        v->constraints = realloc(v->constraints, (size_t)v->constraint_capacity * sizeof(Constraint *));
    }
    v->constraints[v->constraint_count++] = c;
}

// 上游 Vector.remove(c) (按对象身份移除第一个匹配项) + determinedBy 复位
static void variable_remove_constraint(Variable *v, Constraint *c) {
    int idx = -1;
    for (int i = 0; i < v->constraint_count; i++) {
        if (idx < 0 && v->constraints[i]->id == c->id) {
            idx = i;
        }
    }
    if (idx >= 0) {
        for (int i = idx; i + 1 < v->constraint_count; i++) {
            v->constraints[i] = v->constraints[i + 1];
        }
        v->constraint_count--;
    }
    if (v->determined_by != NULL && v->determined_by->id == c->id) {
        v->determined_by = NULL;
    }
}

// ── Constraint 方法 ──
static bool constraint_is_input(Constraint *c) { return c->kind == C_EDIT; }

static bool constraint_is_satisfied(Constraint *c) {
    if (c->kind == C_EQUALITY || c->kind == C_SCALE) {
        return c->direction != FLOW_UNSET;
    }
    return c->satisfied;
}

// BinaryConstraint.chooseMethod (Equality/Scale 共用), 返回选定方向
static Flow binary_choose(int strength, Variable *v1, Variable *v2, int mark) {
    if (v1->mark == mark) {
        if (v2->mark != mark && strength_stronger(strength, v2->walk_strength)) {
            return FLOW_FORWARD;
        }
        return FLOW_UNSET;
    }
    if (v2->mark == mark) {
        if (v1->mark != mark && strength_stronger(strength, v1->walk_strength)) {
            return FLOW_BACKWARD;
        }
        return FLOW_UNSET;
    }
    if (strength_weaker(v1->walk_strength, v2->walk_strength)) {
        if (strength_stronger(strength, v1->walk_strength)) {
            return FLOW_BACKWARD;
        }
        return FLOW_UNSET;
    }
    if (strength_stronger(strength, v2->walk_strength)) {
        return FLOW_FORWARD;
    }
    return FLOW_UNSET;
}

// 上游 chooseMethod
static void constraint_choose_method(Constraint *c, int mark) {
    switch (c->kind) {
        case C_EQUALITY:
        case C_SCALE:
            c->direction = binary_choose(c->strength, c->v1, c->v2, mark);
            break;
        case C_STAY:
        case C_EDIT:
            c->satisfied = c->output->mark != mark &&
                           strength_stronger(c->strength, c->output->walk_strength);
            break;
    }
}

// 上游 addToGraph
static void constraint_add_to_graph(Constraint *c) {
    switch (c->kind) {
        case C_EQUALITY:
            variable_add_constraint(c->v1, c);
            variable_add_constraint(c->v2, c);
            c->direction = FLOW_UNSET;
            break;
        case C_SCALE:
            variable_add_constraint(c->v1, c);
            variable_add_constraint(c->v2, c);
            variable_add_constraint(c->scale, c);
            variable_add_constraint(c->offset, c);
            c->direction = FLOW_UNSET;
            break;
        case C_STAY:
        case C_EDIT:
            variable_add_constraint(c->output, c);
            c->satisfied = false;
            break;
    }
}

// 上游 execute
static void constraint_execute(Constraint *c) {
    switch (c->kind) {
        case C_EQUALITY:
            if (c->direction == FLOW_FORWARD) {
                c->v2->value = c->v1->value;
            } else if (c->direction == FLOW_BACKWARD) {
                c->v1->value = c->v2->value;
            }
            break;
        case C_SCALE:
            if (c->direction == FLOW_FORWARD) {
                c->v2->value = c->v1->value * c->scale->value + c->offset->value;
            } else if (c->direction == FLOW_BACKWARD) {
                c->v1->value = (c->v2->value - c->offset->value) / c->scale->value;
            }
            break;
        case C_STAY:
        case C_EDIT:
            break;
    }
}

// 上游 inputsDo(in -> in.setMark(mark))
static void constraint_mark_inputs(Constraint *c, int mark) {
    switch (c->kind) {
        case C_EQUALITY:
            if (c->direction == FLOW_FORWARD) {
                c->v1->mark = mark;
            } else {
                c->v2->mark = mark;
            }
            break;
        case C_SCALE:
            if (c->direction == FLOW_FORWARD) {
                c->v1->mark = mark;
            } else {
                c->v2->mark = mark;
            }
            c->scale->mark = mark;
            c->offset->mark = mark;
            break;
        case C_STAY:
        case C_EDIT:
            break;
    }
}

// 上游 inputsKnown(mark)
static bool variable_known(Variable *v, int mark) {
    return v->mark == mark || v->stay || v->determined_by == NULL;
}

static bool constraint_inputs_known(Constraint *c, int mark) {
    switch (c->kind) {
        case C_EQUALITY: {
            Variable *input = c->direction == FLOW_FORWARD ? c->v1 : c->v2;
            return variable_known(input, mark);
        }
        case C_SCALE: {
            Variable *input = c->direction == FLOW_FORWARD ? c->v1 : c->v2;
            return variable_known(input, mark) && variable_known(c->scale, mark) &&
                   variable_known(c->offset, mark);
        }
        case C_STAY:
        case C_EDIT:
            return true;
    }
    return true;
}

// 上游 markUnsatisfied
static void constraint_mark_unsatisfied(Constraint *c) {
    if (c->kind == C_EQUALITY || c->kind == C_SCALE) {
        c->direction = FLOW_UNSET;
    } else {
        c->satisfied = false;
    }
}

// 上游 getOutput
static Variable *constraint_get_output(Constraint *c) {
    switch (c->kind) {
        case C_EQUALITY:
        case C_SCALE:
            return c->direction == FLOW_FORWARD ? c->v2 : c->v1;
        case C_STAY:
        case C_EDIT:
            return c->output;
    }
    return c->v1;
}

// 上游 removeFromGraph
static void constraint_remove_from_graph(Constraint *c) {
    switch (c->kind) {
        case C_EQUALITY:
            variable_remove_constraint(c->v1, c);
            variable_remove_constraint(c->v2, c);
            c->direction = FLOW_UNSET;
            break;
        case C_SCALE:
            variable_remove_constraint(c->v1, c);
            variable_remove_constraint(c->v2, c);
            variable_remove_constraint(c->scale, c);
            variable_remove_constraint(c->offset, c);
            c->direction = FLOW_UNSET;
            break;
        case C_STAY:
        case C_EDIT:
            variable_remove_constraint(c->output, c);
            c->satisfied = false;
            break;
    }
}

// 上游 recalculate
static void constraint_recalculate(Constraint *c) {
    switch (c->kind) {
        case C_EQUALITY: {
            Variable *in = c->direction == FLOW_FORWARD ? c->v1 : c->v2;
            Variable *out = c->direction == FLOW_FORWARD ? c->v2 : c->v1;
            out->walk_strength = strength_weakest(c->strength, in->walk_strength);
            out->stay = in->stay;
            if (out->stay) {
                constraint_execute(c);
            }
            break;
        }
        case C_SCALE: {
            Variable *in = c->direction == FLOW_FORWARD ? c->v1 : c->v2;
            Variable *out = c->direction == FLOW_FORWARD ? c->v2 : c->v1;
            out->walk_strength = strength_weakest(c->strength, in->walk_strength);
            out->stay = in->stay && c->scale->stay && c->offset->stay;
            if (out->stay) {
                constraint_execute(c);
            }
            break;
        }
        case C_STAY:
        case C_EDIT:
            c->output->walk_strength = c->strength;
            c->output->stay = !constraint_is_input(c);
            if (c->output->stay) {
                constraint_execute(c);
            }
            break;
    }
}

// ── Planner ──
static Planner *planner_new(void) {
    Planner *p = malloc(sizeof(Planner));
    p->current_mark = 1;
    p->next_id = 1;
    return p;
}

static uint64_t planner_new_constraint_id(Planner *p) {
    p->next_id += 1;
    return p->next_id;
}

static int planner_new_mark(Planner *p) {
    p->current_mark += 1;
    return p->current_mark;
}

static bool planner_add_propagate(Planner *p, Constraint *c, int mark);

// 上游 incrementalRemove
static void planner_incremental_remove(Planner *p, Constraint *c);

// 上游 AbstractConstraint.satisfy
static bool constraint_satisfy(Constraint *c, int mark, Planner *p, Constraint **overridden_out);

// 上游 addConstraintsConsumingTo (闭包 -> 显式循环)
static void add_constraints_consuming_to(Variable *v, ConstraintVec *coll) {
    Constraint *determining = v->determined_by;
    for (int i = 0; i < v->constraint_count; i++) {
        Constraint *c = v->constraints[i];
        bool consuming = constraint_is_satisfied(c);
        if (determining != NULL && determining->id == c->id) {
            consuming = false;
        }
        if (consuming) {
            cvec_push(coll, c);
        }
    }
}

// 上游 incrementalAdd
static void planner_incremental_add(Planner *p, Constraint *c) {
    int mark = planner_new_mark(p);
    Constraint *cur = NULL;
    if (constraint_satisfy(c, mark, p, &cur)) {
        while (cur != NULL) {
            Constraint *overridden = cur;
            cur = NULL;
            constraint_satisfy(overridden, mark, p, &cur);
        }
    }
}

// 上游 removePropagateFrom
static ConstraintVec planner_remove_propagate_from(Planner *p, Variable *out) {
    ConstraintVec unsatisfied = {NULL, 0, 0};
    (void)p;

    out->determined_by = NULL;
    out->walk_strength = S_ABSOLUTE_WEAKEST;
    out->stay = true;

    VariableVec todo = {NULL, 0, 0};
    vvec_push(&todo, out);

    int head = 0;
    while (head < todo.count) {
        Variable *v = todo.items[head];
        head += 1;

        for (int i = 0; i < v->constraint_count; i++) {
            Constraint *c = v->constraints[i];
            if (!constraint_is_satisfied(c)) {
                cvec_push(&unsatisfied, c);
            }
        }

        Constraint *determining = v->determined_by;
        for (int j = 0; j < v->constraint_count; j++) {
            Constraint *c = v->constraints[j];
            bool consuming = constraint_is_satisfied(c);
            if (determining != NULL && determining->id == c->id) {
                consuming = false;
            }
            if (consuming) {
                constraint_recalculate(c);
                vvec_push(&todo, constraint_get_output(c));
            }
        }
    }
    free(todo.items);

    // unsatisfied.sort((c1,c2) -> c1.strength.stronger(c2.strength) ? -1 : 1)
    // 稳定插入排序: 强者在前, 等强度保持入队顺序
    for (int i = 1; i < unsatisfied.count; i++) {
        Constraint *key = unsatisfied.items[i];
        int key_strength = key->strength;
        int j = i - 1;
        while (j >= 0 && strength_weaker(unsatisfied.items[j]->strength, key_strength)) {
            unsatisfied.items[j + 1] = unsatisfied.items[j];
            j -= 1;
        }
        unsatisfied.items[j + 1] = key;
    }
    return unsatisfied;
}

// 上游 incrementalAdd 调用的 satisfy (返回 overridden); 返回 true 表示已满足
static bool constraint_satisfy(Constraint *c, int mark, Planner *p, Constraint **overridden_out) {
    constraint_choose_method(c, mark);

    if (constraint_is_satisfied(c)) {
        constraint_mark_inputs(c, mark);

        Variable *out = constraint_get_output(c);
        Constraint *overridden = out->determined_by;
        if (overridden != NULL) {
            constraint_mark_unsatisfied(overridden);
        }
        out->determined_by = c;
        if (!planner_add_propagate(p, c, mark)) {
            fprintf(stderr, "Cycle encountered\n");
            abort();
        }
        out->mark = mark;
        *overridden_out = overridden;
        return true;
    }

    if (c->strength == S_REQUIRED) {
        fprintf(stderr, "Could not satisfy a required constraint\n");
        abort();
    }
    *overridden_out = NULL;
    return false;
}

static void planner_incremental_remove(Planner *p, Constraint *c) {
    Variable *out = constraint_get_output(c);
    constraint_mark_unsatisfied(c);
    constraint_remove_from_graph(c);

    ConstraintVec unsatisfied = planner_remove_propagate_from(p, out);
    for (int i = 0; i < unsatisfied.count; i++) {
        planner_incremental_add(p, unsatisfied.items[i]);
    }
    free(unsatisfied.items);
}

// 上游 addPropagate
static bool planner_add_propagate(Planner *p, Constraint *c, int mark) {
    ConstraintVec todo = {NULL, 0, 0};
    cvec_push(&todo, c);

    int head = 0;
    while (head < todo.count) {
        Constraint *d = todo.items[head];
        head += 1;

        Variable *out = constraint_get_output(d);
        if (out->mark == mark) {
            free(todo.items);
            planner_incremental_remove(p, c);
            return false;
        }
        constraint_recalculate(d);
        add_constraints_consuming_to(out, &todo);
    }
    free(todo.items);
    return true;
}

// 上游 makePlan: 接管 sources 数组
static ConstraintVec planner_make_plan(Planner *p, ConstraintVec sources) {
    int mark = planner_new_mark(p);
    ConstraintVec plan = {NULL, 0, 0};

    int head = 0;
    while (head < sources.count) {
        Constraint *c = sources.items[head];
        head += 1;

        Variable *out = constraint_get_output(c);
        if (out->mark != mark && constraint_inputs_known(c, mark)) {
            cvec_push(&plan, c);
            out->mark = mark;
            add_constraints_consuming_to(out, &sources);
        }
    }
    free(sources.items);
    return plan;
}

// 上游 extractPlanFromConstraints
static ConstraintVec planner_extract_plan_from_constraints(Planner *p, ConstraintVec constraints) {
    ConstraintVec sources = {NULL, 0, 0};
    for (int i = 0; i < constraints.count; i++) {
        Constraint *c = constraints.items[i];
        if (constraint_is_input(c) && constraint_is_satisfied(c)) {
            cvec_push(&sources, c);
        }
    }
    return planner_make_plan(p, sources);
}

static void execute_plan(ConstraintVec plan) {
    for (int i = 0; i < plan.count; i++) {
        constraint_execute(plan.items[i]);
    }
}

// ── 构造 (上游各约束构造函数: 建图 + incrementalAdd) ──
static Constraint *constraint_alloc(ConstraintKind kind, int strength) {
    Constraint *c = malloc(sizeof(Constraint));
    c->kind = kind;
    c->id = 0;
    c->strength = strength;
    c->v1 = NULL;
    c->v2 = NULL;
    c->scale = NULL;
    c->offset = NULL;
    c->output = NULL;
    c->direction = FLOW_UNSET;
    c->satisfied = false;
    return c;
}

static Constraint *constraint_equality(Variable *v1, Variable *v2, int strength, Planner *p) {
    Constraint *c = constraint_alloc(C_EQUALITY, strength);
    c->id = planner_new_constraint_id(p);
    c->v1 = v1;
    c->v2 = v2;
    constraint_add_to_graph(c);
    planner_incremental_add(p, c);
    return c;
}

static Constraint *constraint_scale(Variable *v1, Variable *scale, Variable *offset, Variable *v2,
                                    int strength, Planner *p) {
    Constraint *c = constraint_alloc(C_SCALE, strength);
    c->id = planner_new_constraint_id(p);
    c->v1 = v1;
    c->v2 = v2;
    c->scale = scale;
    c->offset = offset;
    constraint_add_to_graph(c);
    planner_incremental_add(p, c);
    return c;
}

static Constraint *constraint_stay(Variable *output, int strength, Planner *p) {
    Constraint *c = constraint_alloc(C_STAY, strength);
    c->id = planner_new_constraint_id(p);
    c->output = output;
    constraint_add_to_graph(c);
    planner_incremental_add(p, c);
    return c;
}

static Constraint *constraint_edit(Variable *output, int strength, Planner *p) {
    Constraint *c = constraint_alloc(C_EDIT, strength);
    c->id = planner_new_constraint_id(p);
    c->output = output;
    constraint_add_to_graph(c);
    planner_incremental_add(p, c);
    return c;
}

// 上游 AbstractConstraint.destroyConstraint
static void constraint_destroy(Constraint *c, Planner *p) {
    if (constraint_is_satisfied(c)) {
        planner_incremental_remove(p, c);
    }
    constraint_remove_from_graph(c);
}

// 上游 Planner.change
static void planner_change(Planner *p, Variable *var, int new_value) {
    Constraint *edit_c = constraint_edit(var, S_PREFERRED, p);

    ConstraintVec edit_v = {NULL, 0, 0};
    cvec_push(&edit_v, edit_c);
    ConstraintVec plan = planner_extract_plan_from_constraints(p, edit_v);

    for (int i = 0; i < 10; i++) {
        var->value = new_value;
        execute_plan(plan);
    }
    free(plan.items);

    constraint_destroy(edit_c, p);
    free(edit_c);
}

// 释放辅助 (Java 侧靠 GC)
static void free_constraints(ConstraintVec cons) {
    for (int i = 0; i < cons.count; i++) {
        free(cons.items[i]);
    }
    free(cons.items);
}

static void free_variables(VariableVec vars) {
    for (int i = 0; i < vars.count; i++) {
        free(vars.items[i]->constraints);
        free(vars.items[i]);
    }
    free(vars.items);
}

// 上游 Planner.chainTest: 返回校验值 (100 次迭代 vars[n] 之和, 恒为 4950)
static int64_t planner_chain_test(int n) {
    Planner *planner = planner_new();

    VariableVec vars = {NULL, 0, 0};
    for (int i = 0; i <= n; i++) {
        vvec_push(&vars, variable_new(0));
    }

    ConstraintVec cons = {NULL, 0, 0};
    for (int k = 0; k < n; k++) {
        cvec_push(&cons, constraint_equality(vars.items[k], vars.items[k + 1], S_REQUIRED, planner));
    }
    cvec_push(&cons, constraint_stay(vars.items[n], S_STRONG_DEFAULT, planner));
    Constraint *edit_c = constraint_edit(vars.items[0], S_PREFERRED, planner);
    cvec_push(&cons, edit_c);

    ConstraintVec edit_v = {NULL, 0, 0};
    cvec_push(&edit_v, edit_c);
    ConstraintVec plan = planner_extract_plan_from_constraints(planner, edit_v);

    int64_t acc = 0;
    for (int t = 0; t < 100; t++) {
        vars.items[0]->value = t;
        execute_plan(plan);
        if (vars.items[n]->value != t) {
            fprintf(stderr, "Chain test failed!\n");
            abort();
        }
        acc += vars.items[n]->value;
    }
    free(plan.items);

    constraint_destroy(edit_c, planner);

    free_constraints(cons);
    free_variables(vars);
    free(planner);
    return acc;
}

// 上游 Planner.projectionTest: 返回校验值
static int64_t planner_projection_test(int n) {
    Planner *planner = planner_new();

    VariableVec all_vars = {NULL, 0, 0};
    ConstraintVec all_cons = {NULL, 0, 0};
    VariableVec dests = {NULL, 0, 0};

    Variable *scale = variable_new(10);
    Variable *offset = variable_new(1000);
    vvec_push(&all_vars, scale);
    vvec_push(&all_vars, offset);

    Variable *src = scale;
    Variable *dst = scale;
    for (int i = 1; i <= n; i++) {
        Variable *s = variable_new(i);
        Variable *d = variable_new(i);
        vvec_push(&all_vars, s);
        vvec_push(&all_vars, d);
        vvec_push(&dests, d);

        cvec_push(&all_cons, constraint_stay(s, S_DEFAULT, planner));
        cvec_push(&all_cons, constraint_scale(s, scale, offset, d, S_REQUIRED, planner));

        src = s;
        dst = d;
    }

    int64_t acc = 0;

    planner_change(planner, src, 17);
    if (dst->value != 1170) {
        fprintf(stderr, "Projection test 1 failed!\n");
        abort();
    }
    acc += dst->value;

    planner_change(planner, dst, 1050);
    if (src->value != 5) {
        fprintf(stderr, "Projection test 2 failed!\n");
        abort();
    }
    acc += src->value;

    planner_change(planner, scale, 5);
    for (int j = 0; j < n - 1; j++) {
        Variable *d = dests.items[j];
        if (d->value != (j + 1) * 5 + 1000) {
            fprintf(stderr, "Projection test 3 failed!\n");
            abort();
        }
        acc += d->value;
    }

    planner_change(planner, offset, 2000);
    for (int j = 0; j < n - 1; j++) {
        Variable *d = dests.items[j];
        if (d->value != (j + 1) * 5 + 2000) {
            fprintf(stderr, "Projection test 4 failed!\n");
            abort();
        }
        acc += d->value;
    }
    acc += src->value + dst->value + scale->value + offset->value;

    free(dests.items);
    free_constraints(all_cons);
    free_variables(all_vars);
    free(planner);
    return acc;
}

int main(int argc, char **argv) {
    int n = argc > 1 ? atoi(argv[1]) : 100;
    int iterations = argc > 2 ? atoi(argv[2]) : 14000;

    int64_t total = 0;
    for (int i = 0; i < iterations; i++) {
        total += planner_chain_test(n);
        total += planner_projection_test(n);
    }

    printf("%lld\n", (long long)total);
    return 0;
}
