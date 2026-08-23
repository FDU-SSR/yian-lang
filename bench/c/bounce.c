#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <stdbool.h>

// 绝对值函数
int abs_int(int x) {
    if (x < 0) {
        return -x;
    }
    return x;
}

// 随机数生成器结构体
typedef struct {
    int seed;
} Random;

// 球结构体
typedef struct {
    int x;
    int y;
    int x_vel;
    int y_vel;
} Ball;

// 随机数结果结构体
typedef struct {
    Random random_state;
    int value;
} RandomResult;

// 球初始化结果结构体
typedef struct {
    Ball ball;
    Random random_state;
} BallInitResult;

// 弹跳结果结构体
typedef struct {
    Ball ball;
    bool bounced;
} BounceResult;

// 随机数生成器初始化
Random Random_new() {
    Random r = {74755};
    return r;
}

// 随机数生成器下一个值
RandomResult Random_next(Random* self) {
    int new_seed = ((self->seed * 1309) + 13849) & 65535;
    RandomResult result;
    result.random_state.seed = new_seed;
    result.value = new_seed;
    return result;
}

// 球初始化
BallInitResult BallInitResult_new(Random* r) {
    RandomResult res1 = Random_next(r);
    RandomResult res2 = Random_next(&res1.random_state);
    RandomResult res3 = Random_next(&res2.random_state);
    RandomResult res4 = Random_next(&res3.random_state);

    Ball new_ball;
    new_ball.x = res1.value % 500;
    new_ball.y = res2.value % 500;
    new_ball.x_vel = (res3.value % 300) - 150;
    new_ball.y_vel = (res4.value % 300) - 150;

    BallInitResult result;
    result.ball = new_ball;
    result.random_state = res4.random_state;
    return result;
}

// 球弹跳逻辑
BounceResult Ball_bounce(Ball* self) {
    int x_limit = 300;
    int y_limit = 300;
    bool bounced = false;

    int next_x = self->x + self->x_vel;
    int next_y = self->y + self->y_vel;
    int next_x_vel = self->x_vel;
    int next_y_vel = self->y_vel;

    if (next_x > x_limit) {
        next_x = x_limit;
        next_x_vel = -abs_int(self->x_vel);
        bounced = true;
    }
    if (next_x < 0) {
        next_x = 0;
        next_x_vel = abs_int(self->x_vel);
        bounced = true;
    }
    if (next_y > y_limit) {
        next_y = y_limit;
        next_y_vel = -abs_int(self->y_vel);
        bounced = true;
    }
    if (next_y < 0) {
        next_y = 0;
        next_y_vel = abs_int(self->y_vel);
        bounced = true;
    }

    Ball new_ball;
    new_ball.x = next_x;
    new_ball.y = next_y;
    new_ball.x_vel = next_x_vel;
    new_ball.y_vel = next_y_vel;

    BounceResult result;
    result.ball = new_ball;
    result.bounced = bounced;
    return result;
}

// 性能测试函数
int benchmark() {
    Random random = Random_new();

    int ball_count = 100000;
    int bounces = 0;
    Ball* balls = (Ball*)malloc(sizeof(Ball) * ball_count);

    for (int i = 0; i < ball_count; i++) {
        BallInitResult init_res = BallInitResult_new(&random);
        balls[i] = init_res.ball;
        random = init_res.random_state;
    }

    for (int j = 0; j < 10000; j++) {
        for (int i = 0; i < ball_count; i++) {
            BounceResult bounce_res = Ball_bounce(&balls[i]);
            balls[i] = bounce_res.ball;

            if (bounce_res.bounced) {
                bounces++;
            }
        }
    }

    free(balls);
    return bounces;
}

// 主函数
int main() {
    int result = benchmark();
    int expected = 368285073;
    assert(result == expected && "bounce count not expected");
    printf("Test passed! Bounces: %d\n", result);
    return 0;
}