#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

typedef struct TowersDisk {
    int size;
    struct TowersDisk* next;
} TowersDisk;

typedef struct TowersState {
    TowersDisk* piles[3];
    int moves_done;
} TowersState;

TowersState state = {{NULL, NULL, NULL}, 0};

void push_disk(TowersDisk* disk, int pile) {
    if (disk == NULL) {
        return;
    }
    TowersDisk* top = state.piles[pile];
    if (top != NULL) {
        if (disk->size >= top->size) {
            fprintf(stderr, "Cannot put a big disk on a smaller one\n");
            exit(1);
        }
    }
    
    disk->next = top;
    state.piles[pile] = disk;
}

TowersDisk* pop_disk_from(int pile) {
    TowersDisk* top = state.piles[pile];
    if (top == NULL) {
        fprintf(stderr, "Attempting to remove a disk from an empty pile\n");
        exit(1);    
    }
    state.piles[pile] = top->next;
    top->next = NULL;
    return top;
}

void move_top_disk(int from_pile, int to_pile) {
    TowersDisk* disk = pop_disk_from(from_pile);
    push_disk(disk, to_pile);
    state.moves_done = state.moves_done + 1;
}

void build_tower_at(int pile, int disks) {
    for (int i = disks; i >= 0; i = i - 1) {
        TowersDisk* disk = (TowersDisk*)malloc(sizeof(TowersDisk));
        if (disk == NULL) {
            fprintf(stderr, "malloc failed\n");
            exit(1);
        }
        disk->size = i;
        disk->next = NULL;
        push_disk(disk, pile);
    }
}

void move_disks(int disks, int from_pile, int to_pile) {
    if (disks == 1) {
        move_top_disk(from_pile, to_pile);
    } else {
        int other_pile = (3 - from_pile) - to_pile;
        move_disks(disks - 1, from_pile, other_pile);
        move_top_disk(from_pile, to_pile);
        move_disks(disks - 1, other_pile, to_pile);
    }
}

void free_pile(TowersDisk* disk) {
    while (disk != NULL) {
        TowersDisk* next = disk->next;
        free(disk);
        disk = next;
    }
}

int benchmark() {
    for (int i = 0; i < 3; i = i + 1) {
        state.piles[i] = NULL;
    }
    
    state.moves_done = 0;
    build_tower_at(0, 30);
    move_disks(30, 0, 1);
    
    for (int i = 0; i < 3; i = i + 1) {
        if (state.piles[i] != NULL) {
            free_pile(state.piles[i]);
        }
    }
    
    return state.moves_done;
}

int main() {
    int result = benchmark();
    assert(result == 1073741823 && "towers moves_done should be 1073741823");
    return 0;
}