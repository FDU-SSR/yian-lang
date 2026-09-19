// bench/AWFY/c/richards.c — 任务调度器模拟 (Richards) 的 C 参考实现
//
// 语义与 bench/AWFY/an/richards.an 对齐; 只用于跨语言正确性对照, 不参与 fat/raw 计时。
// 上游: AWFY (are-we-fast-yet) benchmarks/Java/src/Richards.java + richards/*.java
//       (MIT, Copyright (c) 2001-2016 Stefan Marr; 见仓库 LICENSE.md)
//       再上游: Mario Wolczko 的 Java/Smalltalk 版 Richards (SOM 移植)。
// 期望值 (上游 Java Scheduler.start 的断言, 也是 SOM 的官方期望值):
//       queuePacketCount == 23246 && holdCount == 9297 (单趟 run)。
//
// 与上游 Java 的对应关系:
//   - Java 用 lambda (ProcessFunction) 做虚分派; C 用 TaskKind 标签 + switch,
//     与 .an 的 tagged enum + match 一一对应;
//   - TaskControlBlock 继承 TaskState 的三个布尔量, 这里平铺成字段;
//   - RBObject.append 变成自由函数 append_packet;
//   - Java 依赖 GC, 这里同样只分配不回收 (整趟 run 只有 6 个 TCB + 4 个数据记录
//     + 8 个 Packet, 分配次数与上游一致)。
#include <stdio.h>
#include <stdlib.h>

// ── RBObject 常量 ──
enum {
    IDLER = 0,
    WORKER = 1,
    HANDLER_A = 2,
    HANDLER_B = 3,
    DEVICE_A = 4,
    DEVICE_B = 5,
    NUM_TYPES = 6
};

enum { DEVICE_PACKET_KIND = 0, WORK_PACKET_KIND = 1 };

#define PACKET_DATA_SIZE 4

// ── Packet ──
typedef struct Packet {
    struct Packet *link;
    int identity;
    int kind;
    int datum;
    int data[PACKET_DATA_SIZE];
} Packet;

static Packet *create_packet(Packet *link, int identity, int kind) {
    Packet *packet = malloc(sizeof(Packet));
    packet->link = link;
    packet->identity = identity;
    packet->kind = kind;
    packet->datum = 0;
    for (int i = 0; i < PACKET_DATA_SIZE; i++) {
        packet->data[i] = 0;
    }
    return packet;
}

// 上游 RBObject.append: 把 packet 挂到 queueHead 链表尾, 返回新的队头
static Packet *append_packet(Packet *packet, Packet *queue_head) {
    packet->link = NULL;
    if (queue_head == NULL) {
        return packet;
    }
    Packet *mouse = queue_head;
    Packet *link;
    while ((link = mouse->link) != NULL) {
        mouse = link;
    }
    mouse->link = packet;
    return queue_head;
}

// ── TaskState (上游 TaskState.java 的三个布尔量) ──
typedef struct {
    int packet_pending;
    int task_waiting;
    int task_holding;
} TaskState;

static TaskState state_running(void) {
    TaskState s;
    s.packet_pending = 0;
    s.task_waiting = 0;
    s.task_holding = 0;
    return s;
}

static TaskState state_waiting(void) {
    TaskState s;
    s.packet_pending = 0;
    s.task_waiting = 1;
    s.task_holding = 0;
    return s;
}

static TaskState state_waiting_with_packet(void) {
    TaskState s;
    s.packet_pending = 1;
    s.task_waiting = 1;
    s.task_holding = 0;
    return s;
}

static void state_packet_pending(TaskState *s) {
    s->packet_pending = 1;
    s->task_waiting = 0;
    s->task_holding = 0;
}

static void state_running_set(TaskState *s) {
    s->packet_pending = 0;
    s->task_waiting = 0;
    s->task_holding = 0;
}

static int state_is_holding_or_waiting(const TaskState *s) {
    return s->task_holding || (!s->packet_pending && s->task_waiting);
}

static int state_is_waiting_with_packet(const TaskState *s) {
    return s->packet_pending && s->task_waiting && !s->task_holding;
}

// ── 四类任务的私有数据 (上游 *TaskDataRecord.java) ──
typedef struct {
    int control;
    int count;
} IdleRecord;

typedef struct {
    int destination;
    int count;
} WorkerRecord;

typedef struct {
    Packet *work_in;
    Packet *device_in;
} HandlerRecord;

typedef struct {
    Packet *pending;
} DeviceRecord;

// 上游的 Task 类层次 / ProcessFunction 虚分派 → 标签 + 数据联合
typedef enum { TASK_IDLE, TASK_WORKER, TASK_HANDLER, TASK_DEVICE } TaskKind;

typedef union {
    IdleRecord idle;
    WorkerRecord worker;
    HandlerRecord handler;
    DeviceRecord device;
} TaskData;

typedef struct TaskControlBlock {
    struct TaskControlBlock *link;
    int identity;
    int priority;
    Packet *input;
    TaskState state;
    TaskKind kind;
    TaskData data;
} TaskControlBlock;

// ── Scheduler ──
typedef struct {
    TaskControlBlock *task_list;
    TaskControlBlock *current_task;
    int current_task_identity;
    TaskControlBlock *task_table[NUM_TYPES];
    int queue_packet_count;
    int hold_count;
} Scheduler;

static Scheduler scheduler;

static TaskControlBlock *find_task(int identity) {
    TaskControlBlock *t = scheduler.task_table[identity];
    if (t == NULL) {
        fprintf(stderr, "findTask failed\n");
        exit(1);
    }
    return t;
}

static TaskControlBlock *hold_self(void) {
    scheduler.hold_count += 1;
    scheduler.current_task->state.task_holding = 1;
    return scheduler.current_task->link;
}

static TaskControlBlock *queue_packet(Packet *packet) {
    TaskControlBlock *t = find_task(packet->identity);
    if (t == NULL) {
        return NULL;
    }
    scheduler.queue_packet_count += 1;
    packet->link = NULL;
    packet->identity = scheduler.current_task_identity;
    if (t->input == NULL) {
        t->input = packet;
        t->state.packet_pending = 1;  // 上游 setPacketPending(true): 只改 pending
        if (t->priority > scheduler.current_task->priority) {
            return t;
        }
    } else {
        t->input = append_packet(packet, t->input);
    }
    return scheduler.current_task;
}

static TaskControlBlock *release(int identity) {
    TaskControlBlock *t = find_task(identity);
    if (t == NULL) {
        return NULL;
    }
    t->state.task_holding = 0;
    if (t->priority > scheduler.current_task->priority) {
        return t;
    }
    return scheduler.current_task;
}

static TaskControlBlock *mark_waiting(void) {
    scheduler.current_task->state.task_waiting = 1;
    return scheduler.current_task;
}

// 上游 TaskControlBlock.runTask 的 if 部分
static Packet *run_task_peek(void) {
    TaskControlBlock *t = scheduler.current_task;
    Packet *message;
    if (state_is_waiting_with_packet(&t->state)) {
        message = t->input;
        t->input = message->link;
        if (t->input == NULL) {
            state_running_set(&t->state);
        } else {
            state_packet_pending(&t->state);
        }
    } else {
        message = NULL;
    }
    return message;
}

// 上游四个 lambda 的函数体: 以标签 switch 分派 (对应 .an 的 tagged enum + match)
static TaskControlBlock *run_task(void) {
    TaskControlBlock *t = scheduler.current_task;
    Packet *message = run_task_peek();

    switch (t->kind) {
        case TASK_IDLE: {
            IdleRecord *data = &t->data.idle;
            data->count -= 1;
            if (data->count == 0) {
                return hold_self();
            }
            if ((data->control & 1) == 0) {
                data->control = data->control / 2;
                return release(DEVICE_A);
            }
            data->control = (data->control / 2) ^ 53256;
            return release(DEVICE_B);
        }
        case TASK_WORKER: {
            WorkerRecord *data = &t->data.worker;
            if (message == NULL) {
                return mark_waiting();
            }
            data->destination = (data->destination == HANDLER_A) ? HANDLER_B : HANDLER_A;
            message->identity = data->destination;
            message->datum = 0;
            for (int i = 0; i < PACKET_DATA_SIZE; i++) {
                data->count += 1;
                if (data->count > 26) {
                    data->count = 1;
                }
                message->data[i] = 65 + data->count - 1;
            }
            return queue_packet(message);
        }
        case TASK_HANDLER: {
            HandlerRecord *data = &t->data.handler;
            if (message != NULL) {
                if (message->kind == WORK_PACKET_KIND) {
                    data->work_in = append_packet(message, data->work_in);
                } else {
                    data->device_in = append_packet(message, data->device_in);
                }
            }
            Packet *work_packet = data->work_in;
            if (work_packet == NULL) {
                return mark_waiting();
            }
            int count = work_packet->datum;
            if (count >= PACKET_DATA_SIZE) {
                data->work_in = work_packet->link;
                return queue_packet(work_packet);
            }
            Packet *device_packet = data->device_in;
            if (device_packet == NULL) {
                return mark_waiting();
            }
            data->device_in = device_packet->link;
            device_packet->datum = work_packet->data[count];
            work_packet->datum = count + 1;
            return queue_packet(device_packet);
        }
        case TASK_DEVICE: {
            DeviceRecord *data = &t->data.device;
            if (message == NULL) {
                Packet *pending = data->pending;
                if (pending == NULL) {
                    return mark_waiting();
                }
                data->pending = NULL;
                return queue_packet(pending);
            }
            data->pending = message;
            return hold_self();
        }
    }
    return NULL;
}

static void schedule(void) {
    scheduler.current_task = scheduler.task_list;
    while (scheduler.current_task != NULL) {
        if (state_is_holding_or_waiting(&scheduler.current_task->state)) {
            scheduler.current_task = scheduler.current_task->link;
        } else {
            scheduler.current_task_identity = scheduler.current_task->identity;
            scheduler.current_task = run_task();
        }
    }
}

static void create_task(int identity, int priority, Packet *input, TaskState state,
                        TaskKind kind, TaskData data) {
    TaskControlBlock *t = malloc(sizeof(TaskControlBlock));
    t->link = scheduler.task_list;
    t->identity = identity;
    t->priority = priority;
    t->input = input;
    t->state = state;
    t->kind = kind;
    t->data = data;  // 记录体按种类拷贝 (对应上游 new *TaskDataRecord())
    scheduler.task_list = t;
    scheduler.task_table[identity] = t;
}

static void create_idler(int identity, int priority, Packet *work, TaskState state) {
    TaskData data;
    data.idle.control = 1;
    data.idle.count = 10000;
    create_task(identity, priority, work, state, TASK_IDLE, data);
}

static void create_worker(int identity, int priority, Packet *work, TaskState state) {
    TaskData data;
    data.worker.destination = HANDLER_A;
    data.worker.count = 0;
    create_task(identity, priority, work, state, TASK_WORKER, data);
}

static void create_handler(int identity, int priority, Packet *work, TaskState state) {
    TaskData data;
    data.handler.work_in = NULL;
    data.handler.device_in = NULL;
    create_task(identity, priority, work, state, TASK_HANDLER, data);
}

static void create_device(int identity, int priority, Packet *work, TaskState state) {
    TaskData data;
    data.device.pending = NULL;
    create_task(identity, priority, work, state, TASK_DEVICE, data);
}

static void scheduler_init(void) {
    scheduler.task_list = NULL;
    scheduler.current_task = NULL;
    scheduler.current_task_identity = 0;
    scheduler.queue_packet_count = 0;
    scheduler.hold_count = 0;
    for (int i = 0; i < NUM_TYPES; i++) {
        scheduler.task_table[i] = NULL;
    }
}

// 上游 Scheduler.start(): 返回 queuePacketCount==23246 && holdCount==9297
static int start(void) {
    Packet *work_q;

    create_idler(IDLER, 0, NULL, state_running());
    work_q = create_packet(NULL, WORKER, WORK_PACKET_KIND);
    work_q = create_packet(work_q, WORKER, WORK_PACKET_KIND);
    create_worker(WORKER, 1000, work_q, state_waiting_with_packet());

    work_q = create_packet(NULL, DEVICE_A, DEVICE_PACKET_KIND);
    work_q = create_packet(work_q, DEVICE_A, DEVICE_PACKET_KIND);
    work_q = create_packet(work_q, DEVICE_A, DEVICE_PACKET_KIND);
    create_handler(HANDLER_A, 2000, work_q, state_waiting_with_packet());

    work_q = create_packet(NULL, DEVICE_B, DEVICE_PACKET_KIND);
    work_q = create_packet(work_q, DEVICE_B, DEVICE_PACKET_KIND);
    work_q = create_packet(work_q, DEVICE_B, DEVICE_PACKET_KIND);
    create_handler(HANDLER_B, 3000, work_q, state_waiting_with_packet());

    create_device(DEVICE_A, 4000, NULL, state_waiting());
    create_device(DEVICE_B, 5000, NULL, state_waiting());

    schedule();

    return scheduler.queue_packet_count == 23246 && scheduler.hold_count == 9297;
}

int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 1;
    long total_queue = 0;
    long total_hold = 0;
    int ok = 1;
    for (int i = 0; i < iterations; i++) {
        scheduler_init();
        ok = start() && ok;
        total_queue += scheduler.queue_packet_count;
        total_hold += scheduler.hold_count;
    }
    printf("%ld %ld\n", total_queue, total_hold);
    return ok ? 0 : 1;
}
