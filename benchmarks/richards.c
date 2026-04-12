// Richards benchmark - OS task scheduler simulation
// Classic benchmark by Martin Richards (1982)
// Stresses: function calls, pointer chasing, struct manipulation, state machines

#define IDLE 0
#define WORKER 1
#define HANDLER_A 2
#define HANDLER_B 3
#define DEVICE_A 4
#define DEVICE_B 5

#define NUM_TASKS 6
#define ITERATIONS 10

#define TASK_HOLDING 1
#define TASK_RUNNING 2
#define TASK_WAITING 4
#define TASK_HELD 8

#define PACKET_DEVICE 0
#define PACKET_WORK 1

struct Packet {
    struct Packet *link;
    int id;
    int kind;
    int a1;
    int a2[4];
};

struct Task {
    struct Task *link;
    int id;
    int pri;
    struct Packet *wkq;
    int state;
    int fn; // which function to run
    int v1, v2;
};

static struct Task task_tab[NUM_TASKS];
static struct Task *task_list;
static struct Task *cur_task;
static int hold_count, qpkt_count;

static struct Packet packets[32];
static int packet_idx;

static struct Packet *alloc_packet(int id, int kind) {
    struct Packet *p = &packets[packet_idx++];
    if (packet_idx >= 32) packet_idx = 0;
    p->link = 0;
    p->id = id;
    p->kind = kind;
    p->a1 = 0;
    int i;
    for (i = 0; i < 4; i++) p->a2[i] = 0;
    return p;
}

static struct Task *find_task(int id) {
    if (id >= 0 && id < NUM_TASKS) return &task_tab[id];
    return 0;
}

static struct Task *hold_self(void) {
    hold_count++;
    cur_task->state |= TASK_HELD;
    return cur_task->link;
}

static struct Task *release(int id) {
    struct Task *t = find_task(id);
    if (!t) return 0;
    t->state &= ~TASK_HELD;
    if (t->pri > cur_task->pri) return t;
    return cur_task;
}

static struct Task *qpkt(struct Packet *pkt) {
    struct Task *t = find_task(pkt->id);
    if (!t) return 0;
    qpkt_count++;
    pkt->link = 0;
    pkt->id = cur_task->id;
    if (t->wkq == 0) {
        t->wkq = pkt;
        t->state |= TASK_WAITING;
        if (t->pri > cur_task->pri) return t;
    } else {
        struct Packet *last = t->wkq;
        while (last->link) last = last->link;
        last->link = pkt;
    }
    return cur_task;
}

static struct Task *wait_task(void) {
    cur_task->state |= TASK_WAITING;
    return cur_task;
}

static struct Task *fn_idle(struct Packet *pkt) {
    (void)pkt;
    cur_task->v1--;
    if (cur_task->v1 == 0) return hold_self();
    if ((cur_task->v2 & 1) == 0) {
        cur_task->v2 = (cur_task->v2 >> 1);
        return release(DEVICE_A);
    } else {
        cur_task->v2 = (cur_task->v2 >> 1) ^ 0xD008;
        return release(DEVICE_B);
    }
}

static struct Task *fn_worker(struct Packet *pkt) {
    if (pkt == 0) return wait_task();
    int dest = (cur_task->v1 == HANDLER_A) ? HANDLER_B : HANDLER_A;
    cur_task->v1 = dest;
    pkt->id = dest;
    pkt->a1 = 0;
    int i;
    for (i = 0; i < 4; i++) {
        cur_task->v2++;
        if (cur_task->v2 > 26) cur_task->v2 = 1;
        pkt->a2[i] = cur_task->v2;
    }
    return qpkt(pkt);
}

static struct Task *fn_handler(struct Packet *pkt) {
    if (pkt) {
        // Just queue and process
        cur_task->v1++;
    }
    if (cur_task->v1 > 0) {
        cur_task->v1--;
        struct Packet *p = alloc_packet(cur_task->id == HANDLER_A ? DEVICE_A : DEVICE_B, PACKET_DEVICE);
        return qpkt(p);
    }
    return wait_task();
}

static struct Task *fn_device(struct Packet *pkt) {
    if (pkt == 0) return wait_task();
    pkt->id = (cur_task->id == DEVICE_A) ? HANDLER_A : HANDLER_B;
    return qpkt(pkt);
}

static struct Task *dispatch(struct Task *t, struct Packet *pkt) {
    switch (t->fn) {
        case IDLE:      return fn_idle(pkt);
        case WORKER:    return fn_worker(pkt);
        case HANDLER_A:
        case HANDLER_B: return fn_handler(pkt);
        case DEVICE_A:
        case DEVICE_B:  return fn_device(pkt);
    }
    return 0;
}

static void schedule(void) {
    struct Task *t = task_list;
    int steps = 0;
    while (t && steps < 10000) {
        steps++;
        struct Packet *pkt = 0;
        if ((t->state & (TASK_WAITING | TASK_HELD)) == TASK_WAITING) {
            pkt = t->wkq;
            if (pkt) {
                t->wkq = pkt->link;
                if (t->wkq == 0)
                    t->state &= ~TASK_WAITING;
            }
        }
        if (t->state == TASK_RUNNING || (t->state & TASK_WAITING && pkt)) {
            cur_task = t;
            struct Task *next = dispatch(t, pkt);
            t = (next == t) ? t->link : next;
        } else {
            t = t->link;
        }
    }
}

int main(void) {
    int iter;
    long total = 0;

    for (iter = 0; iter < ITERATIONS; iter++) {
        hold_count = 0;
        qpkt_count = 0;
        packet_idx = 0;

        // Initialize tasks
        int i;
        for (i = 0; i < NUM_TASKS; i++) {
            task_tab[i].link = (i + 1 < NUM_TASKS) ? &task_tab[i + 1] : 0;
            task_tab[i].id = i;
            task_tab[i].pri = NUM_TASKS - i;
            task_tab[i].wkq = 0;
            task_tab[i].state = TASK_RUNNING;
            task_tab[i].fn = i;
            task_tab[i].v1 = (i == IDLE) ? 100 : 0;
            task_tab[i].v2 = (i == IDLE) ? 0x5AFE : 0;
        }
        task_list = &task_tab[0];

        // Add initial work packets
        struct Packet *p1 = alloc_packet(WORKER, PACKET_WORK);
        struct Packet *p2 = alloc_packet(WORKER, PACKET_WORK);
        task_tab[WORKER].wkq = p1;
        p1->link = p2;
        task_tab[WORKER].state |= TASK_WAITING;

        schedule();
        total += hold_count + qpkt_count;
    }

    return (int)(total % 256);
}
