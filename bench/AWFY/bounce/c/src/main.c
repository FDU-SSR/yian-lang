#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <stdbool.h>

// Absolute-value function.
int abs_int(int x) {
    if (x < 0) {
        return -x;
    }
    return x;
}

// Random-number generator struct.
typedef struct {
    int seed;
} Random;

// Ball struct.
typedef struct {
    int x;
    int y;
    int x_vel;
    int y_vel;
} Ball;

// Random-number result struct.
typedef struct {
    Random random_state;
    int value;
} RandomResult;

// Ball-initialization result struct.
typedef struct {
    Ball ball;
    Random random_state;
} BallInitResult;

// Bounce-result struct.
typedef struct {
    Ball ball;
    bool bounced;
} BounceResult;

// Initialize the random-number generator.
Random Random_new() {
    Random r = {74755};
    return r;
}

// Generate the next random value.
RandomResult Random_next(Random* self) {
    int new_seed = ((self->seed * 1309) + 13849) & 65535;
    RandomResult result;
    result.random_state.seed = new_seed;
    result.value = new_seed;
    return result;
}

// Initialize a ball.
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

// Ball-bounce logic.
BounceResult Ball_bounce(Ball* self) {
    int x_limit = 500;
    int y_limit = 500;
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

// Benchmark function.
int benchmark() {
    Random random = Random_new();

    int ball_count = 100;
    int bounces = 0;
    Ball* balls = (Ball*)malloc(sizeof(Ball) * ball_count);

    for (int i = 0; i < ball_count; i++) {
        BallInitResult init_res = BallInitResult_new(&random);
        balls[i] = init_res.ball;
        random = init_res.random_state;
    }

    for (int j = 0; j < 50; j++) {
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

// main: argv[1] controls only the outer repetition count of complete work units.
int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 300000;
    for (int i = 0; i < iterations; i++) {
        int result = benchmark();
        assert(result == 1331 && "bounce count not expected");
    }
    return 0;
}
