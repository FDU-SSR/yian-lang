// bench/AWFY/c/richards.c — 任务调度器模拟 (Richards) 的 C 参考实现
//
// 语义与 bench/AWFY/an/richards.an 对齐; C 与 YIAN 都执行一个独立工作单元,
// 并在验证后释放该工作单元创建的对象。上游 Java 依赖 GC, 这里用登记表代替
// 沿任务图递归释放, 因为任务和 packet 的 link/input 会形成环状引用。
// 上游: AWFY (are-we-fast-yet) benchmarks/Java/src/Richards.java + richards/*.java
//       (MIT, Copyright (c) 2001-2016 Stefan Marr; 见仓库 LICENSE.md)
// 期望值 (上游 Java Scheduler.start 的断言):
//       queuePacketCount == 23246 && holdCount == 9297 (单趟 run)。

#include <stdio.h>
#include <stdlib.h>

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
#define PACKET_CAPACITY 8
#define TASK_CAPACITY 6
#define IDLE_RECORD_CAPACITY 1
#define WORKER_RECORD_CAPACITY 1
#define HANDLER_RECORD_CAPACITY 2
#define DEVICE_RECORD_CAPACITY 2

typedef struct Packet Packet;
typedef struct TaskControlBlock TaskControlBlock;
typedef struct Scheduler Scheduler;

struct Packet {
    Packet *link;
    int identity;
    int kind;
    int datum;
    int data[PACKET_DATA_SIZE];
};

typedef struct {
    int packet_pending;
    int task_waiting;
    int task_holding;
} TaskState;

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

typedef enum { TASK_IDLE, TASK_WORKER, TASK_HANDLER, TASK_DEVICE } TaskKind;

typedef union {
    IdleRecord *idle;
    WorkerRecord *worker;
    HandlerRecord *handler;
    DeviceRecord *device;
} TaskData;

struct TaskControlBlock {
    TaskControlBlock *link;
    int identity;
    int priority;
    Packet *input;
    TaskState state;
    TaskKind kind;
    TaskData data;
};

typedef struct {
    Packet *packets[PACKET_CAPACITY];
    size_t packet_count;
    TaskControlBlock *tasks[TASK_CAPACITY];
    size_t task_count;
    IdleRecord *idle_records[IDLE_RECORD_CAPACITY];
    size_t idle_record_count;
    WorkerRecord *worker_records[WORKER_RECORD_CAPACITY];
    size_t worker_record_count;
    HandlerRecord *handler_records[HANDLER_RECORD_CAPACITY];
    size_t handler_record_count;
    DeviceRecord *device_records[DEVICE_RECORD_CAPACITY];
    size_t device_record_count;
} AllocationRegistry;

struct Scheduler {
    TaskControlBlock *task_list;
    TaskControlBlock *current_task;
    int current_task_identity;
    TaskControlBlock *task_table[NUM_TYPES];
    int queue_packet_count;
    int hold_count;
    AllocationRegistry allocations;
};

static void register_packet(Scheduler *scheduler, Packet *packet) {
    if (scheduler->allocations.packet_count >= PACKET_CAPACITY) {
        fprintf(stderr, "richards: packet registry overflow\n");
        exit(1);
    }
    scheduler->allocations.packets[scheduler->allocations.packet_count++] = packet;
}

static void register_task(Scheduler *scheduler, TaskControlBlock *task) {
    if (scheduler->allocations.task_count >= TASK_CAPACITY) {
        fprintf(stderr, "richards: task registry overflow\n");
        exit(1);
    }
    scheduler->allocations.tasks[scheduler->allocations.task_count++] = task;
}

static void register_idle_record(Scheduler *scheduler, IdleRecord *record) {
    if (scheduler->allocations.idle_record_count >= IDLE_RECORD_CAPACITY) {
        fprintf(stderr, "richards: idle record registry overflow\n");
        exit(1);
    }
    scheduler->allocations.idle_records[scheduler->allocations.idle_record_count++] = record;
}

static void register_worker_record(Scheduler *scheduler, WorkerRecord *record) {
    if (scheduler->allocations.worker_record_count >= WORKER_RECORD_CAPACITY) {
        fprintf(stderr, "richards: worker record registry overflow\n");
        exit(1);
    }
    scheduler->allocations.worker_records[scheduler->allocations.worker_record_count++] = record;
}

static void register_handler_record(Scheduler *scheduler, HandlerRecord *record) {
    if (scheduler->allocations.handler_record_count >= HANDLER_RECORD_CAPACITY) {
        fprintf(stderr, "richards: handler record registry overflow\n");
        exit(1);
    }
    scheduler->allocations.handler_records[scheduler->allocations.handler_record_count++] = record;
}

static void register_device_record(Scheduler *scheduler, DeviceRecord *record) {
    if (scheduler->allocations.device_record_count >= DEVICE_RECORD_CAPACITY) {
        fprintf(stderr, "richards: device record registry overflow\n");
        exit(1);
    }
    scheduler->allocations.device_records[scheduler->allocations.device_record_count++] = record;
}

static Packet *create_packet(Scheduler *scheduler, Packet *link, int identity, int kind) {
    Packet *packet = malloc(sizeof(Packet));
    packet->link = link;
    packet->identity = identity;
    packet->kind = kind;
    packet->datum = 0;
    for (int i = 0; i < PACKET_DATA_SIZE; i++) {
        packet->data[i] = 0;
    }
    register_packet(scheduler, packet);
    return packet;
}

static Packet *append_packet(Packet *packet, Packet *queue_head) {
    packet->link = NULL;
    if (queue_head == NULL) {
        return packet;
    }
    Packet *mouse = queue_head;
    while (mouse->link != NULL) {
        mouse = mouse->link;
    }
    mouse->link = packet;
    return queue_head;
}

static TaskState state_running(void) {
    return (TaskState){.packet_pending = 0, .task_waiting = 0, .task_holding = 0};
}

static TaskState state_waiting(void) {
    return (TaskState){.packet_pending = 0, .task_waiting = 1, .task_holding = 0};
}

static TaskState state_waiting_with_packet(void) {
    return (TaskState){.packet_pending = 1, .task_waiting = 1, .task_holding = 0};
}

static void state_packet_pending(TaskState *state) {
    state->packet_pending = 1;
    state->task_waiting = 0;
    state->task_holding = 0;
}

static void state_running_set(TaskState *state) {
    state->packet_pending = 0;
    state->task_waiting = 0;
    state->task_holding = 0;
}

static int state_is_holding_or_waiting(const TaskState *state) {
    return state->task_holding || (!state->packet_pending && state->task_waiting);
}

static int state_is_waiting_with_packet(const TaskState *state) {
    return state->packet_pending && state->task_waiting && !state->task_holding;
}

static TaskControlBlock *find_task(Scheduler *scheduler, int identity) {
    TaskControlBlock *task = scheduler->task_table[identity];
    if (task == NULL) {
        fprintf(stderr, "richards: findTask failed\n");
        exit(1);
    }
    return task;
}

static TaskControlBlock *hold_self(Scheduler *scheduler) {
    scheduler->hold_count += 1;
    scheduler->current_task->state.task_holding = 1;
    return scheduler->current_task->link;
}

static TaskControlBlock *queue_packet(Scheduler *scheduler, Packet *packet) {
    TaskControlBlock *task = find_task(scheduler, packet->identity);
    scheduler->queue_packet_count += 1;
    packet->link = NULL;
    packet->identity = scheduler->current_task_identity;
    if (task->input == NULL) {
        task->input = packet;
        task->state.packet_pending = 1;
        if (task->priority > scheduler->current_task->priority) {
            return task;
        }
    } else {
        task->input = append_packet(packet, task->input);
    }
    return scheduler->current_task;
}

static TaskControlBlock *release_task(Scheduler *scheduler, int identity) {
    TaskControlBlock *task = find_task(scheduler, identity);
    task->state.task_holding = 0;
    if (task->priority > scheduler->current_task->priority) {
        return task;
    }
    return scheduler->current_task;
}

static TaskControlBlock *mark_waiting(Scheduler *scheduler) {
    scheduler->current_task->state.task_waiting = 1;
    return scheduler->current_task;
}

static Packet *run_task_peek(Scheduler *scheduler) {
    TaskControlBlock *task = scheduler->current_task;
    Packet *message = NULL;
    if (state_is_waiting_with_packet(&task->state)) {
        message = task->input;
        task->input = message->link;
        if (task->input == NULL) {
            state_running_set(&task->state);
        } else {
            state_packet_pending(&task->state);
        }
    }
    return message;
}

static TaskControlBlock *run_task(Scheduler *scheduler) {
    TaskControlBlock *task = scheduler->current_task;
    Packet *message = run_task_peek(scheduler);

    switch (task->kind) {
        case TASK_IDLE: {
            IdleRecord *data = task->data.idle;
            data->count -= 1;
            if (data->count == 0) {
                return hold_self(scheduler);
            }
            if ((data->control & 1) == 0) {
                data->control = data->control / 2;
                return release_task(scheduler, DEVICE_A);
            }
            data->control = (data->control / 2) ^ 53256;
            return release_task(scheduler, DEVICE_B);
        }
        case TASK_WORKER: {
            WorkerRecord *data = task->data.worker;
            if (message == NULL) {
                return mark_waiting(scheduler);
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
            return queue_packet(scheduler, message);
        }
        case TASK_HANDLER: {
            HandlerRecord *data = task->data.handler;
            if (message != NULL) {
                if (message->kind == WORK_PACKET_KIND) {
                    data->work_in = append_packet(message, data->work_in);
                } else {
                    data->device_in = append_packet(message, data->device_in);
                }
            }
            Packet *work_packet = data->work_in;
            if (work_packet == NULL) {
                return mark_waiting(scheduler);
            }
            int count = work_packet->datum;
            if (count >= PACKET_DATA_SIZE) {
                data->work_in = work_packet->link;
                return queue_packet(scheduler, work_packet);
            }
            Packet *device_packet = data->device_in;
            if (device_packet == NULL) {
                return mark_waiting(scheduler);
            }
            data->device_in = device_packet->link;
            device_packet->datum = work_packet->data[count];
            work_packet->datum = count + 1;
            return queue_packet(scheduler, device_packet);
        }
        case TASK_DEVICE: {
            DeviceRecord *data = task->data.device;
            if (message == NULL) {
                Packet *pending = data->pending;
                if (pending == NULL) {
                    return mark_waiting(scheduler);
                }
                data->pending = NULL;
                return queue_packet(scheduler, pending);
            }
            data->pending = message;
            return hold_self(scheduler);
        }
    }
    return NULL;
}

static void schedule(Scheduler *scheduler) {
    scheduler->current_task = scheduler->task_list;
    while (scheduler->current_task != NULL) {
        if (state_is_holding_or_waiting(&scheduler->current_task->state)) {
            scheduler->current_task = scheduler->current_task->link;
        } else {
            scheduler->current_task_identity = scheduler->current_task->identity;
            scheduler->current_task = run_task(scheduler);
        }
    }
}

static void create_task(Scheduler *scheduler, int identity, int priority, Packet *input,
                        TaskState state, TaskKind kind, TaskData data) {
    TaskControlBlock *task = malloc(sizeof(TaskControlBlock));
    task->link = scheduler->task_list;
    task->identity = identity;
    task->priority = priority;
    task->input = input;
    task->state = state;
    task->kind = kind;
    task->data = data;
    register_task(scheduler, task);
    scheduler->task_list = task;
    scheduler->task_table[identity] = task;
}

static void create_idler(Scheduler *scheduler, int identity, int priority, Packet *work,
                         TaskState state) {
    IdleRecord *record = malloc(sizeof(IdleRecord));
    record->control = 1;
    record->count = 10000;
    register_idle_record(scheduler, record);
    TaskData data = {.idle = record};
    create_task(scheduler, identity, priority, work, state, TASK_IDLE, data);
}

static void create_worker(Scheduler *scheduler, int identity, int priority, Packet *work,
                          TaskState state) {
    WorkerRecord *record = malloc(sizeof(WorkerRecord));
    record->destination = HANDLER_A;
    record->count = 0;
    register_worker_record(scheduler, record);
    TaskData data = {.worker = record};
    create_task(scheduler, identity, priority, work, state, TASK_WORKER, data);
}

static void create_handler(Scheduler *scheduler, int identity, int priority, Packet *work,
                           TaskState state) {
    HandlerRecord *record = malloc(sizeof(HandlerRecord));
    record->work_in = NULL;
    record->device_in = NULL;
    register_handler_record(scheduler, record);
    TaskData data = {.handler = record};
    create_task(scheduler, identity, priority, work, state, TASK_HANDLER, data);
}

static void create_device(Scheduler *scheduler, int identity, int priority, Packet *work,
                          TaskState state) {
    DeviceRecord *record = malloc(sizeof(DeviceRecord));
    record->pending = NULL;
    register_device_record(scheduler, record);
    TaskData data = {.device = record};
    create_task(scheduler, identity, priority, work, state, TASK_DEVICE, data);
}

static void scheduler_init(Scheduler *scheduler) {
    *scheduler = (Scheduler){0};
}

static int start(Scheduler *scheduler) {
    Packet *work_q;

    create_idler(scheduler, IDLER, 0, NULL, state_running());
    work_q = create_packet(scheduler, NULL, WORKER, WORK_PACKET_KIND);
    work_q = create_packet(scheduler, work_q, WORKER, WORK_PACKET_KIND);
    create_worker(scheduler, WORKER, 1000, work_q, state_waiting_with_packet());

    work_q = create_packet(scheduler, NULL, DEVICE_A, DEVICE_PACKET_KIND);
    work_q = create_packet(scheduler, work_q, DEVICE_A, DEVICE_PACKET_KIND);
    work_q = create_packet(scheduler, work_q, DEVICE_A, DEVICE_PACKET_KIND);
    create_handler(scheduler, HANDLER_A, 2000, work_q, state_waiting_with_packet());

    work_q = create_packet(scheduler, NULL, DEVICE_B, DEVICE_PACKET_KIND);
    work_q = create_packet(scheduler, work_q, DEVICE_B, DEVICE_PACKET_KIND);
    work_q = create_packet(scheduler, work_q, DEVICE_B, DEVICE_PACKET_KIND);
    create_handler(scheduler, HANDLER_B, 3000, work_q, state_waiting_with_packet());

    create_device(scheduler, DEVICE_A, 4000, NULL, state_waiting());
    create_device(scheduler, DEVICE_B, 5000, NULL, state_waiting());

    schedule(scheduler);
    return scheduler->queue_packet_count == 23246 && scheduler->hold_count == 9297;
}

static void free_allocations(Scheduler *scheduler) {
    for (size_t i = 0; i < scheduler->allocations.idle_record_count; i++) {
        free(scheduler->allocations.idle_records[i]);
    }
    for (size_t i = 0; i < scheduler->allocations.worker_record_count; i++) {
        free(scheduler->allocations.worker_records[i]);
    }
    for (size_t i = 0; i < scheduler->allocations.handler_record_count; i++) {
        free(scheduler->allocations.handler_records[i]);
    }
    for (size_t i = 0; i < scheduler->allocations.device_record_count; i++) {
        free(scheduler->allocations.device_records[i]);
    }
    for (size_t i = 0; i < scheduler->allocations.packet_count; i++) {
        free(scheduler->allocations.packets[i]);
    }
    for (size_t i = 0; i < scheduler->allocations.task_count; i++) {
        free(scheduler->allocations.tasks[i]);
    }
}

int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 3303;
    for (int i = 0; i < iterations; i++) {
        Scheduler scheduler;
        scheduler_init(&scheduler);
        int valid = start(&scheduler);
        if (!valid) {
            free_allocations(&scheduler);
            return 1;
        }
        free_allocations(&scheduler);
    }
    return 0;
}
