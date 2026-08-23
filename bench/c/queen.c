#include <stdio.h>
#include <assert.h>

// 检查位置是否安全
int is_safe(int r, int c, int* free_rows, int* free_maxs, int* free_mins) {
    if (!free_rows[r]) {
        return 0;
    }
    if (!free_maxs[r + c]) {
        return 0;
    }
    if (!free_mins[r - c + (8000-1)]) {
        return 0;
    }
    return 1;
}

// 更新棋盘状态
void update_board_state(int r, int c, int value, int* free_rows, int* free_maxs, int* free_mins) {
    free_rows[r] = value;
    free_maxs[c + r] = value;
    free_mins[c - r + (8000-1)] = value;
}

// 放置皇后
int place_queen(int c, int* free_rows, int* free_maxs, int* free_mins, int* queen_rows) {
    for (int r = 0; r < 8000; r++) {
        if (is_safe(r, c, free_rows, free_maxs, free_mins)) {
            queen_rows[r] = c;
            update_board_state(r, c, 0, free_rows, free_maxs, free_mins);
            if (c == (8000-1)) {
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

// 求解八皇后问题一次
int solve_queens_once() {
    int free_rows[8000];
    int free_maxs[16000];
    int free_mins[16000];
    int queen_rows[8000];
    
    for (int i = 0; i < 8000; i++) {
        free_rows[i] = 1;
    }
    for (int i = 0; i < 16000; i++) {
        free_maxs[i] = 1;
        free_mins[i] = 1;
    }

    if (place_queen(0, free_rows, free_maxs, free_mins, queen_rows)) {
        return 1;
    }
    else {
        return 0;
    }
}

// 主函数
int main() {
    int overall_result = 0;
    for (int i = 0; i < 150; i++) {
        overall_result = overall_result + 1;
        if (!solve_queens_once()) {
            overall_result = 16000;
            break;
        }
    }
    assert(overall_result == 150 && "queens Error");
    printf("Test passed!\n");
    return 0;
}