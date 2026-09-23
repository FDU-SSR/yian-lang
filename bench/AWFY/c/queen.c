#include <assert.h>
#include <stdlib.h>

static int is_safe(int r, int c, const int *free_rows, const int *free_maxs,
                   const int *free_mins) {
    return free_rows[r] && free_maxs[r + c] && free_mins[c - r + 7];
}

static void update_board_state(int r, int c, int value, int *free_rows,
                               int *free_maxs, int *free_mins) {
    free_rows[r] = value;
    free_maxs[c + r] = value;
    free_mins[c - r + 7] = value;
}

static int place_queen(int c, int *free_rows, int *free_maxs, int *free_mins,
                       int *queen_rows) {
    for (int r = 0; r < 8; ++r) {
        if (is_safe(r, c, free_rows, free_maxs, free_mins)) {
            queen_rows[r] = c;
            update_board_state(r, c, 0, free_rows, free_maxs, free_mins);
            if (c == 7) {
                return 1;
            }
            if (place_queen(c + 1, free_rows, free_maxs, free_mins, queen_rows)) {
                return 1;
            }
            update_board_state(r, c, 1, free_rows, free_maxs, free_mins);
        }
    }
    return 0;
}

static int solve_queens_once(void) {
    int *free_rows = malloc(sizeof(int) * 8);
    int *free_maxs = malloc(sizeof(int) * 16);
    int *free_mins = malloc(sizeof(int) * 16);
    int *queen_rows = malloc(sizeof(int) * 8);
    assert(free_rows != NULL && free_maxs != NULL && free_mins != NULL && queen_rows != NULL);

    for (int i = 0; i < 8; ++i) {
        free_rows[i] = 1;
        queen_rows[i] = -1;
    }
    for (int i = 0; i < 16; ++i) {
        free_maxs[i] = 1;
        free_mins[i] = 1;
    }

    int result = place_queen(0, free_rows, free_maxs, free_mins, queen_rows);
    free(free_rows);
    free(free_maxs);
    free(free_mins);
    free(queen_rows);
    return result;
}

static int benchmark_queens(void) {
    int result = 1;
    for (int i = 0; i < 10; ++i) {
        result = result && solve_queens_once();
    }
    return result;
}

int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 276923;
    for (int i = 0; i < iterations; ++i) {
        assert(benchmark_queens() && "queens Error");
    }
    return 0;
}
