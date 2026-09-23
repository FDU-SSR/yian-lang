#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <stdint.h>

// AWFY Towers 基准单元使用 13 个盘和 8191 次移动。外层循环重复该单元，
// 为进程级计时提供足够工作量。
#define INNER_ITERATIONS 41065

typedef struct TowersDisk {
    int32_t size;
    struct TowersDisk* next;
} TowersDisk;

typedef struct TowersState {
    TowersDisk* piles[3];
    int32_t moves_done;
} TowersState;

static inline void push_disk(TowersDisk* disk, uint64_t pile, TowersState* state) {
    TowersDisk* top = state->piles[pile];
    if (top != NULL) {
        if (disk->size >= top->size) {
            fprintf(stderr, "Cannot put a big disk on a smaller one");
            exit(1);
        }
    }

    disk->next = top;
    state->piles[pile] = disk;
}

static inline TowersDisk* pop_disk_from(uint64_t pile, TowersState* state) {
    TowersDisk* top = state->piles[pile];
    if (top == NULL) {
        fprintf(stderr, "Attempting to remove a disk from an empty pile");
        exit(1);
    }
    state->piles[pile] = top->next;
    top->next = NULL;
    return top;
}

static inline void move_top_disk(
        uint64_t from_pile, uint64_t to_pile, TowersState* state) {
    push_disk(pop_disk_from(from_pile, state), to_pile, state);
    state->moves_done = state->moves_done + 1;
}

static inline void build_tower_at(uint64_t pile, int32_t disks, TowersState* state) {
    for (int32_t i = disks; i >= 0; i = i - 1) {
        TowersDisk* disk = (TowersDisk*)malloc(sizeof(TowersDisk));
        if (disk == NULL) {
            fprintf(stderr, "malloc failed\n");
            exit(1);
        }
        disk->size = i;
        disk->next = NULL;
        push_disk(disk, pile, state);
    }
}

static inline void move_disks(
        int32_t disks, uint64_t from_pile, uint64_t to_pile, TowersState* state) {
    if (disks == 1) {
        move_top_disk(from_pile, to_pile, state);
    } else {
        uint64_t other_pile = (UINT64_C(3) - from_pile) - to_pile;
        move_disks(disks - 1, from_pile, other_pile, state);
        move_top_disk(from_pile, to_pile, state);
        move_disks(disks - 1, other_pile, to_pile, state);
    }
}

static inline void free_pile(TowersDisk* disk) {
    if (disk != NULL) {
        free_pile(disk->next);
        free(disk);
    }
}

static inline int32_t benchmark(TowersState* state) {
    for (uint64_t i = 0; i < UINT64_C(3); i = i + 1) {
        state->piles[i] = NULL;
    }

    build_tower_at(0, 13, state);
    state->moves_done = 0;
    move_disks(13, 0, 1, state);

    for (uint64_t i = 0; i < UINT64_C(3); i = i + 1) {
        free_pile(state->piles[i]);
    }

    return state->moves_done;
}

int main(int argc, char **argv) {
    int32_t iterations = argc > 1 ? atoi(argv[1]) : INNER_ITERATIONS;
    for (int32_t i = 0; i < iterations; i = i + 1) {
        TowersState* state = (TowersState*)malloc(sizeof(TowersState));
        if (state == NULL) {
            fprintf(stderr, "malloc failed\n");
            return 1;
        }
        int32_t result = benchmark(state);
        assert(result == 8191 && "towers moves_done should be 8191");
        free(state);
    }
    return 0;
}
