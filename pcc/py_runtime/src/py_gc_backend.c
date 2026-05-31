/* Pluggable-GC backend selector and shared telemetry.
 *
 * The non-refcount backends start as selectable skeletons: they reuse
 * the refcount semantics while exposing the barrier/safepoint counters
 * that the real Lua/Go/OCaml/ZGC implementations will drive. Keeping
 * this separate from py_obj.c lets each backend grow without changing
 * the public pcc_gc_* ABI again.
 */

#include "py_internal.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

void py_weakref_invalidate(PyObject *target);
int64_t py_weakref_retarget(PyObject *from, PyObject *to);
void py_gc_untrack(PyObject *o);

static int64_t pcc_gc_selected_backend = PCC_GC_KIND_REFCOUNT_CYCLE;
static int64_t pcc_gc_metrics[6] = {0, 0, 0, 0, 0, 0};
static int32_t pcc_gc_mark_active = 0;
static int32_t pcc_gc_cycle_requested = 0;
static int32_t pcc_gc_config_initialized = 0;
static int32_t pcc_gc_backend0_frame_roots_enabled = 0;
static _Thread_local int32_t pcc_gc_in_auto_step = 0;
static _Thread_local int32_t pcc_gc_explicit_collect_active = 0;
static int64_t pcc_gc_debt_bytes = 0;
static int64_t pcc_gc_live_bytes = 0;
static int64_t pcc_gc_gcpause = 200;
static int64_t pcc_gc_gcstepmul = 200;
static int64_t pcc_gc_debt_threshold_override = 0;
static int64_t pcc_gc_max_pause_us = 0;
static int64_t pcc_gc_minor_heap_size = 1048576;
static int64_t pcc_gc_minor_alloc_max = 256;
static int64_t pcc_gc_minor_allocations = 0;
static int64_t pcc_gc_minor_collections = 0;
static int64_t pcc_gc_minor_bytes = 0;

typedef struct PccGcMinorBlock {
    uint8_t *base;
    uint8_t *ptr;
    uint8_t *end;
    int64_t live_objects;
    int64_t owner_thread_id;
    struct PccGcMinorBlock *next;
} PccGcMinorBlock;

typedef struct PccGcObjectNode {
    PyObject *obj;
    int64_t size;
    int32_t freeing;
    PccGcMinorBlock *minor_block;
    struct PccGcObjectNode *next;
} PccGcObjectNode;

typedef struct PccGcFrameNode {
    const int32_t *frame_map;
    PyObject **slots;
    struct PccGcFrameNode *next;
} PccGcFrameNode;

typedef struct PccGcContinuationRootNode {
    const int32_t *frame_map;
    PyObject **slots;
    struct PccGcContinuationRootNode *next;
} PccGcContinuationRootNode;

typedef struct PccGcSchedulerRootNode {
    PyObject **slot;
    struct PccGcSchedulerRootNode *next;
} PccGcSchedulerRootNode;

typedef struct PccGcSchedulerQueueEntry {
    PyObject *value;
    struct PccGcSchedulerQueueEntry *next;
} PccGcSchedulerQueueEntry;

struct PccGcSchedulerQueue {
    PccMutex *mutex;
    PccGcSchedulerQueueEntry *head;
    PccGcSchedulerQueueEntry *tail;
    int64_t length;
};

typedef struct PccGcForwardNode {
    PyObject *from;
    PyObject *to;
    struct PccGcForwardNode *next;
} PccGcForwardNode;

typedef struct PccGcIdentityNode {
    PyObject *obj;
    int64_t id;
    struct PccGcIdentityNode *next;
} PccGcIdentityNode;

typedef struct PccGcRelocationNode {
    PyObject *obj;
    struct PccGcRelocationNode *next;
} PccGcRelocationNode;

typedef struct PccGcStoreBufferNode {
    PyObject *owner;
    PyObject **slot;
    PyObject *value;
    struct PccGcStoreBufferNode *next;
} PccGcStoreBufferNode;

typedef struct PccGcRememberedSlotNode {
    PyObject *owner;
    PyObject **slot;
    struct PccGcRememberedSlotNode *next;
} PccGcRememberedSlotNode;

typedef struct {
    PyObject *owner;
    PyObject **slot;
    PyObject *value;
} PccGcStoreBufferEntry;

typedef struct {
    PyObjectHeader h;
    const char *name;
    PyNativeFuncEntry entry;
    PyObject *captures;
    PyObject *args;
    PyObject *result;
    int32_t closed;
    int32_t done;
} PccGcCoroutineObject;

/* py_threading.c keeps its concrete thread object private.  The tracing
 * backends still need to walk and clear the PyObject* children so Thread
 * wrappers do not pin their target/captures forever.  Keep this view in sync
 * with PyThreadObject in py_threading.c; it only exposes pointer slots. */
typedef struct {
    PyObjectHeader h;
    PccThreadHandle *handle;
    PyObject *callable;
    PyObject *args;
    PyObject *result;
    int64_t started;
    int64_t joined;
    int64_t finished;
} PccGcThreadObject;

static PccGcObjectNode *pcc_gc_objects = NULL;
static PccGcFrameNode *pcc_gc_frames = NULL;
static PccGcContinuationRootNode *pcc_gc_continuation_roots = NULL;
static PccGcSchedulerRootNode *pcc_gc_scheduler_roots = NULL;
static PccGcForwardNode *pcc_gc_forwardings = NULL;
static PccGcIdentityNode *pcc_gc_identities = NULL;
static PccGcRelocationNode *pcc_gc_relocation_set = NULL;
static PccGcStoreBufferNode *pcc_gc_backend4_store_buffer = NULL;
static PccGcRememberedSlotNode *pcc_gc_backend4_remembered_slots = NULL;
#define PCC_GC_BACKEND4_REMEMBERED_PAGE_SLOT_BITS 512
#define PCC_GC_BACKEND4_REMEMBERED_PAGE_WORDS \
    (PCC_GC_BACKEND4_REMEMBERED_PAGE_SLOT_BITS / 64)
#define PCC_GC_BACKEND4_ZPAGE_CARD_SLOT_BITS 8
#define PCC_GC_BACKEND4_ZPAGE_CARD_COUNT \
    (PCC_GC_BACKEND4_REMEMBERED_PAGE_SLOT_BITS / \
     PCC_GC_BACKEND4_ZPAGE_CARD_SLOT_BITS)
#ifndef PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES
#define PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES 512
#endif

typedef struct PccGcRememberedPageNode {
    uintptr_t page_key;
    uint64_t slot_bitmap[PCC_GC_BACKEND4_REMEMBERED_PAGE_WORDS];
    int64_t slots;
    struct PccGcRememberedPageNode *next;
} PccGcRememberedPageNode;
static PccGcRememberedPageNode *pcc_gc_backend4_remembered_pages = NULL;
typedef struct PccGcZPage {
    PyObject *primary_owner;
    int64_t used_bytes;
    int64_t capacity_bytes;
    int64_t allocated_bytes;
    int32_t page_class;
    int32_t generation;
    int64_t object_count;
    int64_t remembered_slots;
    int64_t remembered_cards;
    uint64_t remembered_card_bitmap;
    uint16_t remembered_card_refcounts[PCC_GC_BACKEND4_ZPAGE_CARD_COUNT];
    uint8_t *span_base;
    int64_t span_capacity_bytes;
    int64_t pending_alloc_count;
    struct PccGcZPage *next;
} PccGcZPage;

typedef struct PccGcZPageNode {
    PyObject *owner;
    PccGcZPage *page;
    int64_t offset_bytes;
    int64_t size_bytes;
    struct PccGcZPageNode *next;
} PccGcZPageNode;

typedef struct PccGcZPagePayloadSpanNode {
    PyObject *owner;
    uint8_t *base;
    int64_t size_bytes;
    int64_t offset_bytes;
    PccGcZPage *page;
    struct PccGcZPagePayloadSpanNode *next;
} PccGcZPagePayloadSpanNode;

typedef struct PccGcZPageEvacuationCandidate {
    PccGcZPageNode *mapping;
    PccGcZPage *page;
    PyObject *owner;
    int64_t used_bytes;
    int64_t capacity_bytes;
    int64_t fragmentation_bytes;
    int64_t remembered_slots;
    int64_t remembered_cards;
    int64_t score;
    int32_t owner_flags;
} PccGcZPageEvacuationCandidate;

typedef struct PccGcZPageEvacuationNode {
    PccGcZPage *page;
    struct PccGcZPageEvacuationNode *next;
} PccGcZPageEvacuationNode;

static PccGcZPageNode *pcc_gc_backend4_zpages = NULL;
static PccGcZPagePayloadSpanNode *pcc_gc_backend4_zpage_payload_spans = NULL;
static PccGcZPage *pcc_gc_backend4_pages = NULL;
static PccGcZPage *pcc_gc_backend4_free_pages = NULL;
static PccGcZPageEvacuationNode *pcc_gc_backend4_evacuation_pages = NULL;
static void pcc_gc_backend4_zpage_note_remembered_slot_unlocked(
    PyObject *owner,
    int64_t delta
);
static void pcc_gc_backend4_zpage_note_remembered_card_unlocked(
    PyObject *owner,
    PyObject **slot,
    int64_t delta
);
typedef struct PccGcStoreBufferMediumState {
    PccGcStoreBufferEntry *entries;
    int32_t *count;
    struct PccGcStoreBufferMediumState *next;
} PccGcStoreBufferMediumState;
static PccGcStoreBufferMediumState *pcc_gc_backend4_store_buffer_medium_states = NULL;
static PccGcMinorBlock *pcc_gc_minor_blocks = NULL;
static _Thread_local PccGcMinorBlock *pcc_gc_minor_current = NULL;
static _Thread_local PccGcMinorBlock *pcc_gc_pending_minor_block = NULL;
static int64_t pcc_gc_next_object_id = 1;
static int pcc_gc_tracks_objects(void);
static int pcc_gc_is_known_object(PyObject *o);
static int pcc_gc_has_sweep_candidate(void);
static void pcc_gc_backend4_evacuation_page_clear_unlocked(void);
static PccGcZPageEvacuationNode *
pcc_gc_backend4_evacuation_page_find_unlocked(PccGcZPage *page);
static int64_t pcc_gc_known_object_size_unlocked(PyObject *obj);
static int64_t pcc_gc_cms_trace_gray_object_unlocked(PyObject *o);
static int64_t pcc_gc_cms_worker_trace_cycle_unlocked(int64_t budget);
static int64_t pcc_gc_step_trace_cycle(int64_t budget);

#define PCC_GC_SAFEPOINT_BATCH 16
#define PCC_GC_DEFAULT_DEBT_THRESHOLD 65536LL
#define PCC_GC_WORK_BYTES 64LL
#define PCC_GC_MAX_AUTO_STEP_BUDGET 128LL
#define PCC_GC_CMS_QUEUE_CAPACITY 256
#define PCC_GC_CMS_WB_BUFFER_CAPACITY 32
#define PCC_GC_BACKEND4_STORE_BUFFER_BATCH_CAPACITY 8
#define PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY 32
#define PCC_GC_BACKEND4_SMALL_PAGE_LIMIT 4096
#define PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT 65536
#define PCC_GC_BACKEND4_FREE_SMALL_PAGE_LIMIT 8
#define PCC_GC_BACKEND4_FREE_MEDIUM_PAGE_LIMIT 4

static int64_t pcc_gc_cms_queue[PCC_GC_CMS_QUEUE_CAPACITY];
static int32_t pcc_gc_cms_queue_head = 0;
static int32_t pcc_gc_cms_queue_tail = 0;
static unsigned char pcc_gc_cms_queue_lock_word = 0;
static _Thread_local PyObject *pcc_gc_cms_wb_buffer[
    PCC_GC_CMS_WB_BUFFER_CAPACITY
];
static _Thread_local int32_t pcc_gc_cms_wb_buffer_count = 0;
static _Thread_local PccGcStoreBufferEntry pcc_gc_backend4_store_buffer_medium[
    PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY
];
static _Thread_local int32_t pcc_gc_backend4_store_buffer_medium_count = 0;
static _Thread_local PccGcStoreBufferMediumState *pcc_gc_backend4_store_buffer_medium_state = NULL;
static int32_t pcc_gc_cms_worker_started = 0;
static int32_t pcc_gc_cms_worker_stop_requested = 0;
static PccThreadHandle *pcc_gc_cms_worker_handle = NULL;
static int64_t pcc_gc_cms_worker_starts = 0;
static int64_t pcc_gc_cms_worker_stops = 0;
static int64_t pcc_gc_cms_queue_pushes = 0;
static int64_t pcc_gc_cms_worker_drains = 0;
static int64_t pcc_gc_cms_mutator_assists = 0;
static int64_t pcc_gc_cms_worker_traces = 0;
static int64_t pcc_gc_cms_wb_flushes = 0;
static int32_t pcc_gc_graph_lock_state = 0;
static _Thread_local int32_t pcc_gc_graph_lock_depth = 0;
static int64_t pcc_gc_minor_arena_refills = 0;
static int64_t pcc_gc_minor_arena_bumps = 0;
static int64_t pcc_gc_minor_arena_fallbacks = 0;
static int64_t pcc_gc_relocation_forwards = 0;
static int64_t pcc_gc_relocation_barrier_forwards = 0;
static int64_t pcc_gc_relocation_pin_rejects = 0;
static int64_t pcc_gc_backend4_genzgc_store_barriers = 0;
static int64_t pcc_gc_backend4_store_buffer_entries_count = 0;
static int64_t pcc_gc_backend4_young_promotions = 0;
static int64_t pcc_gc_backend4_evacuation_candidates = 0;
static int64_t pcc_gc_backend4_evacuated_bytes_count = 0;
static int64_t pcc_gc_backend4_large_object_defers = 0;
static int64_t pcc_gc_backend4_large_object_deferred_bytes_count = 0;
static int64_t pcc_gc_backend4_large_object_reconsiderations_count = 0;
static int64_t pcc_gc_backend4_small_page_candidates = 0;
static int64_t pcc_gc_backend4_medium_page_candidates = 0;
static int64_t pcc_gc_backend4_evacuation_candidate_bytes_count = 0;
static int64_t pcc_gc_backend4_small_page_candidate_bytes_count = 0;
static int64_t pcc_gc_backend4_medium_page_candidate_bytes_count = 0;
static int64_t pcc_gc_backend4_evacuation_candidate_zpage_bytes_count = 0;
static int64_t pcc_gc_backend4_small_page_candidate_zpage_bytes_count = 0;
static int64_t pcc_gc_backend4_medium_page_candidate_zpage_bytes_count = 0;
static int64_t pcc_gc_backend4_store_buffer_drain_batches_count = 0;
static int64_t pcc_gc_backend4_store_buffer_drained_entries_count = 0;
static int64_t pcc_gc_backend4_store_buffer_duplicate_skips_count = 0;
static int64_t pcc_gc_backend4_store_buffer_high_water_count = 0;
static int64_t pcc_gc_backend4_store_buffer_owner_fanout_high_water_count = 0;
static int64_t pcc_gc_backend4_store_buffer_owner_count_high_water_count = 0;
static int64_t pcc_gc_backend4_store_buffer_incomplete_drains_count = 0;
static int64_t pcc_gc_backend4_evacuation_incomplete_batches_count = 0;
static int64_t pcc_gc_backend4_store_buffer_max_batch_size_count = 0;
static int64_t pcc_gc_backend4_store_buffer_full_batches_count = 0;
static int64_t pcc_gc_backend4_remembered_set_entries_count = 0;
static int64_t pcc_gc_backend4_remembered_set_duplicate_skips_count = 0;
static int64_t pcc_gc_backend4_remembered_set_high_water_count = 0;
static int64_t pcc_gc_backend4_remembered_page_entries_count = 0;
static int64_t pcc_gc_backend4_remembered_page_slot_entries_count = 0;
static int64_t pcc_gc_backend4_remembered_page_high_water_count = 0;
static int64_t pcc_gc_backend4_store_buffer_medium_flushes_count = 0;
static int64_t pcc_gc_backend4_store_buffer_medium_flushed_entries_count = 0;
static int64_t pcc_gc_backend4_store_buffer_medium_full_flushes_count = 0;
static int64_t pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count = 0;
static int64_t pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count = 0;

static void pcc_gc_cms_maybe_start_worker(void);

static int32_t pcc_gc_mark_active_load(void) {
    return __atomic_load_n(&pcc_gc_mark_active, __ATOMIC_ACQUIRE);
}

static void pcc_gc_mark_active_store(int32_t value) {
    __atomic_store_n(&pcc_gc_mark_active, value, __ATOMIC_RELEASE);
}

static int32_t pcc_gc_cycle_requested_load(void) {
    return __atomic_load_n(&pcc_gc_cycle_requested, __ATOMIC_ACQUIRE);
}

static void pcc_gc_cycle_requested_store(int32_t value) {
    __atomic_store_n(&pcc_gc_cycle_requested, value, __ATOMIC_RELEASE);
}

static void pcc_gc_cms_queue_lock(void) {
    while (__atomic_test_and_set(
            &pcc_gc_cms_queue_lock_word, __ATOMIC_ACQUIRE
        )) {
        pcc_thread_safepoint();
    }
}

static void pcc_gc_cms_queue_unlock(void) {
    __atomic_clear(&pcc_gc_cms_queue_lock_word, __ATOMIC_RELEASE);
}

static void pcc_gc_metric_add(int64_t metric, int64_t delta) {
    if (metric < 0 || metric > PCC_GC_COUNTER_WORK_STEPS) return;
    __atomic_add_fetch(&pcc_gc_metrics[metric], delta, __ATOMIC_RELAXED);
}

static int64_t pcc_gc_metric_load(int64_t metric) {
    if (metric < 0 || metric > PCC_GC_COUNTER_WORK_STEPS) return -1;
    return __atomic_load_n(&pcc_gc_metrics[metric], __ATOMIC_RELAXED);
}

static void pcc_gc_live_bytes_subtract(int64_t size) {
    if (size <= 0) return;
    int64_t old_live = __atomic_load_n(&pcc_gc_live_bytes, __ATOMIC_RELAXED);
    while (old_live > 0) {
        int64_t new_live = size >= old_live ? 0 : old_live - size;
        if (__atomic_compare_exchange_n(
                &pcc_gc_live_bytes,
                &old_live,
                new_live,
                0,
                __ATOMIC_ACQ_REL,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static int pcc_gc_object_node_is_freeing(PccGcObjectNode *n) {
    if (n == NULL) return 1;
    return __atomic_load_n(&n->freeing, __ATOMIC_ACQUIRE) != 0;
}

static void pcc_gc_object_node_set_freeing(PccGcObjectNode *n) {
    if (n == NULL) return;
    __atomic_store_n(&n->freeing, 1, __ATOMIC_RELEASE);
}

static int pcc_gc_object_node_is_active(PccGcObjectNode *n) {
    return n != NULL
        && !pcc_gc_object_node_is_freeing(n)
        && n->obj != NULL
        && !PY_IS_TAGGED_INT(n->obj);
}

static int64_t pcc_gc_now_us(void) {
    struct timeval tv;
    if (gettimeofday(&tv, NULL) != 0) return 0;
    return (int64_t)tv.tv_sec * 1000000LL + (int64_t)tv.tv_usec;
}

static int64_t pcc_gc_parse_env_i64(
    const char *name,
    int64_t default_value,
    int64_t min_value,
    int64_t max_value
) {
    const char *raw = getenv(name);
    if (raw == NULL || raw[0] == '\0') return default_value;
    errno = 0;
    char *end = NULL;
    long long value = strtoll(raw, &end, 10);
    if (errno != 0 || end == raw || *end != '\0') return default_value;
    if ((int64_t)value < min_value) return min_value;
    if ((int64_t)value > max_value) return max_value;
    return (int64_t)value;
}

static void pcc_gc_init_config(void) {
    if (pcc_gc_config_initialized) return;
    pcc_gc_config_initialized = 1;
    int64_t backend = pcc_gc_parse_env_i64(
        "PCC_GC_BACKEND",
        pcc_gc_selected_backend,
        PCC_GC_KIND_REFCOUNT_CYCLE,
        PCC_GC_KIND_COLORED_RELOCATING
    );
    pcc_gc_selected_backend = backend;
    pcc_gc_gcpause = pcc_gc_parse_env_i64("PCC_GC_PAUSE", 200, 50, 1000);
    pcc_gc_gcstepmul = pcc_gc_parse_env_i64("PCC_GC_STEPMUL", 200, 1, 10000);
    pcc_gc_gcstepmul = pcc_gc_parse_env_i64(
        "PCC_GC_STEP_MUL",
        pcc_gc_gcstepmul,
        1,
        10000
    );
    pcc_gc_debt_threshold_override = pcc_gc_parse_env_i64(
        "PCC_GC_DEBT_THRESHOLD",
        0,
        0,
        1LL << 40
    );
    pcc_gc_minor_heap_size = pcc_gc_parse_env_i64(
        "PCC_GC_MINOR_HEAP_SIZE",
        1048576,
        256,
        1LL << 40
    );
    pcc_gc_minor_alloc_max = pcc_gc_parse_env_i64(
        "PCC_GC_MINOR_ALLOC_MAX",
        256,
        16,
        1LL << 30
    );
    if (pcc_gc_selected_backend != PCC_GC_KIND_REFCOUNT_CYCLE) {
        pcc_gc_cycle_requested_store(1);
    }
    pcc_gc_cms_maybe_start_worker();
}

static int64_t pcc_gc_debt_threshold(void) {
    if (pcc_gc_debt_threshold_override > 0) {
        return pcc_gc_debt_threshold_override;
    }
    int64_t threshold = PCC_GC_DEFAULT_DEBT_THRESHOLD;
    int64_t live_bytes = __atomic_load_n(
        &pcc_gc_live_bytes, __ATOMIC_RELAXED
    );
    if (live_bytes > 0 && pcc_gc_gcpause > 100) {
        int64_t live_pause = (
            live_bytes * (pcc_gc_gcpause - 100)
        ) / 100;
        if (live_pause > threshold) threshold = live_pause;
    }
    return threshold;
}

static int64_t pcc_gc_budget_from_debt(void) {
    int64_t debt = __atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_RELAXED);
    int64_t budget = debt / PCC_GC_WORK_BYTES;
    budget = (budget * pcc_gc_gcstepmul) / 100;
    if (budget < 1) budget = 1;
    if (budget > PCC_GC_MAX_AUTO_STEP_BUDGET) {
        budget = PCC_GC_MAX_AUTO_STEP_BUDGET;
    }
    return budget;
}

static void pcc_gc_discharge_debt(int64_t processed) {
    if (processed <= 0) return;
    int64_t credit = (
        processed * PCC_GC_WORK_BYTES * pcc_gc_gcstepmul
    ) / 100;
    if (credit < PCC_GC_WORK_BYTES) credit = PCC_GC_WORK_BYTES;
    int64_t old_debt = __atomic_load_n(
        &pcc_gc_debt_bytes, __ATOMIC_RELAXED
    );
    while (old_debt > 0) {
        int64_t new_debt = credit >= old_debt ? 0 : old_debt - credit;
        if (__atomic_compare_exchange_n(
                &pcc_gc_debt_bytes,
                &old_debt,
                new_debt,
                0,
                __ATOMIC_ACQ_REL,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static void pcc_gc_record_pause(int64_t start_us, int64_t end_us) {
    if (start_us <= 0 || end_us < start_us) return;
    int64_t pause = end_us - start_us;
    int64_t old_pause = __atomic_load_n(
        &pcc_gc_max_pause_us, __ATOMIC_RELAXED
    );
    while (pause > old_pause) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_max_pause_us,
                &old_pause,
                pause,
                0,
                __ATOMIC_ACQ_REL,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static int pcc_gc_graph_try_lock(void) {
    int32_t expected = 0;
    return __atomic_compare_exchange_n(
        &pcc_gc_graph_lock_state,
        &expected,
        1,
        0,
        __ATOMIC_ACQ_REL,
        __ATOMIC_ACQUIRE
    );
}

static void pcc_gc_graph_lock(void) {
    if (pcc_gc_graph_lock_depth > 0) {
        pcc_gc_graph_lock_depth++;
        return;
    }
    while (!pcc_gc_graph_try_lock()) {
        pcc_thread_safepoint();
        usleep(100);
    }
    pcc_gc_graph_lock_depth = 1;
}

static void pcc_gc_graph_unlock(void) {
    if (pcc_gc_graph_lock_depth <= 0) return;
    pcc_gc_graph_lock_depth--;
    if (pcc_gc_graph_lock_depth > 0) return;
    __atomic_store_n(&pcc_gc_graph_lock_state, 0, __ATOMIC_RELEASE);
}

void pcc_gc_root_slot_lock(void) {
    pcc_gc_graph_lock();
}

void pcc_gc_root_slot_unlock(void) {
    pcc_gc_graph_unlock();
}

static int pcc_gc_cms_queue_push_unlocked(int64_t work) {
    if (work == 0) work = 1;
    int32_t tail = pcc_gc_cms_queue_tail;
    int32_t head = pcc_gc_cms_queue_head;
    int32_t next = tail + 1;
    if (next >= PCC_GC_CMS_QUEUE_CAPACITY) next = 0;
    if (next == head) {
        return 0;
    }
    pcc_gc_cms_queue[tail] = work;
    pcc_gc_cms_queue_tail = next;
    return 1;
}

static int pcc_gc_cms_queue_push(int64_t work) {
    pcc_gc_cms_queue_lock();
    int pushed = pcc_gc_cms_queue_push_unlocked(work);
    pcc_gc_cms_queue_unlock();
    if (pushed) {
        __atomic_add_fetch(
            &pcc_gc_cms_queue_pushes, 1, __ATOMIC_RELAXED
        );
    }
    return pushed;
}

static int pcc_gc_cms_queue_push_gray_batch(
    PyObject **objects,
    int32_t count
) {
    if (objects == NULL || count <= 0) return 0;
    int pushed = 0;
    pcc_gc_cms_queue_lock();
    for (int32_t i = 0; i < count; i++) {
        PyObject *o = objects[i];
        if (o == NULL || PY_IS_TAGGED_INT(o)) continue;
        uintptr_t raw = (uintptr_t)o;
        if (raw == 0 || raw > (uintptr_t)INT64_MAX) continue;
        if (!pcc_gc_cms_queue_push_unlocked(-((int64_t)raw))) break;
        pushed++;
    }
    pcc_gc_cms_queue_unlock();
    if (pushed > 0) {
        __atomic_add_fetch(
            &pcc_gc_cms_queue_pushes, pushed, __ATOMIC_RELAXED
        );
    }
    return pushed;
}

static void pcc_gc_cms_flush_wb_buffer(void) {
    int32_t count = pcc_gc_cms_wb_buffer_count;
    if (count <= 0) return;
    if (count > PCC_GC_CMS_WB_BUFFER_CAPACITY) {
        count = PCC_GC_CMS_WB_BUFFER_CAPACITY;
    }
    (void)pcc_gc_cms_queue_push_gray_batch(pcc_gc_cms_wb_buffer, count);
    for (int32_t i = 0; i < count; i++) {
        pcc_gc_cms_wb_buffer[i] = NULL;
    }
    pcc_gc_cms_wb_buffer_count = 0;
    __atomic_add_fetch(&pcc_gc_cms_wb_flushes, 1, __ATOMIC_RELAXED);
}

static int pcc_gc_cms_buffer_gray(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    int32_t count = pcc_gc_cms_wb_buffer_count;
    if (count >= PCC_GC_CMS_WB_BUFFER_CAPACITY) return 1;
    pcc_gc_cms_wb_buffer[count] = o;
    count++;
    pcc_gc_cms_wb_buffer_count = count;
    return count >= PCC_GC_CMS_WB_BUFFER_CAPACITY;
}

static int pcc_gc_cms_queue_pop(int64_t *work) {
    pcc_gc_cms_queue_lock();
    int32_t head = pcc_gc_cms_queue_head;
    int32_t tail = pcc_gc_cms_queue_tail;
    if (head == tail) {
        pcc_gc_cms_queue_unlock();
        return 0;
    }
    if (work != NULL) *work = pcc_gc_cms_queue[head];
    head++;
    if (head >= PCC_GC_CMS_QUEUE_CAPACITY) head = 0;
    pcc_gc_cms_queue_head = head;
    pcc_gc_cms_queue_unlock();
    return 1;
}

static void *pcc_gc_cms_worker_main(void *arg) {
    (void)arg;
    for (;;) {
        int64_t work = 0;
        int drained = 0;
        while (drained < 64 && pcc_gc_cms_queue_pop(&work)) {
            int64_t traced = 0;
            int64_t worker_stw = pcc_stop_the_world();
            if (worker_stw != 0) {
                drained++;
                pcc_thread_safepoint();
                continue;
            }
            pcc_gc_graph_lock();
            if (
                pcc_gc_selected_backend
                != PCC_GC_KIND_CONCURRENT_MARK_SWEEP
            ) {
                pcc_gc_graph_unlock();
                if (worker_stw == 0) (void)pcc_resume_world();
                break;
            }
            if (work < 0) {
                PyObject *gray = (PyObject *)(uintptr_t)(-work);
                traced = pcc_gc_cms_trace_gray_object_unlocked(gray);
            } else {
                int64_t budget = work / PCC_GC_WORK_BYTES;
                if (budget < 1) budget = 1;
                if (budget > 64) budget = 64;
                traced = pcc_gc_cms_worker_trace_cycle_unlocked(budget);
            }
            pcc_gc_graph_unlock();
            if (worker_stw == 0) (void)pcc_resume_world();
            if (traced > 0) {
                __atomic_add_fetch(
                    &pcc_gc_cms_worker_traces,
                    traced,
                    __ATOMIC_RELAXED
                );
            }
            __atomic_add_fetch(
                &pcc_gc_cms_worker_drains, 1, __ATOMIC_RELAXED
            );
            drained++;
        }
        pcc_thread_safepoint();
        if (__atomic_load_n(
                &pcc_gc_cms_worker_stop_requested,
                __ATOMIC_ACQUIRE
            ) != 0) {
            break;
        }
        if (drained == 0) usleep(1000);
    }
    __atomic_add_fetch(
        &pcc_gc_cms_worker_stops, 1, __ATOMIC_RELAXED
    );
    return NULL;
}

static void pcc_gc_cms_maybe_start_worker(void) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_CONCURRENT_MARK_SWEEP) return;
    if (!pcc_threads_enabled()) return;
    __atomic_store_n(
        &pcc_gc_cms_worker_stop_requested, 0, __ATOMIC_RELEASE
    );
    int32_t expected = 0;
    if (!__atomic_compare_exchange_n(
            &pcc_gc_cms_worker_started,
            &expected,
            1,
            0,
            __ATOMIC_ACQ_REL,
            __ATOMIC_ACQUIRE
        )) {
        return;
    }
    PccThreadHandle *handle = NULL;
    if (pcc_thread_start(&handle, pcc_gc_cms_worker_main, NULL) == 0) {
        pcc_gc_cms_worker_handle = handle;
        __atomic_add_fetch(
            &pcc_gc_cms_worker_starts, 1, __ATOMIC_RELAXED
        );
        return;
    }
    __atomic_store_n(&pcc_gc_cms_worker_started, 0, __ATOMIC_RELEASE);
}

static void pcc_gc_cms_stop_worker(void) {
    if (!pcc_threads_enabled()) return;
    PccThreadHandle *handle = pcc_gc_cms_worker_handle;
    if (
        __atomic_load_n(&pcc_gc_cms_worker_started, __ATOMIC_ACQUIRE) == 0
        || handle == NULL
    ) {
        return;
    }
    __atomic_store_n(
        &pcc_gc_cms_worker_stop_requested, 1, __ATOMIC_RELEASE
    );
    (void)pcc_thread_join(handle, NULL);
    pcc_gc_cms_worker_handle = NULL;
    __atomic_store_n(&pcc_gc_cms_worker_started, 0, __ATOMIC_RELEASE);
    __atomic_store_n(
        &pcc_gc_cms_worker_stop_requested, 0, __ATOMIC_RELEASE
    );
    pcc_gc_cms_queue_lock();
    pcc_gc_cms_queue_head = 0;
    pcc_gc_cms_queue_tail = 0;
    pcc_gc_cms_queue_unlock();
}

static PccGcForwardNode *pcc_gc_forwarding_find(PyObject *from) {
    if (from == NULL || PY_IS_TAGGED_INT(from)) return NULL;
    for (PccGcForwardNode *n = pcc_gc_forwardings; n != NULL; n = n->next) {
        if (n->from == from) return n;
    }
    return NULL;
}

static int pcc_gc_forwarding_target_exists(PyObject *target) {
    if (target == NULL || PY_IS_TAGGED_INT(target)) return 0;
    for (PccGcForwardNode *n = pcc_gc_forwardings; n != NULL; n = n->next) {
        if (n->to == target) return 1;
    }
    return 0;
}

static void pcc_gc_forwarding_remove(PyObject *from) {
    if (from == NULL || PY_IS_TAGGED_INT(from)) return;
    PccGcForwardNode **cur = &pcc_gc_forwardings;
    while (*cur != NULL) {
        if ((*cur)->from == from) {
            PccGcForwardNode *dead = *cur;
            *cur = dead->next;
            py_decref(dead->to);
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_forwarding_clear_all(void) {
    PccGcForwardNode *n = pcc_gc_forwardings;
    pcc_gc_forwardings = NULL;
    while (n != NULL) {
        PccGcForwardNode *next = n->next;
        py_decref(n->to);
        free(n);
        n = next;
    }
}

static PccGcIdentityNode *pcc_gc_identity_find(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    for (PccGcIdentityNode *n = pcc_gc_identities; n != NULL; n = n->next) {
        if (n->obj == obj) return n;
    }
    return NULL;
}

static PccGcIdentityNode *pcc_gc_identity_ensure(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    PccGcIdentityNode *existing = pcc_gc_identity_find(obj);
    if (existing != NULL) return existing;

    PccGcIdentityNode *n = (
        PccGcIdentityNode *
    )calloc(1, sizeof(PccGcIdentityNode));
    if (n == NULL) return NULL;
    if (pcc_gc_next_object_id <= 0) pcc_gc_next_object_id = 1;
    n->obj = obj;
    n->id = pcc_gc_next_object_id++;
    n->next = pcc_gc_identities;
    pcc_gc_identities = n;
    return n;
}

static int pcc_gc_identity_assign(PyObject *obj, int64_t id) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || id <= 0) return 0;
    PccGcIdentityNode *existing = pcc_gc_identity_find(obj);
    if (existing != NULL) {
        existing->id = id;
        return 1;
    }

    PccGcIdentityNode *n = (
        PccGcIdentityNode *
    )calloc(1, sizeof(PccGcIdentityNode));
    if (n == NULL) return 0;
    n->obj = obj;
    n->id = id;
    n->next = pcc_gc_identities;
    pcc_gc_identities = n;
    return 1;
}

static void pcc_gc_identity_remove(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return;
    PccGcIdentityNode **cur = &pcc_gc_identities;
    while (*cur != NULL) {
        if ((*cur)->obj == obj) {
            PccGcIdentityNode *dead = *cur;
            *cur = dead->next;
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_identity_clear_all(void) {
    PccGcIdentityNode *n = pcc_gc_identities;
    pcc_gc_identities = NULL;
    while (n != NULL) {
        PccGcIdentityNode *next = n->next;
        free(n);
        n = next;
    }
}

int64_t pcc_gc_object_id(PyObject *o) {
    pcc_gc_init_config();
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    PccGcIdentityNode *identity = pcc_gc_identity_ensure(o);
    if (identity == NULL) return 0;
    return identity->id;
}

static PccGcRelocationNode *pcc_gc_relocation_set_find(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    for (
        PccGcRelocationNode *n = pcc_gc_relocation_set;
        n != NULL;
        n = n->next
    ) {
        if (n->obj == obj) return n;
    }
    return NULL;
}

static int pcc_gc_relocation_set_add(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    PyObjectHeader *h = py_header(obj);
    if ((py_header_flags_load(h) & PY_FLAG_GC_RELOCATION_TARGET) != 0) {
        return 0;
    }
    if (pcc_gc_forwarding_find(obj) != NULL) return 0;
    if (pcc_gc_forwarding_target_exists(obj)) return 0;
    if (pcc_gc_relocation_set_find(obj) != NULL) return 0;
    PccGcRelocationNode *n = (
        PccGcRelocationNode *
    )calloc(1, sizeof(PccGcRelocationNode));
    if (n == NULL) return 0;
    n->obj = obj;
    n->next = pcc_gc_relocation_set;
    pcc_gc_relocation_set = n;
    py_header_flags_or(h, PY_FLAG_GC_RELOCATION_CANDIDATE);
    return 1;
}

static void pcc_gc_relocation_set_remove(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return;
    PccGcRelocationNode **cur = &pcc_gc_relocation_set;
    while (*cur != NULL) {
        if ((*cur)->obj == obj) {
            PccGcRelocationNode *dead = *cur;
            *cur = dead->next;
            py_header_flags_and(py_header(obj), ~PY_FLAG_GC_RELOCATION_CANDIDATE);
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_backend4_store_buffer_dec_unlocked(void) {
    int64_t pending = __atomic_load_n(
        &pcc_gc_backend4_store_buffer_entries_count,
        __ATOMIC_RELAXED
    );
    if (pending <= 0) return;
    __atomic_sub_fetch(
        &pcc_gc_backend4_store_buffer_entries_count,
        1,
        __ATOMIC_RELAXED
    );
}

static void pcc_gc_backend4_store_buffer_note_high_water(int64_t current) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_store_buffer_high_water_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_store_buffer_high_water_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static void pcc_gc_backend4_store_buffer_note_owner_fanout_high_water(
    int64_t current
) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_store_buffer_owner_fanout_high_water_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_store_buffer_owner_fanout_high_water_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static PccGcStoreBufferMediumState *
pcc_gc_backend4_store_buffer_register_medium_locked(void) {
    if (pcc_gc_backend4_store_buffer_medium_state != NULL) {
        return pcc_gc_backend4_store_buffer_medium_state;
    }
    PccGcStoreBufferMediumState *state = (
        PccGcStoreBufferMediumState *
    )calloc(1, sizeof(PccGcStoreBufferMediumState));
    if (state == NULL) return NULL;
    state->entries = pcc_gc_backend4_store_buffer_medium;
    state->count = &pcc_gc_backend4_store_buffer_medium_count;
    state->next = pcc_gc_backend4_store_buffer_medium_states;
    pcc_gc_backend4_store_buffer_medium_states = state;
    pcc_gc_backend4_store_buffer_medium_state = state;
    return state;
}

static int64_t pcc_gc_backend4_store_buffer_owner_fanout(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    int64_t count = 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            if (state->entries[i].owner == owner) count++;
        }
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner) count++;
    }
    return count;
}

static int64_t pcc_gc_backend4_store_buffer_owner_count(void) {
    int64_t count = 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            PyObject *owner = state->entries[i].owner;
            int seen = 0;
            for (
                PccGcStoreBufferMediumState *prev =
                    pcc_gc_backend4_store_buffer_medium_states;
                prev != NULL;
                prev = prev->next
            ) {
                int32_t prev_count = prev->count == NULL ? 0 : *prev->count;
                int32_t stop = prev == state ? i : prev_count;
                for (int32_t p = 0; p < stop; p++) {
                    if (prev->entries[p].owner == owner) {
                        seen = 1;
                        break;
                    }
                }
                if (seen || prev == state) break;
            }
            if (!seen) count++;
        }
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        int seen = 0;
        for (
            PccGcStoreBufferMediumState *state =
                pcc_gc_backend4_store_buffer_medium_states;
            state != NULL;
            state = state->next
        ) {
            int32_t medium_count = state->count == NULL ? 0 : *state->count;
            for (int32_t i = 0; i < medium_count; i++) {
                if (state->entries[i].owner == n->owner) {
                    seen = 1;
                    break;
                }
            }
            if (seen) break;
        }
        for (
            PccGcStoreBufferNode *p = pcc_gc_backend4_store_buffer;
            p != NULL && p != n;
            p = p->next
        ) {
            if (p->owner == n->owner) {
                seen = 1;
                break;
            }
        }
        if (!seen) count++;
    }
    return count;
}

static int64_t pcc_gc_backend4_store_buffer_entry_count(void) {
    int64_t count = 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        count += state->count == NULL ? 0 : *state->count;
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        count++;
    }
    return count;
}

static int64_t pcc_gc_backend4_store_buffer_max_owner_fanout(void) {
    int64_t max_fanout = 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            int64_t fanout = pcc_gc_backend4_store_buffer_owner_fanout(
                state->entries[i].owner
            );
            if (fanout > max_fanout) max_fanout = fanout;
        }
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        int64_t fanout = pcc_gc_backend4_store_buffer_owner_fanout(n->owner);
        if (fanout > max_fanout) max_fanout = fanout;
    }
    return max_fanout;
}

static void pcc_gc_backend4_reset_store_buffer_epoch_state(void) {
    pcc_gc_graph_lock();
    int64_t entries = pcc_gc_backend4_store_buffer_entry_count();
    int64_t owner_fanout = pcc_gc_backend4_store_buffer_max_owner_fanout();
    int64_t owner_count = pcc_gc_backend4_store_buffer_owner_count();
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_entries_count,
        entries,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_high_water_count,
        entries,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_owner_fanout_high_water_count,
        owner_fanout,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_owner_count_high_water_count,
        owner_count,
        __ATOMIC_RELAXED
    );
    pcc_gc_graph_unlock();
}

static void pcc_gc_backend4_store_buffer_note_owner_count_high_water(
    int64_t current
) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_store_buffer_owner_count_high_water_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_store_buffer_owner_count_high_water_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static void pcc_gc_backend4_store_buffer_note_max_batch(int64_t current) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_store_buffer_max_batch_size_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_store_buffer_max_batch_size_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static void pcc_gc_backend4_remembered_set_note_high_water(int64_t current) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_remembered_set_high_water_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_remembered_set_high_water_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static uintptr_t pcc_gc_backend4_remembered_page_key(PyObject **slot) {
    return ((uintptr_t)slot) & ~(uintptr_t)(PCC_GC_BACKEND4_SMALL_PAGE_LIMIT - 1);
}

static int32_t pcc_gc_backend4_remembered_page_slot_bit(PyObject **slot) {
    uintptr_t offset = ((uintptr_t)slot)
        & (uintptr_t)(PCC_GC_BACKEND4_SMALL_PAGE_LIMIT - 1);
    return (int32_t)(offset / sizeof(PyObject *));
}

static void pcc_gc_backend4_remembered_page_note_high_water(int64_t current) {
    int64_t old = __atomic_load_n(
        &pcc_gc_backend4_remembered_page_high_water_count,
        __ATOMIC_RELAXED
    );
    while (current > old) {
        if (__atomic_compare_exchange_n(
                &pcc_gc_backend4_remembered_page_high_water_count,
                &old,
                current,
                0,
                __ATOMIC_RELAXED,
                __ATOMIC_RELAXED
            )) {
            return;
        }
    }
}

static PccGcRememberedPageNode *pcc_gc_backend4_remembered_page_find(
    uintptr_t page_key
) {
    for (
        PccGcRememberedPageNode *n = pcc_gc_backend4_remembered_pages;
        n != NULL;
        n = n->next
    ) {
        if (n->page_key == page_key) return n;
    }
    return NULL;
}

static int pcc_gc_backend4_remembered_page_contains_slot_unlocked(
    PyObject **slot
) {
    if (slot == NULL) return 0;
    uintptr_t page_key = pcc_gc_backend4_remembered_page_key(slot);
    PccGcRememberedPageNode *page =
        pcc_gc_backend4_remembered_page_find(page_key);
    if (page == NULL) return 0;
    int32_t bit = pcc_gc_backend4_remembered_page_slot_bit(slot);
    if (bit < 0 || bit >= 512) return 0;
    int32_t word = bit / 64;
    uint64_t mask = (uint64_t)1 << (uint64_t)(bit % 64);
    return (page->slot_bitmap[word] & mask) != 0;
}

static void pcc_gc_backend4_remembered_page_add(PyObject **slot) {
    if (slot == NULL) return;
    uintptr_t page_key = pcc_gc_backend4_remembered_page_key(slot);
    PccGcRememberedPageNode *page =
        pcc_gc_backend4_remembered_page_find(page_key);
    if (page == NULL) {
        page = (
            PccGcRememberedPageNode *
        )calloc(1, sizeof(PccGcRememberedPageNode));
        if (page == NULL) return;
        page->page_key = page_key;
        page->next = pcc_gc_backend4_remembered_pages;
        pcc_gc_backend4_remembered_pages = page;
        int64_t pages = __atomic_add_fetch(
            &pcc_gc_backend4_remembered_page_entries_count,
            1,
            __ATOMIC_RELAXED
        );
        pcc_gc_backend4_remembered_page_note_high_water(pages);
    }
    int32_t bit = pcc_gc_backend4_remembered_page_slot_bit(slot);
    if (bit < 0 || bit >= 512) return;
    int32_t word = bit / 64;
    uint64_t mask = (uint64_t)1 << (uint64_t)(bit % 64);
    if ((page->slot_bitmap[word] & mask) != 0) return;
    page->slot_bitmap[word] |= mask;
    page->slots++;
    __atomic_add_fetch(
        &pcc_gc_backend4_remembered_page_slot_entries_count,
        1,
        __ATOMIC_RELAXED
    );
}

static void pcc_gc_backend4_remembered_page_remove_slot(PyObject **slot) {
    if (slot == NULL) return;
    uintptr_t page_key = pcc_gc_backend4_remembered_page_key(slot);
    PccGcRememberedPageNode **cur = &pcc_gc_backend4_remembered_pages;
    while (*cur != NULL) {
        if ((*cur)->page_key == page_key) {
            PccGcRememberedPageNode *page = *cur;
            int32_t bit = pcc_gc_backend4_remembered_page_slot_bit(slot);
            if (bit < 0 || bit >= 512) return;
            int32_t word = bit / 64;
            uint64_t mask = (uint64_t)1 << (uint64_t)(bit % 64);
            if ((page->slot_bitmap[word] & mask) == 0) return;
            page->slot_bitmap[word] &= ~mask;
            if (page->slots > 0) page->slots--;
            int64_t slots = __atomic_load_n(
                &pcc_gc_backend4_remembered_page_slot_entries_count,
                __ATOMIC_RELAXED
            );
            if (slots > 0) {
                __atomic_sub_fetch(
                    &pcc_gc_backend4_remembered_page_slot_entries_count,
                    1,
                    __ATOMIC_RELAXED
                );
            }
            if (page->slots <= 0) {
                *cur = page->next;
                int64_t pages = __atomic_load_n(
                    &pcc_gc_backend4_remembered_page_entries_count,
                    __ATOMIC_RELAXED
                );
                if (pages > 0) {
                    __atomic_sub_fetch(
                        &pcc_gc_backend4_remembered_page_entries_count,
                        1,
                        __ATOMIC_RELAXED
                    );
                }
                free(page);
            }
            return;
        }
        cur = &(*cur)->next;
    }
}

static int pcc_gc_backend4_remembered_set_contains(
    PyObject *owner,
    PyObject **slot
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    if (slot == NULL) return 0;
    for (
        PccGcRememberedSlotNode *n = pcc_gc_backend4_remembered_slots;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner && n->slot == slot) return 1;
    }
    return 0;
}

static int pcc_gc_backend4_remembered_set_add(
    PyObject *owner,
    PyObject **slot
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    if (slot == NULL) return 0;
    if (pcc_gc_backend4_remembered_set_contains(owner, slot)) {
        __atomic_add_fetch(
            &pcc_gc_backend4_remembered_set_duplicate_skips_count,
            1,
            __ATOMIC_RELAXED
        );
        return 0;
    }
    PccGcRememberedSlotNode *n = (
        PccGcRememberedSlotNode *
    )calloc(1, sizeof(PccGcRememberedSlotNode));
    if (n == NULL) return 0;
    n->owner = owner;
    n->slot = slot;
    n->next = pcc_gc_backend4_remembered_slots;
    pcc_gc_backend4_remembered_slots = n;
    int64_t entries = __atomic_add_fetch(
        &pcc_gc_backend4_remembered_set_entries_count,
        1,
        __ATOMIC_RELAXED
    );
    pcc_gc_backend4_remembered_set_note_high_water(entries);
    pcc_gc_backend4_remembered_page_add(slot);
    pcc_gc_backend4_zpage_note_remembered_slot_unlocked(owner, 1);
    pcc_gc_backend4_zpage_note_remembered_card_unlocked(owner, slot, 1);
    return 1;
}

static int pcc_gc_backend4_remembered_set_remove_slot(PyObject **slot) {
    if (slot == NULL) return 0;
    int removed = 0;
    PccGcRememberedSlotNode **cur = &pcc_gc_backend4_remembered_slots;
    while (*cur != NULL) {
        if ((*cur)->slot == slot) {
            PccGcRememberedSlotNode *dead = *cur;
            *cur = dead->next;
            pcc_gc_backend4_remembered_page_remove_slot(dead->slot);
            pcc_gc_backend4_zpage_note_remembered_slot_unlocked(
                dead->owner,
                -1
            );
            pcc_gc_backend4_zpage_note_remembered_card_unlocked(
                dead->owner,
                dead->slot,
                -1
            );
            int64_t entries = __atomic_load_n(
                &pcc_gc_backend4_remembered_set_entries_count,
                __ATOMIC_RELAXED
            );
            if (entries > 0) {
                __atomic_sub_fetch(
                    &pcc_gc_backend4_remembered_set_entries_count,
                    1,
                    __ATOMIC_RELAXED
                );
            }
            free(dead);
            removed = 1;
            continue;
        }
        cur = &(*cur)->next;
    }
    return removed;
}

static void pcc_gc_backend4_remembered_set_remove(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    PccGcRememberedSlotNode **cur = &pcc_gc_backend4_remembered_slots;
    while (*cur != NULL) {
        if ((*cur)->owner == owner) {
            PccGcRememberedSlotNode *dead = *cur;
            *cur = dead->next;
            pcc_gc_backend4_remembered_page_remove_slot(dead->slot);
            pcc_gc_backend4_zpage_note_remembered_slot_unlocked(
                dead->owner,
                -1
            );
            pcc_gc_backend4_zpage_note_remembered_card_unlocked(
                dead->owner,
                dead->slot,
                -1
            );
            int64_t entries = __atomic_load_n(
                &pcc_gc_backend4_remembered_set_entries_count,
                __ATOMIC_RELAXED
            );
            if (entries > 0) {
                __atomic_sub_fetch(
                    &pcc_gc_backend4_remembered_set_entries_count,
                    1,
                    __ATOMIC_RELAXED
                );
            }
            free(dead);
            continue;
        }
        cur = &(*cur)->next;
    }
}

static int64_t pcc_gc_backend4_remembered_set_entry_count(void) {
    int64_t count = 0;
    for (
        PccGcRememberedSlotNode *n = pcc_gc_backend4_remembered_slots;
        n != NULL;
        n = n->next
    ) {
        count++;
    }
    return count;
}

static void pcc_gc_backend4_reset_remembered_set_epoch_state(void) {
    pcc_gc_graph_lock();
    int64_t entries = pcc_gc_backend4_remembered_set_entry_count();
    int64_t pages = __atomic_load_n(
        &pcc_gc_backend4_remembered_page_entries_count,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_set_entries_count,
        entries,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_set_high_water_count,
        entries,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_page_high_water_count,
        pages,
        __ATOMIC_RELAXED
    );
    pcc_gc_graph_unlock();
}

static void pcc_gc_backend4_remembered_set_clear(void) {
    PccGcRememberedSlotNode *n = pcc_gc_backend4_remembered_slots;
    pcc_gc_backend4_remembered_slots = NULL;
    while (n != NULL) {
        PccGcRememberedSlotNode *next = n->next;
        pcc_gc_backend4_zpage_note_remembered_slot_unlocked(n->owner, -1);
        pcc_gc_backend4_zpage_note_remembered_card_unlocked(
            n->owner,
            n->slot,
            -1
        );
        free(n);
        n = next;
    }
    PccGcRememberedPageNode *page = pcc_gc_backend4_remembered_pages;
    pcc_gc_backend4_remembered_pages = NULL;
    while (page != NULL) {
        PccGcRememberedPageNode *next = page->next;
        free(page);
        page = next;
    }
    __atomic_store_n(
        &pcc_gc_backend4_remembered_set_entries_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_set_high_water_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_page_entries_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_page_slot_entries_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_remembered_page_high_water_count, 0, __ATOMIC_RELAXED
    );
}

static int pcc_gc_backend4_store_buffer_owner_pending(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            if (state->entries[i].owner == owner) return 1;
        }
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner) return 1;
    }
    return 0;
}

static int pcc_gc_backend4_store_buffer_contains(
    PyObject *owner,
    PyObject **slot,
    PyObject *value
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    if (value == NULL || PY_IS_TAGGED_INT(value)) return 0;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            PccGcStoreBufferEntry *entry = &state->entries[i];
            if (
                entry->owner == owner
                && entry->slot == slot
                && entry->value == value
            ) {
                return 1;
            }
        }
    }
    for (
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner && n->slot == slot && n->value == value) {
            return 1;
        }
    }
    return 0;
}

static void pcc_gc_backend4_store_buffer_append_global_owned(
    PyObject *owner,
    PyObject **slot,
    PyObject *value
) {
    PccGcStoreBufferNode *n = (
        PccGcStoreBufferNode *
    )calloc(1, sizeof(PccGcStoreBufferNode));
    if (n == NULL) {
        pcc_gc_backend4_store_buffer_dec_unlocked();
        py_decref(value);
        return;
    }
    n->owner = owner;
    n->slot = slot;
    n->value = value;
    n->next = pcc_gc_backend4_store_buffer;
    pcc_gc_backend4_store_buffer = n;
}

static void pcc_gc_backend4_store_buffer_flush_medium_state_locked(
    PccGcStoreBufferMediumState *state
) {
    if (state == NULL || state->count == NULL || state->entries == NULL) {
        return;
    }
    int32_t count = *state->count;
    if (count <= 0) return;
    for (int32_t i = 0; i < count; i++) {
        PccGcStoreBufferEntry *entry = &state->entries[i];
        pcc_gc_backend4_store_buffer_append_global_owned(
            entry->owner,
            entry->slot,
            entry->value
        );
        entry->owner = NULL;
        entry->slot = NULL;
        entry->value = NULL;
    }
    *state->count = 0;
    __atomic_add_fetch(
        &pcc_gc_backend4_store_buffer_medium_flushes_count,
        1,
        __ATOMIC_RELAXED
    );
    __atomic_add_fetch(
        &pcc_gc_backend4_store_buffer_medium_flushed_entries_count,
        count,
        __ATOMIC_RELAXED
    );
    if (count >= PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY) {
        __atomic_add_fetch(
            &pcc_gc_backend4_store_buffer_medium_full_flushes_count,
            1,
            __ATOMIC_RELAXED
        );
    }
}

static void pcc_gc_backend4_store_buffer_flush_all_medium_locked(void) {
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t before = state->count == NULL ? 0 : *state->count;
        int is_cross_thread =
            state != pcc_gc_backend4_store_buffer_medium_state;
        pcc_gc_backend4_store_buffer_flush_medium_state_locked(state);
        if (is_cross_thread && before > 0) {
            __atomic_add_fetch(
                &pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count,
                1,
                __ATOMIC_RELAXED
            );
            __atomic_add_fetch(
                &pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count,
                before,
                __ATOMIC_RELAXED
            );
        }
    }
}

static int pcc_gc_backend4_store_buffer_enqueue(
    PyObject *owner,
    PyObject **slot,
    PyObject *value
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    if (value == NULL || PY_IS_TAGGED_INT(value)) return 0;
    PccGcStoreBufferMediumState *state =
        pcc_gc_backend4_store_buffer_register_medium_locked();
    if (state == NULL || state->count == NULL || state->entries == NULL) {
        return 0;
    }
    if (pcc_gc_backend4_store_buffer_contains(owner, slot, value)) {
        __atomic_add_fetch(
            &pcc_gc_backend4_store_buffer_duplicate_skips_count,
            1,
            __ATOMIC_RELAXED
        );
        return 0;
    }
    PyObjectHeader *owner_h = py_header(owner);
    if (
        *state->count
        >= PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY
    ) {
        pcc_gc_backend4_store_buffer_flush_medium_state_locked(state);
    }
    if (
        *state->count
        >= PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY
    ) {
        return 0;
    }
    py_incref(value);
    PccGcStoreBufferEntry *entry = &state->entries[*state->count];
    entry->owner = owner;
    entry->slot = slot;
    entry->value = value;
    *state->count = *state->count + 1;
    (void)pcc_gc_backend4_remembered_set_add(owner, slot);
    py_header_flags_or(owner_h, PY_FLAG_GC_REMEMBERED);
    int64_t pending = __atomic_add_fetch(
        &pcc_gc_backend4_store_buffer_entries_count,
        1,
        __ATOMIC_RELAXED
    );
    pcc_gc_backend4_store_buffer_note_high_water(pending);
    pcc_gc_backend4_store_buffer_note_owner_fanout_high_water(
        pcc_gc_backend4_store_buffer_owner_fanout(owner)
    );
    pcc_gc_backend4_store_buffer_note_owner_count_high_water(
        pcc_gc_backend4_store_buffer_owner_count()
    );
    return 1;
}

static void pcc_gc_backend4_store_buffer_remove(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t write = 0;
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t read = 0; read < medium_count; read++) {
            PccGcStoreBufferEntry *entry = &state->entries[read];
            if (entry->owner == owner) {
                pcc_gc_backend4_store_buffer_dec_unlocked();
                py_decref(entry->value);
                continue;
            }
            if (write != read) {
                state->entries[write] = *entry;
            }
            write++;
        }
        for (int32_t i = write; i < medium_count; i++) {
            state->entries[i].owner = NULL;
            state->entries[i].slot = NULL;
            state->entries[i].value = NULL;
        }
        if (state->count != NULL) *state->count = write;
    }
    PccGcStoreBufferNode **cur = &pcc_gc_backend4_store_buffer;
    while (*cur != NULL) {
        if ((*cur)->owner == owner) {
            PccGcStoreBufferNode *dead = *cur;
            *cur = dead->next;
            pcc_gc_backend4_store_buffer_dec_unlocked();
            py_decref(dead->value);
            free(dead);
            continue;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_backend4_store_buffer_clear(void) {
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        int32_t medium_count = state->count == NULL ? 0 : *state->count;
        for (int32_t i = 0; i < medium_count; i++) {
            py_decref(state->entries[i].value);
            state->entries[i].owner = NULL;
            state->entries[i].slot = NULL;
            state->entries[i].value = NULL;
        }
        if (state->count != NULL) *state->count = 0;
    }
    PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
    pcc_gc_backend4_store_buffer = NULL;
    while (n != NULL) {
        PccGcStoreBufferNode *next = n->next;
        if (pcc_gc_is_known_object(n->owner)) {
            py_header_flags_and(py_header(n->owner), ~PY_FLAG_GC_REMEMBERED);
        }
        py_decref(n->value);
        free(n);
        n = next;
    }
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_entries_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_high_water_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_owner_fanout_high_water_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_owner_count_high_water_count,
        0,
        __ATOMIC_RELAXED
    );
    pcc_gc_backend4_remembered_set_clear();
}

void pcc_gc_thread_unregister_buffers(void) {
    PccGcStoreBufferMediumState *state =
        pcc_gc_backend4_store_buffer_medium_state;
    if (state == NULL) return;
    pcc_gc_graph_lock();
    pcc_gc_backend4_store_buffer_flush_medium_state_locked(state);
    PccGcStoreBufferMediumState **cur =
        &pcc_gc_backend4_store_buffer_medium_states;
    while (*cur != NULL) {
        if (*cur == state) {
            *cur = state->next;
            free(state);
            break;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_backend4_store_buffer_medium_state = NULL;
    pcc_gc_graph_unlock();
}

void pcc_gc_reset_relocation_set(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    PccGcRelocationNode *n = pcc_gc_relocation_set;
    pcc_gc_relocation_set = NULL;
    pcc_gc_backend4_evacuation_page_clear_unlocked();
    while (n != NULL) {
        PccGcRelocationNode *next = n->next;
        if (n->obj != NULL && !PY_IS_TAGGED_INT(n->obj)) {
            py_header_flags_and(
                py_header(n->obj), ~PY_FLAG_GC_RELOCATION_CANDIDATE
            );
        }
        free(n);
        n = next;
    }
    for (
        PccGcObjectNode *obj_node = pcc_gc_objects;
        obj_node != NULL;
        obj_node = obj_node->next
    ) {
        if (obj_node->obj != NULL && !PY_IS_TAGGED_INT(obj_node->obj)) {
            py_header_flags_and(
                py_header(obj_node->obj), ~PY_FLAG_GC_RELOCATION_TARGET
            );
        }
    }
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    pcc_gc_graph_unlock();
}

int64_t pcc_gc_relocation_set_contains(PyObject *o) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t contains = pcc_gc_relocation_set_find(o) != NULL ? 1 : 0;
    pcc_gc_graph_unlock();
    return contains;
}

int64_t pcc_gc_relocation_set_size(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t size = 0;
    for (
        PccGcRelocationNode *n = pcc_gc_relocation_set;
        n != NULL;
        n = n->next
    ) {
        size++;
    }
    pcc_gc_graph_unlock();
    return size;
}

static int64_t pcc_gc_backend4_forwarding_entries_unlocked(void) {
    int64_t count = 0;
    for (
        PccGcForwardNode *n = pcc_gc_forwardings;
        n != NULL;
        n = n->next
    ) {
        count++;
    }
    return count;
}

static int64_t pcc_gc_backend4_stable_id_entries_unlocked(void) {
    int64_t count = 0;
    for (
        PccGcIdentityNode *n = pcc_gc_identities;
        n != NULL;
        n = n->next
    ) {
        count++;
    }
    return count;
}

int64_t pcc_gc_backend4_verify_no_old_addresses(void) {
    pcc_gc_init_config();
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) {
        return 1;
    }
    pcc_gc_graph_lock();
    for (
        PccGcForwardNode *n = pcc_gc_forwardings;
        n != NULL;
        n = n->next
    ) {
        PyObject *from = n->from;
        PyObject *to = n->to;
        if (from == NULL || to == NULL) {
            pcc_gc_graph_unlock();
            return 0;
        }
        if (from == to) {
            pcc_gc_graph_unlock();
            return 0;
        }
        if ((py_header_flags_load(py_header(to)) & PY_FLAG_GC_OLD) != 0) {
            pcc_gc_graph_unlock();
            return 0;
        }
    }
    pcc_gc_graph_unlock();
    return 1;
}

int64_t pcc_gc_backend4_forwarding_entries(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t count = pcc_gc_backend4_forwarding_entries_unlocked();
    pcc_gc_graph_unlock();
    return count;
}

int64_t pcc_gc_backend4_stable_id_entries(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t count = pcc_gc_backend4_stable_id_entries_unlocked();
    pcc_gc_graph_unlock();
    return count;
}

int64_t pcc_gc_backend4_fragmentation_score(void) {
    return pcc_gc_relocation_set_size()
        + pcc_gc_backend4_forwarding_entries();
}

int64_t pcc_gc_backend4_generation_barrier_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_genzgc_store_barriers,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_generation_promotion_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_young_promotions,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_evacuation_candidate_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_evacuation_candidates,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_evacuated_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_evacuated_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_page_policy_score(void) {
    return pcc_gc_backend4_evacuation_candidate_score()
        + pcc_gc_backend4_evacuated_bytes();
}

int64_t pcc_gc_backend4_page_pressure_score(void) {
    return pcc_gc_backend4_evacuation_candidate_bytes()
        + pcc_gc_backend4_large_object_deferred_bytes();
}

int64_t pcc_gc_backend4_fragmentation_backlog_bytes(void) {
    int64_t candidates = pcc_gc_backend4_evacuation_candidate_bytes();
    int64_t evacuated = pcc_gc_backend4_evacuated_bytes();
    int64_t deferred = pcc_gc_backend4_large_object_deferred_bytes();
    int64_t pending = candidates > evacuated ? candidates - evacuated : 0;
    return pending + deferred;
}

int64_t pcc_gc_backend4_evacuation_efficiency_per_mille(void) {
    int64_t candidates = pcc_gc_backend4_evacuation_candidate_bytes();
    if (candidates <= 0) return 1000;
    int64_t evacuated = pcc_gc_backend4_evacuated_bytes();
    if (evacuated <= 0) return 0;
    if (evacuated >= candidates) return 1000;
    return (evacuated * 1000) / candidates;
}

int64_t pcc_gc_backend4_fragmentation_policy_score(void) {
    return pcc_gc_backend4_fragmentation_backlog_bytes()
        + pcc_gc_backend4_evacuation_incomplete_batches();
}

int64_t pcc_gc_backend4_small_page_limit_bytes(void) {
    return PCC_GC_BACKEND4_SMALL_PAGE_LIMIT;
}

int64_t pcc_gc_backend4_medium_page_limit_bytes(void) {
    return PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT;
}

int64_t pcc_gc_backend4_large_defer_limit_bytes(void) {
    return PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT;
}

int64_t pcc_gc_backend4_large_object_defer_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_large_object_defers,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_large_object_deferred_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_large_object_deferred_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_large_object_reconsiderations(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_large_object_reconsiderations_count,
        __ATOMIC_RELAXED
    );
}

static int64_t pcc_gc_backend4_generation_count(int32_t flag) {
    int64_t count = 0;
    pcc_gc_graph_lock();
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (o == NULL || PY_IS_TAGGED_INT(o)) continue;
        if ((py_header_flags_load(py_header(o)) & flag) != 0) count++;
    }
    pcc_gc_graph_unlock();
    return count;
}

static int64_t pcc_gc_backend4_generation_bytes(int32_t flag) {
    int64_t bytes = 0;
    pcc_gc_graph_lock();
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (o == NULL || PY_IS_TAGGED_INT(o)) continue;
        if ((py_header_flags_load(py_header(o)) & flag) != 0) bytes += n->size;
    }
    pcc_gc_graph_unlock();
    return bytes;
}

int64_t pcc_gc_backend4_young_object_count(void) {
    return pcc_gc_backend4_generation_count(PY_FLAG_GC_YOUNG);
}

int64_t pcc_gc_backend4_old_object_count(void) {
    return pcc_gc_backend4_generation_count(PY_FLAG_GC_OLD);
}

int64_t pcc_gc_backend4_young_bytes(void) {
    return pcc_gc_backend4_generation_bytes(PY_FLAG_GC_YOUNG);
}

int64_t pcc_gc_backend4_old_bytes(void) {
    return pcc_gc_backend4_generation_bytes(PY_FLAG_GC_OLD);
}

static int32_t pcc_gc_backend4_page_class_for_size(int64_t size) {
    if (size <= PCC_GC_BACKEND4_SMALL_PAGE_LIMIT) return 0;
    if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) return 1;
    return 2;
}

static int64_t pcc_gc_backend4_align_alloc_size(int64_t size) {
    if (size <= 0) return 0;
    return (size + 7) & ~(int64_t)7;
}

static int32_t pcc_gc_backend4_generation_for_flags(int32_t flags) {
    return (flags & PY_FLAG_GC_OLD) != 0 ? 2 : 1;
}

static int32_t pcc_gc_backend4_generation_for_owner(PyObject *owner) {
    if (
        owner != NULL
        && !PY_IS_TAGGED_INT(owner)
        && (py_header_flags_load(py_header(owner)) & PY_FLAG_GC_OLD) != 0
    ) {
        return 2;
    }
    return 1;
}

static int64_t pcc_gc_backend4_page_class_population(
    int32_t page_class,
    int32_t count_bytes
) {
    int64_t total = 0;
    pcc_gc_graph_lock();
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (o == NULL || PY_IS_TAGGED_INT(o)) continue;
        if (pcc_gc_backend4_page_class_for_size(n->size) != page_class) {
            continue;
        }
        total += count_bytes != 0 ? n->size : 1;
    }
    pcc_gc_graph_unlock();
    return total;
}

int64_t pcc_gc_backend4_small_page_object_count(void) {
    return pcc_gc_backend4_page_class_population(0, 0);
}

int64_t pcc_gc_backend4_medium_page_object_count(void) {
    return pcc_gc_backend4_page_class_population(1, 0);
}

int64_t pcc_gc_backend4_large_page_object_count(void) {
    return pcc_gc_backend4_page_class_population(2, 0);
}

int64_t pcc_gc_backend4_small_page_live_bytes(void) {
    return pcc_gc_backend4_page_class_population(0, 1);
}

int64_t pcc_gc_backend4_medium_page_live_bytes(void) {
    return pcc_gc_backend4_page_class_population(1, 1);
}

int64_t pcc_gc_backend4_large_page_live_bytes(void) {
    return pcc_gc_backend4_page_class_population(2, 1);
}

static int64_t pcc_gc_backend4_zpage_capacity_for_size(int64_t size) {
    if (size <= PCC_GC_BACKEND4_SMALL_PAGE_LIMIT) {
        return PCC_GC_BACKEND4_SMALL_PAGE_LIMIT;
    }
    if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
        return PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT;
    }
    int64_t unit = PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT;
    int64_t pages = (size + unit - 1) / unit;
    if (pages < 1) pages = 1;
    return pages * unit;
}

static PccGcZPage *pcc_gc_backend4_zpage_find_reusable_page_unlocked(
    PyObject *owner,
    int64_t size
) {
    if (size <= 0 || size > PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
        return NULL;
    }
    int32_t page_class = pcc_gc_backend4_page_class_for_size(size);
    int32_t generation = pcc_gc_backend4_generation_for_owner(owner);
    int64_t alloc_size = pcc_gc_backend4_align_alloc_size(size);
    for (
        PccGcZPage *page = pcc_gc_backend4_pages;
        page != NULL;
        page = page->next
    ) {
        if (pcc_gc_backend4_evacuation_page_find_unlocked(page) != NULL) {
            continue;
        }
        if (page->page_class != page_class) continue;
        if (page->generation != generation) continue;
        if (page->capacity_bytes - page->allocated_bytes >= alloc_size) {
            return page;
        }
    }
    return NULL;
}

static PccGcZPage *pcc_gc_backend4_zpage_find_reusable_page_for_gen_unlocked(
    int64_t size,
    int32_t generation
) {
    if (size <= 0 || size > PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
        return NULL;
    }
    int32_t page_class = pcc_gc_backend4_page_class_for_size(size);
    int64_t alloc_size = pcc_gc_backend4_align_alloc_size(size);
    for (
        PccGcZPage *page = pcc_gc_backend4_pages;
        page != NULL;
        page = page->next
    ) {
        if (pcc_gc_backend4_evacuation_page_find_unlocked(page) != NULL) {
            continue;
        }
        if (page->page_class != page_class) continue;
        if (page->generation != generation) continue;
        if (page->capacity_bytes - page->allocated_bytes >= alloc_size) {
            return page;
        }
    }
    return NULL;
}

static PccGcZPage *pcc_gc_backend4_zpage_pop_free_page_unlocked(
    int64_t size
) {
    if (size <= 0 || size > PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
        return NULL;
    }
    int32_t page_class = pcc_gc_backend4_page_class_for_size(size);
    int64_t capacity = pcc_gc_backend4_zpage_capacity_for_size(size);
    PccGcZPage **cur = &pcc_gc_backend4_free_pages;
    while (*cur != NULL) {
        PccGcZPage *page = *cur;
        if (
            page->page_class == page_class
            && page->capacity_bytes == capacity
        ) {
            *cur = page->next;
            page->next = NULL;
            return page;
        }
        cur = &(*cur)->next;
    }
    return NULL;
}

static void pcc_gc_backend4_zpage_reset_unlocked(
    PccGcZPage *page,
    PyObject *owner,
    int64_t size
) {
    if (page == NULL) return;
    page->primary_owner = owner;
    page->used_bytes = 0;
    page->allocated_bytes = 0;
    page->capacity_bytes = pcc_gc_backend4_zpage_capacity_for_size(size);
    page->page_class = pcc_gc_backend4_page_class_for_size(size);
    page->generation = pcc_gc_backend4_generation_for_owner(owner);
    page->object_count = 0;
    page->remembered_slots = 0;
    page->remembered_cards = 0;
    page->remembered_card_bitmap = 0;
    page->pending_alloc_count = 0;
    memset(
        page->remembered_card_refcounts,
        0,
        sizeof(page->remembered_card_refcounts)
    );
    if (
        page->span_base == NULL
        || page->span_capacity_bytes < page->capacity_bytes
    ) {
        free(page->span_base);
        page->span_base = (uint8_t *)calloc(
            1, (size_t)page->capacity_bytes
        );
        page->span_capacity_bytes =
            page->span_base == NULL ? 0 : page->capacity_bytes;
    } else if (page->capacity_bytes > 0) {
        memset(page->span_base, 0, (size_t)page->capacity_bytes);
    }
}

static PccGcZPage *pcc_gc_backend4_zpage_find_page_for_addr_unlocked(
    void *ptr,
    int64_t size,
    int64_t *offset_out
) {
    if (ptr == NULL || size <= 0) return NULL;
    uintptr_t addr = (uintptr_t)ptr;
    int64_t alloc_size = pcc_gc_backend4_align_alloc_size(size);
    for (
        PccGcZPage *page = pcc_gc_backend4_pages;
        page != NULL;
        page = page->next
    ) {
        if (page->span_base == NULL || page->span_capacity_bytes <= 0) {
            continue;
        }
        uintptr_t base = (uintptr_t)page->span_base;
        uintptr_t span = (uintptr_t)page->span_capacity_bytes;
        if (addr >= base && addr - base + (uintptr_t)alloc_size <= span) {
            if (offset_out != NULL) *offset_out = (int64_t)(addr - base);
            return page;
        }
    }
    return NULL;
}

static int32_t pcc_gc_backend4_zpage_list_owns_addr_unlocked(
    PccGcZPage *head,
    void *ptr
) {
    if (ptr == NULL) return 0;
    uintptr_t addr = (uintptr_t)ptr;
    for (PccGcZPage *page = head; page != NULL; page = page->next) {
        if (page->span_base == NULL || page->span_capacity_bytes <= 0) {
            continue;
        }
        uintptr_t base = (uintptr_t)page->span_base;
        uintptr_t span = (uintptr_t)page->span_capacity_bytes;
        if (addr >= base && addr < base + span) return 1;
    }
    return 0;
}

static int32_t pcc_gc_backend4_zpage_owns_addr_unlocked(void *ptr) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (pcc_gc_backend4_zpage_list_owns_addr_unlocked(
            pcc_gc_backend4_pages,
            ptr
        )) {
        return 1;
    }
    return pcc_gc_backend4_zpage_list_owns_addr_unlocked(
        pcc_gc_backend4_free_pages,
        ptr
    );
}

void *pcc_gc_backend4_try_zpage_alloc(int64_t size, int32_t flags) {
    pcc_gc_init_config();
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) {
        return NULL;
    }
    if (size < (int64_t)sizeof(PyObjectHeader)) return NULL;
    int64_t alloc_size = pcc_gc_backend4_align_alloc_size(size);
    if (alloc_size <= 0) return NULL;
    int32_t generation = pcc_gc_backend4_generation_for_flags(flags);
    pcc_gc_graph_lock();
    int32_t page_needs_reset = 0;
    PccGcZPage *page =
        pcc_gc_backend4_zpage_find_reusable_page_for_gen_unlocked(
            size,
            generation
        );
    if (page == NULL) {
        page = pcc_gc_backend4_zpage_pop_free_page_unlocked(size);
        if (page != NULL) page_needs_reset = 1;
    }
    if (page == NULL) {
        page = (PccGcZPage *)calloc(1, sizeof(PccGcZPage));
        if (page == NULL) {
            pcc_gc_graph_unlock();
            return NULL;
        }
        page_needs_reset = 1;
    }
    if (page_needs_reset != 0) {
        pcc_gc_backend4_zpage_reset_unlocked(page, NULL, size);
        page->generation = generation;
        page->next = pcc_gc_backend4_pages;
        pcc_gc_backend4_pages = page;
    }
    if (
        page->span_base == NULL
        || page->span_capacity_bytes < page->capacity_bytes
        || page->allocated_bytes < 0
        || page->capacity_bytes - page->allocated_bytes < alloc_size
    ) {
        pcc_gc_graph_unlock();
        return NULL;
    }
    uint8_t *ptr = page->span_base + page->allocated_bytes;
    memset(ptr, 0, (size_t)alloc_size);
    page->allocated_bytes += alloc_size;
    page->pending_alloc_count++;
    pcc_gc_graph_unlock();
    return ptr;
}

static int64_t pcc_gc_backend4_free_page_count_for_class_unlocked(
    int32_t page_class
) {
    int64_t count = 0;
    for (
        PccGcZPage *page = pcc_gc_backend4_free_pages;
        page != NULL;
        page = page->next
    ) {
        if (page->page_class == page_class) count++;
    }
    return count;
}

static int64_t pcc_gc_backend4_free_page_limit_for_class(int32_t page_class) {
    if (page_class == 0) return PCC_GC_BACKEND4_FREE_SMALL_PAGE_LIMIT;
    if (page_class == 1) return PCC_GC_BACKEND4_FREE_MEDIUM_PAGE_LIMIT;
    return 0;
}

static void pcc_gc_backend4_zpage_destroy_unlocked(PccGcZPage *page) {
    if (page == NULL) return;
    free(page->span_base);
    free(page);
}

static void pcc_gc_backend4_zpage_recycle_unlocked(PccGcZPage *page) {
    if (page == NULL) return;
    if (page->page_class > 1) {
        pcc_gc_backend4_zpage_destroy_unlocked(page);
        return;
    }
    int64_t limit = pcc_gc_backend4_free_page_limit_for_class(
        page->page_class
    );
    if (
        limit <= 0
        || pcc_gc_backend4_free_page_count_for_class_unlocked(
            page->page_class
        ) >= limit
    ) {
        pcc_gc_backend4_zpage_destroy_unlocked(page);
        return;
    }
    page->primary_owner = NULL;
    page->used_bytes = 0;
    page->allocated_bytes = 0;
    page->object_count = 0;
    page->pending_alloc_count = 0;
    page->remembered_slots = 0;
    page->remembered_cards = 0;
    page->remembered_card_bitmap = 0;
    memset(
        page->remembered_card_refcounts,
        0,
        sizeof(page->remembered_card_refcounts)
    );
    page->next = pcc_gc_backend4_free_pages;
    pcc_gc_backend4_free_pages = page;
}

static void pcc_gc_backend4_zpage_track_alloc_unlocked(
    PyObject *owner,
    int64_t size
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return;
    PccGcZPageNode *page = (PccGcZPageNode *)calloc(
        1,
        sizeof(PccGcZPageNode)
    );
    if (page == NULL) return;
    PccGcZPage *zpage = pcc_gc_backend4_zpage_find_reusable_page_unlocked(
        owner,
        size
    );
    int64_t existing_offset = -1;
    if ((py_header_flags_load(py_header(owner)) & PY_FLAG_GC_ZPAGE_ALLOC) != 0) {
        zpage = pcc_gc_backend4_zpage_find_page_for_addr_unlocked(
            owner,
            size,
            &existing_offset
        );
    }
    if (zpage == NULL) {
        zpage = pcc_gc_backend4_zpage_pop_free_page_unlocked(size);
    }
    if (zpage == NULL) {
        zpage = (PccGcZPage *)calloc(1, sizeof(PccGcZPage));
        if (zpage == NULL) {
            free(page);
            return;
        }
    }
    if (existing_offset < 0 && zpage->object_count <= 0) {
        pcc_gc_backend4_zpage_reset_unlocked(zpage, owner, size);
        zpage->next = pcc_gc_backend4_pages;
        pcc_gc_backend4_pages = zpage;
    }
    page->owner = owner;
    page->page = zpage;
    page->offset_bytes =
        existing_offset >= 0 ? existing_offset : zpage->allocated_bytes;
    page->size_bytes = size;
    if (existing_offset >= 0 && zpage->pending_alloc_count > 0) {
        zpage->pending_alloc_count--;
    }
    if (existing_offset < 0) {
        zpage->allocated_bytes += pcc_gc_backend4_align_alloc_size(size);
    }
    zpage->used_bytes += size;
    zpage->object_count++;
    if (zpage->primary_owner == NULL) zpage->primary_owner = owner;
    page->next = pcc_gc_backend4_zpages;
    pcc_gc_backend4_zpages = page;
}

static void pcc_gc_backend4_zpage_unlink_page_unlocked(PccGcZPage *page) {
    if (page == NULL) return;
    PccGcZPage **cur = &pcc_gc_backend4_pages;
    while (*cur != NULL) {
        if (*cur == page) {
            *cur = page->next;
            return;
        }
        cur = &(*cur)->next;
    }
}

static PyObject *pcc_gc_backend4_zpage_find_owner_for_page_unlocked(
    PccGcZPage *page
) {
    if (page == NULL) return NULL;
    for (
        PccGcZPageNode *n = pcc_gc_backend4_zpages;
        n != NULL;
        n = n->next
    ) {
        if (n->page == page) return n->owner;
    }
    return NULL;
}

static void pcc_gc_backend4_zpage_remove_payload_spans_unlocked(
    PyObject *owner
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    PccGcZPagePayloadSpanNode **cur = &pcc_gc_backend4_zpage_payload_spans;
    while (*cur != NULL) {
        PccGcZPagePayloadSpanNode *node = *cur;
        if (node->owner == owner) {
            *cur = node->next;
            if (node->page != NULL && node->size_bytes > 0) {
                if (node->page->used_bytes >= node->size_bytes) {
                    node->page->used_bytes -= node->size_bytes;
                } else {
                    node->page->used_bytes = 0;
                }
            }
            free(node);
            continue;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_backend4_zpage_remove_unlocked(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return;
    PccGcZPageNode **cur = &pcc_gc_backend4_zpages;
    while (*cur != NULL) {
        if ((*cur)->owner == owner) {
            PccGcZPageNode *dead = *cur;
            *cur = dead->next;
            PccGcZPage *page = dead->page;
            if (page != NULL) {
                pcc_gc_backend4_zpage_remove_payload_spans_unlocked(owner);
                int64_t size = pcc_gc_known_object_size_unlocked(owner);
                if (size > 0 && page->used_bytes >= size) {
                    page->used_bytes -= size;
                } else if (size > 0) {
                    page->used_bytes = 0;
                }
                if (page->object_count > 0) page->object_count--;
                if (page->primary_owner == owner) {
                    page->primary_owner =
                        pcc_gc_backend4_zpage_find_owner_for_page_unlocked(page);
                }
                if (
                    page->object_count <= 0
                    && page->pending_alloc_count <= 0
                ) {
                    pcc_gc_backend4_zpage_unlink_page_unlocked(page);
                    pcc_gc_backend4_zpage_recycle_unlocked(page);
                }
            }
            free(dead);
            continue;
        }
        cur = &(*cur)->next;
    }
}

static PccGcZPageNode *pcc_gc_backend4_zpage_find_unlocked(PyObject *owner) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return NULL;
    for (
        PccGcZPageNode *n = pcc_gc_backend4_zpages;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner) return n;
    }
    return NULL;
}

static void pcc_gc_backend4_zpage_note_owner_promoted_unlocked(
    PyObject *owner
) {
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    if (node != NULL && node->page != NULL) {
        node->page->generation = 2;
    }
}

static int32_t pcc_gc_backend4_zpage_card_for_node_unlocked(
    PccGcZPageNode *node
) {
    if (node == NULL || node->page == NULL) return -1;
    if (node->offset_bytes < 0) return -1;
    int64_t card =
        (node->offset_bytes / PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES)
        % PCC_GC_BACKEND4_ZPAGE_CARD_COUNT;
    if (card < 0 || card >= PCC_GC_BACKEND4_ZPAGE_CARD_COUNT) return -1;
    return (int32_t)card;
}

static int64_t pcc_gc_backend4_zpage_payload_offset_for_slot_unlocked(
    PyObject *owner,
    PyObject **slot
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner) || slot == NULL) return -1;
    uintptr_t slot_addr = (uintptr_t)slot;
    for (
        PccGcZPagePayloadSpanNode *span =
            pcc_gc_backend4_zpage_payload_spans;
        span != NULL;
        span = span->next
    ) {
        if (span->owner != owner || span->base == NULL) continue;
        if (span->size_bytes <= 0 || span->offset_bytes < 0) continue;
        uintptr_t base = (uintptr_t)span->base;
        uintptr_t size = (uintptr_t)span->size_bytes;
        if (slot_addr >= base && slot_addr - base < size) {
            return span->offset_bytes + (int64_t)(slot_addr - base);
        }
    }
    return -1;
}

static int32_t pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
    PccGcZPageNode *node,
    PyObject **slot
) {
    if (node == NULL || node->page == NULL) return -1;
    if (node->offset_bytes < 0) return -1;
    int64_t span_offset = node->offset_bytes;
    if (slot != NULL && node->owner != NULL && node->size_bytes > 0) {
        uintptr_t base = (uintptr_t)node->owner;
        uintptr_t slot_addr = (uintptr_t)slot;
        uintptr_t size = (uintptr_t)node->size_bytes;
        if (slot_addr >= base && slot_addr - base < size) {
            span_offset += (int64_t)(slot_addr - base);
        } else {
            int64_t payload_offset =
                pcc_gc_backend4_zpage_payload_offset_for_slot_unlocked(
                    node->owner,
                    slot
                );
            if (payload_offset >= 0) span_offset = payload_offset;
        }
    }
    int64_t card =
        (span_offset / PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES)
        % PCC_GC_BACKEND4_ZPAGE_CARD_COUNT;
    if (card < 0 || card >= PCC_GC_BACKEND4_ZPAGE_CARD_COUNT) return -1;
    return (int32_t)card;
}

static void pcc_gc_backend4_zpage_note_remembered_slot_unlocked(
    PyObject *owner,
    int64_t delta
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    if (delta == 0) return;
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    if (node == NULL || node->page == NULL) return;
    PccGcZPage *page = node->page;
    int64_t next = page->remembered_slots + delta;
    if (next < 0) next = 0;
    page->remembered_slots = next;
}

static void pcc_gc_backend4_zpage_note_remembered_card_unlocked(
    PyObject *owner,
    PyObject **slot,
    int64_t delta
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return;
    if (slot == NULL) return;
    if (delta == 0) return;
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    if (node == NULL || node->page == NULL) return;
    PccGcZPage *page = node->page;
    int32_t card = pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
        node,
        slot
    );
    if (card < 0) return;
    uint64_t mask = (uint64_t)1 << (uint64_t)card;
    if (delta > 0) {
        if (page->remembered_card_refcounts[card] == 0) {
            page->remembered_card_bitmap |= mask;
            page->remembered_cards++;
        }
        if (page->remembered_card_refcounts[card] < UINT16_MAX) {
            page->remembered_card_refcounts[card]++;
        }
        return;
    }
    if (page->remembered_card_refcounts[card] == 0) return;
    page->remembered_card_refcounts[card]--;
    if (page->remembered_card_refcounts[card] == 0) {
        page->remembered_card_bitmap &= ~mask;
        if (page->remembered_cards > 0) page->remembered_cards--;
    }
}

static int32_t pcc_gc_backend4_zpage_card_for_slot_unlocked(
    PyObject **slot
) {
    if (slot == NULL) return -1;
    int32_t slot_bit = pcc_gc_backend4_remembered_page_slot_bit(slot);
    if (
        slot_bit < 0
        || slot_bit >= PCC_GC_BACKEND4_REMEMBERED_PAGE_SLOT_BITS
    ) return -1;
    int32_t card = slot_bit / PCC_GC_BACKEND4_ZPAGE_CARD_SLOT_BITS;
    if (card < 0 || card >= PCC_GC_BACKEND4_ZPAGE_CARD_COUNT) return -1;
    return card;
}

static int pcc_gc_backend4_zpage_contains_remembered_card_unlocked(
    PyObject *owner,
    PyObject **slot
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    if (node == NULL || node->page == NULL) return 0;
    if (slot == NULL) return 0;
    int32_t card = pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
        node,
        slot
    );
    if (card < 0) return 0;
    uint64_t mask = (uint64_t)1 << (uint64_t)card;
    return (node->page->remembered_card_bitmap & mask) != 0;
}

static int64_t pcc_gc_backend4_owner_remembered_slots_unlocked(
    PyObject *owner
) {
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return 0;
    int64_t total = 0;
    for (
        PccGcRememberedSlotNode *n = pcc_gc_backend4_remembered_slots;
        n != NULL;
        n = n->next
    ) {
        if (n->owner == owner) total++;
    }
    return total;
}

static void pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
    PyObject *from_owner,
    PyObject *to_owner,
    PyObject **from_slot,
    PyObject **to_slot
) {
    if (from_owner == NULL || to_owner == NULL) return;
    if (PY_IS_TAGGED_INT(from_owner) || PY_IS_TAGGED_INT(to_owner)) return;
    if (from_slot == NULL || to_slot == NULL) return;
    if (from_owner == to_owner && from_slot == to_slot) return;
    for (
        PccGcRememberedSlotNode *n = pcc_gc_backend4_remembered_slots;
        n != NULL;
        n = n->next
    ) {
        if (n->owner != from_owner || n->slot != from_slot) continue;
        pcc_gc_backend4_remembered_page_remove_slot(n->slot);
        pcc_gc_backend4_zpage_note_remembered_slot_unlocked(n->owner, -1);
        pcc_gc_backend4_zpage_note_remembered_card_unlocked(
            n->owner, n->slot, -1
        );
        n->owner = to_owner;
        n->slot = to_slot;
        pcc_gc_backend4_remembered_page_add(n->slot);
        pcc_gc_backend4_zpage_note_remembered_slot_unlocked(n->owner, 1);
        pcc_gc_backend4_zpage_note_remembered_card_unlocked(
            n->owner, n->slot, 1
        );
    }
}

static void pcc_gc_retarget_continuation_root_slots_unlocked(
    PyObject **from_slots,
    const int32_t *from_frame_map,
    PyObject **to_slots,
    const int32_t *to_frame_map
) {
    if (from_slots == NULL || to_slots == NULL || to_frame_map == NULL) return;
    for (
        PccGcContinuationRootNode *n = pcc_gc_continuation_roots;
        n != NULL;
        n = n->next
    ) {
        if (n->slots != from_slots) continue;
        if (from_frame_map != NULL && n->frame_map != from_frame_map) continue;
        n->slots = to_slots;
        n->frame_map = to_frame_map;
    }
}

static int64_t pcc_gc_backend4_zpage_population(int32_t metric) {
    int64_t total = 0;
    pcc_gc_graph_lock();
    for (
        PccGcZPage *page = pcc_gc_backend4_pages;
        page != NULL;
        page = page->next
    ) {
        if (metric == 0) {
            total++;
        } else if (metric == 1) {
            total += page->capacity_bytes;
        } else if (metric == 2) {
            if (page->capacity_bytes > page->used_bytes) {
                total += page->capacity_bytes - page->used_bytes;
            }
        } else if (metric == 3) {
            if (page->page_class == 2) total++;
        } else if (metric == 4) {
            total += page->remembered_slots;
        } else if (metric == 5) {
            if (page->remembered_cards > 0) total++;
        } else if (metric == 6) {
            if (page->capacity_bytes > page->used_bytes) total++;
        } else if (metric == 7) {
            if (page->generation == 1) total++;
        } else if (metric == 8) {
            if (page->generation == 2) total++;
        } else if (metric == 9) {
            total += page->remembered_cards;
        } else if (metric == 10) {
            total += page->allocated_bytes;
        } else if (metric == 11) {
            if (page->allocated_bytes > page->used_bytes) {
                total += page->allocated_bytes - page->used_bytes;
            }
        } else if (metric == 12) {
            total += page->span_capacity_bytes;
        }
    }
    pcc_gc_graph_unlock();
    return total;
}

int64_t pcc_gc_backend4_zpage_count(void) {
    return pcc_gc_backend4_zpage_population(0);
}

int64_t pcc_gc_backend4_zpage_capacity_bytes(void) {
    return pcc_gc_backend4_zpage_population(1);
}

int64_t pcc_gc_backend4_zpage_fragmentation_bytes(void) {
    return pcc_gc_backend4_zpage_population(2);
}

int64_t pcc_gc_backend4_zpage_large_pages(void) {
    return pcc_gc_backend4_zpage_population(3);
}

int64_t pcc_gc_backend4_zpage_remembered_slots(void) {
    return pcc_gc_backend4_zpage_population(4);
}

int64_t pcc_gc_backend4_zpage_remembered_cards(void) {
    return pcc_gc_backend4_zpage_population(9);
}

int64_t pcc_gc_backend4_zpage_remembered_card_ratio_per_mille(void) {
    /* Read-only density telemetry: keep selector policy on absolute
     * slot/card/page pressure until the production matrix has enough data to
     * tune a density threshold. */
    int64_t pages = pcc_gc_backend4_zpage_count();
    if (pages <= 0) return 0;
    int64_t capacity = pages * PCC_GC_BACKEND4_ZPAGE_CARD_COUNT;
    if (capacity <= 0) return 0;
    int64_t cards = pcc_gc_backend4_zpage_remembered_cards();
    if (cards <= 0) return 0;
    if (cards >= capacity) return 1000;
    return (cards * 1000) / capacity;
}

int64_t pcc_gc_backend4_zpage_dirty_pages(void) {
    return pcc_gc_backend4_zpage_population(5);
}

int64_t pcc_gc_backend4_zpage_fragmented_pages(void) {
    return pcc_gc_backend4_zpage_population(6);
}

int64_t pcc_gc_backend4_zpage_young_pages(void) {
    return pcc_gc_backend4_zpage_population(7);
}

int64_t pcc_gc_backend4_zpage_old_pages(void) {
    return pcc_gc_backend4_zpage_population(8);
}

static int64_t pcc_gc_backend4_zpage_free_population(int32_t metric) {
    int64_t total = 0;
    pcc_gc_graph_lock();
    for (
        PccGcZPage *page = pcc_gc_backend4_free_pages;
        page != NULL;
        page = page->next
    ) {
        if (metric == 0) {
            total++;
        } else if (metric == 1) {
            total += page->capacity_bytes;
        } else if (metric == 2) {
            total += page->span_capacity_bytes;
        }
    }
    pcc_gc_graph_unlock();
    return total;
}

int64_t pcc_gc_backend4_zpage_free_pages(void) {
    return pcc_gc_backend4_zpage_free_population(0);
}

int64_t pcc_gc_backend4_zpage_free_capacity_bytes(void) {
    return pcc_gc_backend4_zpage_free_population(1);
}

int64_t pcc_gc_backend4_zpage_free_span_bytes(void) {
    return pcc_gc_backend4_zpage_free_population(2);
}

int64_t pcc_gc_backend4_zpage_used_bytes(void) {
    int64_t capacity = pcc_gc_backend4_zpage_capacity_bytes();
    int64_t fragmentation = pcc_gc_backend4_zpage_fragmentation_bytes();
    if (capacity <= fragmentation) return 0;
    return capacity - fragmentation;
}

int64_t pcc_gc_backend4_zpage_allocated_bytes(void) {
    return pcc_gc_backend4_zpage_population(10);
}

int64_t pcc_gc_backend4_zpage_reclaimable_gap_bytes(void) {
    return pcc_gc_backend4_zpage_population(11);
}

int64_t pcc_gc_backend4_zpage_span_bytes(void) {
    return pcc_gc_backend4_zpage_population(12);
}

int64_t pcc_gc_backend4_zpage_owner_offset_bytes(PyObject *owner) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return -1;
    pcc_gc_graph_lock();
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    int64_t out = -1;
    if (node != NULL) out = node->offset_bytes;
    pcc_gc_graph_unlock();
    return out;
}

int64_t pcc_gc_backend4_zpage_owner_size_bytes(PyObject *owner) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return -1;
    pcc_gc_graph_lock();
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    int64_t out = -1;
    if (node != NULL) out = node->size_bytes;
    pcc_gc_graph_unlock();
    return out;
}

int64_t pcc_gc_backend4_zpage_owner_span_card(PyObject *owner) {
    int64_t offset = pcc_gc_backend4_zpage_owner_offset_bytes(owner);
    if (offset < 0) return -1;
    return (offset / PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES)
        % PCC_GC_BACKEND4_ZPAGE_CARD_COUNT;
}

int64_t pcc_gc_backend4_zpage_owner_slot_span_card(
    PyObject *owner,
    PyObject **slot
) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner) || slot == NULL) return -1;
    pcc_gc_graph_lock();
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    int32_t card = pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
        node,
        slot
    );
    pcc_gc_graph_unlock();
    return card;
}

int64_t pcc_gc_backend4_zpage_register_owner_payload_span(
    PyObject *owner,
    void *base,
    int64_t size_bytes
) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner)) return -1;
    if (base == NULL || size_bytes <= 0) return -1;
    pcc_gc_graph_lock();
    PccGcZPageNode *node = pcc_gc_backend4_zpage_find_unlocked(owner);
    if (node == NULL || node->page == NULL) {
        pcc_gc_graph_unlock();
        return -1;
    }
    PccGcZPage *page = node->page;
    pcc_gc_backend4_zpage_remove_payload_spans_unlocked(owner);
    if (page->allocated_bytes > page->capacity_bytes) {
        pcc_gc_graph_unlock();
        return -1;
    }
    int64_t available = page->capacity_bytes - page->allocated_bytes;
    if (size_bytes > available) {
        pcc_gc_graph_unlock();
        return -1;
    }
    PccGcZPagePayloadSpanNode *span =
        (PccGcZPagePayloadSpanNode *)calloc(1, sizeof(PccGcZPagePayloadSpanNode));
    if (span == NULL) {
        pcc_gc_graph_unlock();
        return -1;
    }
    span->owner = owner;
    span->base = (uint8_t *)base;
    span->size_bytes = size_bytes;
    span->offset_bytes = page->allocated_bytes;
    span->page = page;
    page->allocated_bytes += size_bytes;
    page->used_bytes += size_bytes;
    span->next = pcc_gc_backend4_zpage_payload_spans;
    pcc_gc_backend4_zpage_payload_spans = span;
    int64_t offset = span->offset_bytes;
    pcc_gc_graph_unlock();
    return offset;
}

int64_t pcc_gc_backend4_zpage_fragmentation_per_mille(void) {
    int64_t capacity = pcc_gc_backend4_zpage_capacity_bytes();
    if (capacity <= 0) return 0;
    int64_t fragmentation = pcc_gc_backend4_zpage_fragmentation_bytes();
    if (fragmentation <= 0) return 0;
    if (fragmentation >= capacity) return 1000;
    return (fragmentation * 1000) / capacity;
}

int64_t pcc_gc_backend4_zpage_policy_score(void) {
    return pcc_gc_backend4_zpage_fragmentation_bytes()
        + pcc_gc_backend4_fragmentation_backlog_bytes()
        + pcc_gc_backend4_evacuation_incomplete_batches()
        + pcc_gc_backend4_zpage_remembered_slots()
        + pcc_gc_backend4_zpage_remembered_cards()
        + pcc_gc_backend4_zpage_dirty_pages()
        + pcc_gc_backend4_zpage_fragmented_pages()
        + pcc_gc_backend4_zpage_old_pages();
}

int64_t pcc_gc_backend4_small_page_candidate_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_small_page_candidates,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_medium_page_candidate_score(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_medium_page_candidates,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_evacuation_candidate_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_evacuation_candidate_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_small_page_candidate_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_small_page_candidate_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_medium_page_candidate_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_medium_page_candidate_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_evacuation_candidate_zpage_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_small_page_candidate_zpage_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_small_page_candidate_zpage_bytes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_medium_page_candidate_zpage_bytes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_medium_page_candidate_zpage_bytes_count,
        __ATOMIC_RELAXED
    );
}

static int64_t pcc_gc_backend4_evacuation_page_population(int32_t metric) {
    int64_t total = 0;
    pcc_gc_graph_lock();
    for (
        PccGcZPageEvacuationNode *n = pcc_gc_backend4_evacuation_pages;
        n != NULL;
        n = n->next
    ) {
        PccGcZPage *page = n->page;
        if (page == NULL) continue;
        if (metric == 0) {
            total++;
        } else if (metric == 1) {
            total += page->used_bytes;
        } else if (metric == 2) {
            total += page->remembered_cards;
        }
    }
    pcc_gc_graph_unlock();
    return total;
}

int64_t pcc_gc_backend4_evacuation_page_candidate_score(void) {
    return pcc_gc_backend4_evacuation_page_population(0);
}

int64_t pcc_gc_backend4_evacuation_page_candidate_bytes(void) {
    return pcc_gc_backend4_evacuation_page_population(1);
}

int64_t pcc_gc_backend4_evacuation_page_dirty_cards(void) {
    return pcc_gc_backend4_evacuation_page_population(2);
}

int64_t pcc_gc_backend4_store_buffer_drain_batches(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_drain_batches_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_drained_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_drained_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_duplicate_skips(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_duplicate_skips_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_high_water(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_high_water_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_owner_fanout_high_water(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_owner_fanout_high_water_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_owner_count_high_water(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_owner_count_high_water_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_incomplete_drains(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_incomplete_drains_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_evacuation_incomplete_batches(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_evacuation_incomplete_batches_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_batch_capacity(void) {
    return PCC_GC_BACKEND4_STORE_BUFFER_BATCH_CAPACITY;
}

int64_t pcc_gc_backend4_store_buffer_max_batch_size(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_max_batch_size_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_full_batches(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_full_batches_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_medium_capacity(void) {
    return PCC_GC_BACKEND4_STORE_BUFFER_MEDIUM_CAPACITY;
}

int64_t pcc_gc_backend4_store_buffer_medium_pending(void) {
    int64_t count = 0;
    pcc_gc_graph_lock();
    for (
        PccGcStoreBufferMediumState *state =
            pcc_gc_backend4_store_buffer_medium_states;
        state != NULL;
        state = state->next
    ) {
        count += state->count == NULL ? 0 : *state->count;
    }
    pcc_gc_graph_unlock();
    return count;
}

int64_t pcc_gc_backend4_store_buffer_medium_flushes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_medium_flushes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_medium_flushed_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_medium_flushed_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_medium_full_flushes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_medium_full_flushes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_cross_thread_medium_flushes(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_set_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_set_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_set_duplicate_skips(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_set_duplicate_skips_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_set_high_water(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_set_high_water_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_page_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_page_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_page_slot_entries(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_page_slot_entries_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_page_high_water(void) {
    return __atomic_load_n(
        &pcc_gc_backend4_remembered_page_high_water_count,
        __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_backend4_remembered_page_contains_slot(PyObject **slot) {
    pcc_gc_init_config();
    if (slot == NULL) return 0;
    pcc_gc_graph_lock();
    int present = pcc_gc_backend4_remembered_page_contains_slot_unlocked(slot);
    pcc_gc_graph_unlock();
    return present;
}

int64_t pcc_gc_backend4_remembered_page_clear_slot(PyObject **slot) {
    pcc_gc_init_config();
    if (slot == NULL) return 0;
    pcc_gc_graph_lock();
    int removed = pcc_gc_backend4_remembered_set_remove_slot(slot);
    pcc_gc_graph_unlock();
    return removed;
}

int64_t pcc_gc_backend4_zpage_contains_remembered_card(
    PyObject *owner,
    PyObject **slot
) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner) || slot == NULL) return 0;
    pcc_gc_graph_lock();
    int present = pcc_gc_backend4_zpage_contains_remembered_card_unlocked(
        owner,
        slot
    );
    pcc_gc_graph_unlock();
    return present;
}

int64_t pcc_gc_backend4_zpage_clear_remembered_card(
    PyObject *owner,
    PyObject **slot
) {
    pcc_gc_init_config();
    if (owner == NULL || PY_IS_TAGGED_INT(owner) || slot == NULL) return 0;
    pcc_gc_graph_lock();
    PccGcZPageNode *owner_node = pcc_gc_backend4_zpage_find_unlocked(owner);
    int32_t card = pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
        owner_node,
        slot
    );
    if (card < 0) {
        pcc_gc_graph_unlock();
        return 0;
    }
    int64_t removed = 0;
    PccGcRememberedSlotNode **cur = &pcc_gc_backend4_remembered_slots;
    while (*cur != NULL) {
        PccGcRememberedSlotNode *node = *cur;
        if (
            node->owner == owner
            && pcc_gc_backend4_zpage_card_for_node_slot_unlocked(
                owner_node,
                node->slot
            ) == card
        ) {
            *cur = node->next;
            pcc_gc_backend4_remembered_page_remove_slot(node->slot);
            pcc_gc_backend4_zpage_note_remembered_slot_unlocked(
                node->owner,
                -1
            );
            pcc_gc_backend4_zpage_note_remembered_card_unlocked(
                node->owner,
                node->slot,
                -1
            );
            int64_t entries = __atomic_load_n(
                &pcc_gc_backend4_remembered_set_entries_count,
                __ATOMIC_RELAXED
            );
            if (entries > 0) {
                __atomic_sub_fetch(
                    &pcc_gc_backend4_remembered_set_entries_count,
                    1,
                    __ATOMIC_RELAXED
                );
            }
            free(node);
            removed++;
            continue;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
    return removed;
}

static int pcc_gc_relocate_copy_supported_tag(int32_t tag) {
    switch (tag) {
        case PY_TYPE_INT:
        case PY_TYPE_FLOAT:
        case PY_TYPE_STR:
        case PY_TYPE_COMPLEX:
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
            return 1;
        default:
            return 0;
    }
}

static int pcc_gc_colored_relocate_copy_supported_tag(int32_t tag) {
    if (tag == PY_TYPE_PROPERTY) return 1;
    if (tag == PY_TYPE_CLASSMETHOD) return 1;
    if (tag == PY_TYPE_STATICMETHOD) return 1;
    if (tag == PY_TYPE_MEMORYVIEW) return 1;
    if (tag == PY_TYPE_FUNC) return 1;
    if (tag == PY_TYPE_ITER) return 1;
    if (tag == PY_TYPE_GEN) return 1;
    if (tag == PY_TYPE_COROUTINE) return 1;
    if (tag == PY_TYPE_CONTINUATION) return 1;
    if (tag == PY_TYPE_EXC) return 1;
    if (tag == PY_TYPE_CLASS) return 1;
    if (tag == PY_TYPE_WEAKREF) return 1;
    if (tag == PY_TYPE_THREAD) return 1;
    if (tag == PY_TYPE_LIST) return 1;
    if (tag == PY_TYPE_TUPLE) return 1;
    if (tag == PY_TYPE_DICT) return 1;
    if (tag == PY_TYPE_SET) return 1;
    if (tag == PY_TYPE_TASK) return 1;
    if (tag == PY_TYPE_VIRTUAL_THREAD) return 1;
    if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) return 1;
    return pcc_gc_relocate_copy_supported_tag(tag);
}

static PyObject *pcc_gc_note_relocation_read_unlocked(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return o;
    PccGcForwardNode *forwarding = pcc_gc_forwarding_find(o);
    py_header_flags_and(py_header(o), ~PY_FLAG_GC_RELOCATION_CANDIDATE);
    PyObject *resolved = o;
    if (forwarding != NULL && forwarding->to != NULL) {
        pcc_gc_relocation_barrier_forwards++;
        resolved = forwarding->to;
    }
    return resolved;
}

static int pcc_gc_relocate_copy_payload(
    PyObject *from,
    PyObject *to,
    int64_t size
) {
    if (from == NULL || to == NULL) return -1;
    int32_t tag = py_header(from)->type_tag;
    if (tag == PY_TYPE_PROPERTY) {
        PyPropertyObject *src = (PyPropertyObject *)from;
        PyPropertyObject *dst = (PyPropertyObject *)to;
        PyObject *fget = src->fget;
        PyObject *fset = src->fset;
        PyObject *fdel = src->fdel;

        dst->fget = NULL;
        dst->fset = NULL;
        dst->fdel = NULL;

        py_incref(fget);
        py_incref(fset);
        py_incref(fdel);
        dst->fget = fget;
        dst->fset = fset;
        dst->fdel = fdel;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->fget, &dst->fget
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->fset, &dst->fset
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->fdel, &dst->fdel
        );
        return 0;
    }
    if (tag == PY_TYPE_CLASSMETHOD) {
        PyClassMethodObject *src = (PyClassMethodObject *)from;
        PyClassMethodObject *dst = (PyClassMethodObject *)to;
        PyObject *func = src->func;

        dst->func = NULL;
        py_incref(func);
        dst->func = func;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->func, &dst->func
        );
        return 0;
    }
    if (tag == PY_TYPE_STATICMETHOD) {
        PyStaticMethodObject *src = (PyStaticMethodObject *)from;
        PyStaticMethodObject *dst = (PyStaticMethodObject *)to;
        PyObject *func = src->func;

        dst->func = NULL;
        py_incref(func);
        dst->func = func;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->func, &dst->func
        );
        return 0;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *src = (PyMemoryViewObject *)from;
        PyMemoryViewObject *dst = (PyMemoryViewObject *)to;
        PyObject *base = src->base;

        dst->base = NULL;
        py_incref(base);
        dst->base = base;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->base, &dst->base
        );
        return 0;
    }
    if (tag == PY_TYPE_FUNC) {
        PyFuncObject *src = (PyFuncObject *)from;
        PyFuncObject *dst = (PyFuncObject *)to;
        PyObject *captures = pcc_gc_load_ptr(from, &src->captures);
        PyObject *self_obj = pcc_gc_load_ptr(from, &src->self_obj);

        dst->entry = src->entry;
        dst->name = src->name;
        dst->captures = NULL;
        dst->self_obj = NULL;

        py_incref(captures);
        dst->captures = captures;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->captures, &dst->captures
        );
        if (self_obj != NULL) {
            py_incref(self_obj);
            dst->self_obj = self_obj;
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from, to, &src->self_obj, &dst->self_obj
            );
        }
        return 0;
    }
    if (tag == PY_TYPE_ITER) {
        PyIterObject *src = (PyIterObject *)from;
        PyIterObject *dst = (PyIterObject *)to;
        PyObject *seq = src->seq;

        dst->seq = NULL;
        dst->index = src->index;

        py_incref(seq);
        dst->seq = seq;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->seq, &dst->seq
        );
        return 0;
    }
    if (tag == PY_TYPE_GEN) {
        PyGenObject *src = (PyGenObject *)from;
        PyGenObject *dst = (PyGenObject *)to;
        PyObject *frame = src->frame;
        PyObject *send_value = src->send_value;

        dst->resume = src->resume;
        dst->frame = NULL;
        dst->state = src->state;
        dst->done = src->done;
        dst->send_value = NULL;

        py_incref(frame);
        py_incref(send_value);
        dst->frame = frame;
        dst->send_value = send_value;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->frame, &dst->frame
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->send_value, &dst->send_value
        );
        return 0;
    }
    if (tag == PY_TYPE_COROUTINE) {
        PccGcCoroutineObject *src = (PccGcCoroutineObject *)from;
        PccGcCoroutineObject *dst = (PccGcCoroutineObject *)to;
        PyObject *captures = src->captures;
        PyObject *args = src->args;
        PyObject *result = src->result;

        dst->name = src->name;
        dst->entry = src->entry;
        dst->captures = NULL;
        dst->args = NULL;
        dst->result = NULL;
        dst->closed = src->closed;
        dst->done = src->done;

        py_incref(captures);
        py_incref(args);
        py_incref(result);
        dst->captures = captures;
        dst->args = args;
        dst->result = result;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->captures, &dst->captures
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->args, &dst->args
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->result, &dst->result
        );
        return 0;
    }
    if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *src = (PyContinuationObject *)from;
        PyContinuationObject *dst = (PyContinuationObject *)to;
        PyContinuationStackChunk *src_chunk = src->stack_chunk;
        PyContinuationStackChunk *dst_chunk = NULL;

        dst->resume_pc = src->resume_pc;
        dst->stack_chunk = NULL;
        dst->mounted = src->mounted;
        dst->resume_abi = src->resume_abi;

        if (src_chunk != NULL) {
            if (src_chunk->slot_count < 0) return -1;
            dst_chunk = (
                PyContinuationStackChunk *
            )calloc(1, sizeof(PyContinuationStackChunk));
            if (dst_chunk == NULL) return -1;
            dst_chunk->root_map_slot_count = src_chunk->root_map_slot_count;
            dst_chunk->slot_count = src_chunk->slot_count;
            if (src_chunk->slot_count > 0) {
                dst_chunk->slots = (
                    PyObject **
                )calloc((size_t)src_chunk->slot_count, sizeof(PyObject *));
                if (dst_chunk->slots == NULL) {
                    free(dst_chunk);
                    return -1;
                }
                for (int64_t i = 0; i < src_chunk->slot_count; i++) {
                    PyObject *value = src_chunk->slots[i];
                    py_incref(value);
                    dst_chunk->slots[i] = value;
                    pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                        from, to, &src_chunk->slots[i], &dst_chunk->slots[i]
                    );
                }
            }
            dst->stack_chunk = dst_chunk;
            if (src->mounted == 0) {
                pcc_gc_retarget_continuation_root_slots_unlocked(
                    src_chunk->slots,
                    &src_chunk->root_map_slot_count,
                    dst_chunk->slots,
                    &dst_chunk->root_map_slot_count
                );
            }
        }
        return 0;
    }
    if (tag == PY_TYPE_EXC) {
        PyExceptionObject *src = (PyExceptionObject *)from;
        PyExceptionObject *dst = (PyExceptionObject *)to;
        PyClassObject *exc_class = src->exc_class;
        PyObject *message = src->message;
        PyObject *cause = src->cause;
        PyObject *context = src->context;
        PyFrameRecord *traceback = src->traceback;
        int32_t n_frames = src->n_frames;
        int32_t cap_frames = src->cap_frames;

        dst->exc_class = NULL;
        dst->message = NULL;
        dst->cause = NULL;
        dst->context = NULL;
        dst->traceback = NULL;
        dst->n_frames = 0;
        dst->cap_frames = 0;

        if (n_frames < 0 || cap_frames < 0 || n_frames > cap_frames) {
            return -1;
        }
        if (cap_frames > 0 && traceback == NULL) return -1;
        if (
            cap_frames > INT64_MAX / (int64_t)sizeof(PyFrameRecord)
        ) return -1;

        if (cap_frames > 0) {
            dst->traceback = (PyFrameRecord *)malloc(
                (size_t)cap_frames * sizeof(PyFrameRecord)
            );
            if (dst->traceback == NULL) return -1;
            memcpy(
                dst->traceback,
                traceback,
                (size_t)cap_frames * sizeof(PyFrameRecord)
            );
        }

        py_incref((PyObject *)exc_class);
        py_incref(message);
        py_incref(cause);
        py_incref(context);
        dst->exc_class = exc_class;
        dst->message = message;
        dst->cause = cause;
        dst->context = context;
        dst->n_frames = n_frames;
        dst->cap_frames = cap_frames;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, (PyObject **)&src->exc_class, (PyObject **)&dst->exc_class
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->message, &dst->message
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->cause, &dst->cause
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->context, &dst->context
        );
        return 0;
    }
    if (tag == PY_TYPE_CLASS) {
        PyClassObject *src = (PyClassObject *)from;
        PyClassObject *dst = (PyClassObject *)to;
        int32_t n_bases = src->n_bases;
        int32_t n_mro = src->n_mro;
        int32_t n_methods = src->n_methods;
        int32_t n_fields = src->n_fields;
        PyObject *del_method = src->del_method;
        PyObject *attrs = pcc_gc_load_ptr((PyObject *)src, &src->attrs);
        PyClassObject *metaclass = (PyClassObject *)pcc_gc_load_ptr(
            (PyObject *)src,
            (PyObject **)&src->metaclass
        );

        dst->name = src->name;
        dst->n_bases = 0;
        dst->bases = NULL;
        dst->n_mro = 0;
        dst->mro = NULL;
        dst->n_methods = 0;
        dst->methods = NULL;
        dst->n_fields = 0;
        dst->field_names = NULL;
        dst->instance_size = src->instance_size;
        dst->type_tag_alloc = src->type_tag_alloc;
        dst->del_method = del_method;
        dst->attrs = NULL;
        dst->metaclass = metaclass;

        if (n_bases < 0 || n_mro < 0 || n_methods < 0 || n_fields < 0) {
            return -1;
        }
        if (
            n_bases > INT64_MAX / (int64_t)sizeof(PyClassObject *)
            || n_mro > INT64_MAX / (int64_t)sizeof(PyClassObject *)
            || n_methods > INT64_MAX / (int64_t)sizeof(PyClassMethod)
            || n_fields > INT64_MAX / (int64_t)sizeof(const char *)
        ) {
            return -1;
        }

        if (n_bases > 0) {
            if (src->bases == NULL) return -1;
            dst->bases = (PyClassObject **)malloc(
                (size_t)n_bases * sizeof(PyClassObject *)
            );
            if (dst->bases == NULL) return -1;
            for (int32_t i = 0; i < n_bases; i++) {
                dst->bases[i] = (PyClassObject *)pcc_gc_note_relocation_read_unlocked(
                    (PyObject *)src->bases[i]
                );
            }
        }
        if (n_mro > 0) {
            if (src->mro == NULL) return -1;
            dst->mro = (PyClassObject **)malloc(
                (size_t)n_mro * sizeof(PyClassObject *)
            );
            if (dst->mro == NULL) return -1;
            for (int32_t i = 0; i < n_mro; i++) {
                PyClassObject *entry = (PyClassObject *)pcc_gc_note_relocation_read_unlocked(
                    (PyObject *)src->mro[i]
                );
                if (entry == src) entry = dst;
                dst->mro[i] = entry;
            }
        }
        if (n_methods > 0) {
            if (src->methods == NULL) return -1;
            dst->methods = (PyClassMethod *)malloc(
                (size_t)n_methods * sizeof(PyClassMethod)
            );
            if (dst->methods == NULL) return -1;
            for (int32_t i = 0; i < n_methods; i++) {
                dst->methods[i].name = src->methods[i].name;
                dst->methods[i].func = src->methods[i].func;
            }
        }
        if (n_fields > 0) {
            if (src->field_names == NULL) return -1;
            dst->field_names = (const char **)malloc(
                (size_t)n_fields * sizeof(const char *)
            );
            if (dst->field_names == NULL) return -1;
            memcpy(
                (void *)dst->field_names,
                src->field_names,
                (size_t)n_fields * sizeof(const char *)
            );
        }

        dst->n_bases = n_bases;
        dst->n_mro = n_mro;
        dst->n_methods = n_methods;
        dst->n_fields = n_fields;
        py_incref(attrs);
        dst->attrs = attrs;
        if (py_class_attrs_retarget(src, dst) != 0) return -1;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->attrs, &dst->attrs
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from,
            to,
            (PyObject **)&src->metaclass,
            (PyObject **)&dst->metaclass
        );
        return 0;
    }
    if (tag == PY_TYPE_WEAKREF) {
        PyWeakRefObject *src = (PyWeakRefObject *)from;
        PyWeakRefObject *dst = (PyWeakRefObject *)to;
        PyObject *callback = src->callback;

        dst->target = src->target;
        dst->callback = NULL;
        dst->prev = NULL;
        dst->next = NULL;

        if (callback != NULL) py_incref(callback);
        dst->callback = callback;
        if (py_weakref_retarget(from, to) != 0) return -1;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->callback, &dst->callback
        );
        return 0;
    }
    if (tag == PY_TYPE_THREAD) {
        PccGcThreadObject *src = (PccGcThreadObject *)from;
        PccGcThreadObject *dst = (PccGcThreadObject *)to;
        PyObject *callable = src->callable;
        PyObject *args = src->args;
        PyObject *result = src->result;

        if (src->handle != NULL) return -1;

        dst->handle = NULL;
        dst->callable = NULL;
        dst->args = NULL;
        dst->result = NULL;
        dst->started = src->started;
        dst->joined = src->joined;
        dst->finished = src->finished;

        if (callable != NULL) py_incref(callable);
        if (args != NULL) py_incref(args);
        if (result != NULL) py_incref(result);
        dst->callable = callable;
        dst->args = args;
        dst->result = result;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->callable, &dst->callable
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->args, &dst->args
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->result, &dst->result
        );
        return 0;
    }
    if (tag == PY_TYPE_TASK) {
        PyTaskObject *src = (PyTaskObject *)from;
        PyTaskObject *dst = (PyTaskObject *)to;
        PyObject *coro = src->coro;
        PyObject *result = src->result;
        PyObject *waiter = src->waiter;

        dst->coro = NULL;
        dst->result = NULL;
        dst->waiter = NULL;

        py_incref(coro);
        py_incref(result);
        py_incref(waiter);
        dst->coro = coro;
        dst->result = result;
        dst->waiter = waiter;
        dst->done = src->done;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->coro, &dst->coro
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->result, &dst->result
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->waiter, &dst->waiter
        );
        return 0;
    }
    if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *src = (PyVirtualThreadObject *)from;
        PyVirtualThreadObject *dst = (PyVirtualThreadObject *)to;
        PyObject *continuation = src->continuation;
        PyObject *result = src->result;

        dst->continuation = NULL;
        dst->result = NULL;
        dst->state = src->state;
        dst->queued = 0;
        dst->pinned = src->pinned;

        if (src->queued != 0) return -1;
        py_incref(continuation);
        py_incref(result);
        dst->continuation = continuation;
        dst->result = result;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->continuation, &dst->continuation
        );
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, &src->result, &dst->result
        );
        return 0;
    }
    if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
        PyInstanceObject *src = (PyInstanceObject *)from;
        PyInstanceObject *dst = (PyInstanceObject *)to;
        PyClassObject *cls = src->cls;
        dst->cls = NULL;

        if (size < (int64_t)sizeof(PyInstanceObject)) return -1;
        if (cls == NULL || py_header((PyObject *)cls)->type_tag != PY_TYPE_CLASS) {
            return -1;
        }

        int32_t n_fields = cls->n_fields;
        if (n_fields < 0) n_fields = 0;
        int64_t n_slots = (int64_t)n_fields;
        if ((py_header((PyObject *)cls)->flags & 2) == 0) n_slots++;
        if (n_slots < 0) return -1;
        if (n_slots > (
            INT64_MAX - (int64_t)sizeof(PyInstanceObject)
        ) / (int64_t)sizeof(PyObject *)) {
            return -1;
        }
        int64_t required = (int64_t)sizeof(PyInstanceObject)
            + n_slots * (int64_t)sizeof(PyObject *);
        if (size < required) return -1;

        for (int64_t i = 0; i < n_slots; i++) {
            PyObject *child = src->fields[i];
            py_incref(child);
            dst->fields[i] = child;
        }
        dst->cls = cls;
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from, to, (PyObject **)&src->cls, (PyObject **)&dst->cls
        );
        for (int64_t i = 0; i < n_slots; i++) {
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from,
                to,
                &src->fields[i],
                &dst->fields[i]
            );
        }
        return 0;
    }
    if (tag == PY_TYPE_DICT) {
        PyDictObject *src = (PyDictObject *)from;
        PyDictObject *dst = (PyDictObject *)to;
        int64_t dict_size = src->size;
        int64_t capacity = src->capacity;
        int64_t entries_used = src->entries_used;
        int64_t *src_indices = src->indices;
        DictEntry *src_entries = src->entries;

        dst->size = 0;
        dst->capacity = 0;
        dst->indices = NULL;
        dst->entries = NULL;
        dst->entries_used = 0;

        if (size < (int64_t)sizeof(PyDictObject)) return -1;
        if (capacity < 0 || entries_used < 0 || dict_size < 0) return -1;
        if (entries_used > capacity || dict_size > entries_used) return -1;
        if (capacity == 0) return 0;
        if (src_indices == NULL || src_entries == NULL) return -1;
        if (capacity > INT64_MAX / (int64_t)sizeof(int64_t)) return -1;
        if (capacity > INT64_MAX / (int64_t)sizeof(DictEntry)) return -1;

        int64_t *indices = (int64_t *)malloc(
            (size_t)capacity * sizeof(int64_t)
        );
        if (indices == NULL) return -1;
        DictEntry *entries = (DictEntry *)calloc(
            (size_t)capacity, sizeof(DictEntry)
        );
        if (entries == NULL) {
            free(indices);
            return -1;
        }

        memcpy(indices, src_indices, (size_t)capacity * sizeof(int64_t));
        for (int64_t i = 0; i < entries_used; i++) {
            DictEntry *src_entry = &src_entries[i];
            DictEntry *dst_entry = &entries[i];
            PyObject *key = src_entry->key;
            PyObject *value = src_entry->value;
            dst_entry->hash = src_entry->hash;
            dst_entry->key = key;
            dst_entry->value = value;
            if (key != NULL) {
                py_incref(key);
                py_incref(value);
            }
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from,
                to,
                &src_entry->key,
                &dst_entry->key
            );
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from,
                to,
                &src_entry->value,
                &dst_entry->value
            );
        }

        dst->indices = indices;
        dst->entries = entries;
        dst->capacity = capacity;
        dst->size = dict_size;
        dst->entries_used = entries_used;
        return 0;
    }
    if (tag == PY_TYPE_SET) {
        PySetObject *src = (PySetObject *)from;
        PySetObject *dst = (PySetObject *)to;
        int64_t capacity = src->capacity;
        SetEntry *src_entries = src->entries;

        dst->size = 0;
        dst->capacity = 0;
        dst->fill = 0;
        dst->entries = NULL;

        if (size < (int64_t)sizeof(PySetObject)) return -1;
        if (capacity < 0) return -1;
        if (capacity == 0) return 0;
        if (src_entries == NULL) return -1;
        if (capacity > INT64_MAX / (int64_t)sizeof(SetEntry)) return -1;

        SetEntry *entries = (SetEntry *)calloc(
            (size_t)capacity, sizeof(SetEntry)
        );
        if (entries == NULL) return -1;

        for (int64_t i = 0; i < capacity; i++) {
            PyObject *key = src_entries[i].key;
            entries[i].hash = src_entries[i].hash;
            entries[i].key = key;
            if (key != NULL && key != py_set_dummy) {
                py_incref(key);
            }
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from,
                to,
                &src_entries[i].key,
                &entries[i].key
            );
        }

        dst->entries = entries;
        dst->capacity = capacity;
        dst->size = src->size;
        dst->fill = src->fill;
        return 0;
    }
    if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *src = (PyTupleObject *)from;
        PyTupleObject *dst = (PyTupleObject *)to;
        int64_t length = src->len;
        dst->len = 0;

        if (length < 0) return -1;
        if (length > (
            INT64_MAX - (int64_t)sizeof(PyTupleObject)
        ) / (int64_t)sizeof(PyObject *)) {
            return -1;
        }
        int64_t required = (int64_t)sizeof(PyTupleObject)
            + length * (int64_t)sizeof(PyObject *);
        if (size < required) return -1;

        for (int64_t i = 0; i < length; i++) {
            py_incref(dst->items[i]);
            pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
                from,
                to,
                &src->items[i],
                &dst->items[i]
            );
        }
        dst->len = length;
        return 0;
    }
    if (tag != PY_TYPE_LIST) return 0;

    PyListObject *src = (PyListObject *)from;
    PyListObject *dst = (PyListObject *)to;
    int64_t length = src->length;
    int64_t capacity = src->capacity;
    PyObject **src_items = src->items;

    dst->length = 0;
    dst->capacity = 0;
    dst->items = NULL;

    if (length < 0 || capacity < length) return -1;
    if (capacity == 0) return 0;
    if (src_items == NULL) return -1;
    if (capacity > INT64_MAX / (int64_t)sizeof(PyObject *)) return -1;

    PyObject **items = (PyObject **)calloc(
        (size_t)capacity, sizeof(PyObject *)
    );
    if (items == NULL) return -1;
    for (int64_t i = 0; i < length; i++) {
        items[i] = src_items[i];
        py_incref(items[i]);
    }

    dst->items = items;
    dst->length = length;
    dst->capacity = capacity;
    for (int64_t i = 0; i < capacity; i++) {
        pcc_gc_backend4_remembered_set_retarget_slot_unlocked(
            from,
            to,
            &src_items[i],
            &items[i]
        );
    }
    return 0;
}

static int pcc_gc_backend_uses_forwarding(void) {
    return pcc_gc_selected_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING;
}

static int pcc_gc_backend4_evacuation_policy_accept(int64_t size) {
    if (size <= 0) return 0;
    if (size <= PCC_GC_BACKEND4_SMALL_PAGE_LIMIT) return 1;
    if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) return 1;
    return 0;
}

static int pcc_gc_backend4_evacuation_policy_defer_large(int64_t size) {
    return size > PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT ? 1 : 0;
}

static int pcc_gc_backend4_large_page_evacuation_policy_accept(
    PccGcZPage *page,
    int64_t size
) {
    if (page == NULL) return 0;
    if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) return 0;
    if (page->page_class != 2) return 0;
    return page->capacity_bytes > page->used_bytes ? 1 : 0;
}

static void pcc_gc_backend4_note_page_candidate(
    int64_t size,
    PccGcZPage *page
) {
    if (size <= 0) return;
    __atomic_add_fetch(
        &pcc_gc_backend4_evacuation_candidate_bytes_count,
        size,
        __ATOMIC_RELAXED
    );
    if (size <= PCC_GC_BACKEND4_SMALL_PAGE_LIMIT) {
        __atomic_add_fetch(
            &pcc_gc_backend4_small_page_candidates,
            1,
            __ATOMIC_RELAXED
        );
        __atomic_add_fetch(
            &pcc_gc_backend4_small_page_candidate_bytes_count,
            size,
            __ATOMIC_RELAXED
        );
    } else if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
        __atomic_add_fetch(
            &pcc_gc_backend4_medium_page_candidates,
            1,
            __ATOMIC_RELAXED
        );
        __atomic_add_fetch(
            &pcc_gc_backend4_medium_page_candidate_bytes_count,
            size,
            __ATOMIC_RELAXED
        );
    }
    if (page == NULL) return;
    int64_t page_bytes = page->used_bytes;
    if (page_bytes <= 0) return;
    __atomic_add_fetch(
        &pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,
        page_bytes,
        __ATOMIC_RELAXED
    );
    if (page->page_class == 0) {
        __atomic_add_fetch(
            &pcc_gc_backend4_small_page_candidate_zpage_bytes_count,
            page_bytes,
            __ATOMIC_RELAXED
        );
    } else if (page->page_class == 1) {
        __atomic_add_fetch(
            &pcc_gc_backend4_medium_page_candidate_zpage_bytes_count,
            page_bytes,
            __ATOMIC_RELAXED
        );
    }
}

static int pcc_gc_backend4_relocation_set_contains_page_unlocked(
    PccGcZPage *page
) {
    if (page == NULL) return 0;
    for (
        PccGcRelocationNode *n = pcc_gc_relocation_set;
        n != NULL;
        n = n->next
    ) {
        PccGcZPageNode *zp = pcc_gc_backend4_zpage_find_unlocked(n->obj);
        if (zp != NULL && zp->page == page) return 1;
    }
    return 0;
}

static PccGcZPageEvacuationNode *
pcc_gc_backend4_evacuation_page_find_unlocked(PccGcZPage *page) {
    if (page == NULL) return NULL;
    for (
        PccGcZPageEvacuationNode *n = pcc_gc_backend4_evacuation_pages;
        n != NULL;
        n = n->next
    ) {
        if (n->page == page) return n;
    }
    return NULL;
}

static int pcc_gc_backend4_evacuation_page_add_unlocked(PccGcZPage *page) {
    if (page == NULL) return 0;
    if (pcc_gc_backend4_evacuation_page_find_unlocked(page) != NULL) {
        return 0;
    }
    PccGcZPageEvacuationNode *n = (PccGcZPageEvacuationNode *)calloc(
        1, sizeof(PccGcZPageEvacuationNode)
    );
    if (n == NULL) return 0;
    n->page = page;
    n->next = pcc_gc_backend4_evacuation_pages;
    pcc_gc_backend4_evacuation_pages = n;
    return 1;
}

static void pcc_gc_backend4_evacuation_page_remove_unlocked(PccGcZPage *page) {
    if (page == NULL) return;
    PccGcZPageEvacuationNode **cur = &pcc_gc_backend4_evacuation_pages;
    while (*cur != NULL) {
        if ((*cur)->page == page) {
            PccGcZPageEvacuationNode *dead = *cur;
            *cur = dead->next;
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static void pcc_gc_backend4_evacuation_page_clear_unlocked(void) {
    PccGcZPageEvacuationNode *n = pcc_gc_backend4_evacuation_pages;
    pcc_gc_backend4_evacuation_pages = NULL;
    while (n != NULL) {
        PccGcZPageEvacuationNode *next = n->next;
        free(n);
        n = next;
    }
}

static int pcc_gc_backend4_zpage_candidate_snapshot(
    PccGcZPageNode *zp,
    PccGcZPageEvacuationCandidate *candidate,
    int allow_large_pages
) {
    if (zp == NULL || candidate == NULL) return 0;
    PyObject *o = zp->owner;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    PccGcZPage *page = zp->page;
    if (page == NULL) return 0;
    if (pcc_gc_relocation_set_find(o) != NULL) return 0;
    PyObjectHeader *h = py_header(o);
    int32_t flags = py_header_flags_load(h);
    if ((flags & PY_FLAG_GC_PINNED) != 0) return 0;
    if ((flags & PY_FLAG_GC_RELOCATION_TARGET) != 0) return 0;
    if (!pcc_gc_colored_relocate_copy_supported_tag(h->type_tag)) return 0;
    if (
        h->type_tag == PY_TYPE_THREAD
        && ((PccGcThreadObject *)o)->handle != NULL
    ) return 0;
    int64_t owner_size = pcc_gc_known_object_size_unlocked(o);
    if (owner_size <= 0) return 0;
    int large_page_accepted = 0;
    if (!pcc_gc_backend4_evacuation_policy_accept(owner_size)) {
        large_page_accepted =
            allow_large_pages
            && pcc_gc_backend4_large_page_evacuation_policy_accept(
                page,
                owner_size
            );
    }
    if (
        !pcc_gc_backend4_evacuation_policy_accept(owner_size)
        && !large_page_accepted
    ) {
        if (
            pcc_gc_backend4_evacuation_policy_defer_large(owner_size)
            && (flags & PY_FLAG_GC_LARGE_DEFERRED) == 0
        ) {
            py_header_flags_or(h, PY_FLAG_GC_LARGE_DEFERRED);
            __atomic_add_fetch(
                &pcc_gc_backend4_large_object_defers,
                1,
                __ATOMIC_RELAXED
            );
            __atomic_add_fetch(
                &pcc_gc_backend4_large_object_deferred_bytes_count,
                owner_size,
                __ATOMIC_RELAXED
            );
        }
        return 0;
    }
    int64_t score = page->capacity_bytes - page->used_bytes;
    if (score < 0) score = 0;
    score += page->remembered_slots;
    score += page->remembered_cards;
    score += pcc_gc_backend4_owner_remembered_slots_unlocked(o);
    if ((flags & PY_FLAG_GC_OLD) != 0) score++;
    if (score <= 0) return 0;
    candidate->mapping = zp;
    candidate->page = page;
    candidate->owner = o;
    candidate->used_bytes = owner_size;
    candidate->capacity_bytes = page->capacity_bytes;
    candidate->fragmentation_bytes = (
        page->capacity_bytes > page->used_bytes
        ? page->capacity_bytes - page->used_bytes
        : 0
    );
    candidate->remembered_slots = page->remembered_slots;
    candidate->remembered_cards = page->remembered_cards;
    candidate->score = score;
    candidate->owner_flags = flags;
    return 1;
}

static int64_t pcc_gc_backend4_select_page_objects_unlocked(
    PccGcZPageEvacuationCandidate *seed,
    int64_t budget,
    int allow_large_pages
) {
    if (seed == NULL || seed->page == NULL || budget <= 0) return 0;
    int64_t selected = 0;
    for (int pass = 0; pass < 2 && selected < budget; pass++) {
        for (
            PccGcZPageNode *zp = pcc_gc_backend4_zpages;
            zp != NULL && selected < budget;
            zp = zp->next
        ) {
            if (zp->page != seed->page) continue;
            int is_seed = zp->owner == seed->owner;
            if ((pass == 0 && !is_seed) || (pass == 1 && is_seed)) {
                continue;
            }
            PccGcZPageEvacuationCandidate candidate;
            if (
                !pcc_gc_backend4_zpage_candidate_snapshot(
                    zp,
                    &candidate,
                    allow_large_pages
                )
            ) {
                continue;
            }
            int count_page = (
                pcc_gc_backend4_evacuation_page_find_unlocked(
                    candidate.page
                ) == NULL
            );
            if (!pcc_gc_relocation_set_add(candidate.owner)) continue;
            __atomic_add_fetch(
                &pcc_gc_backend4_evacuation_candidates,
                1,
                __ATOMIC_RELAXED
            );
            if (count_page) {
                count_page = pcc_gc_backend4_evacuation_page_add_unlocked(
                    candidate.page
                );
            }
            pcc_gc_backend4_note_page_candidate(
                candidate.used_bytes,
                count_page ? candidate.page : NULL
            );
            selected++;
            if ((selected % PCC_GC_SAFEPOINT_BATCH) == 0) {
                pcc_thread_safepoint();
            }
        }
    }
    return selected;
}

static int64_t pcc_gc_select_relocation_set_unlocked(int64_t budget) {
    int64_t selected = 0;
    while (selected < budget) {
        PccGcZPageEvacuationCandidate best;
        int has_best = 0;
        int64_t best_score = -1;
        for (
            PccGcZPageNode *zp = pcc_gc_backend4_zpages;
            zp != NULL;
            zp = zp->next
        ) {
            PccGcZPageEvacuationCandidate candidate;
            if (
                !pcc_gc_backend4_zpage_candidate_snapshot(zp, &candidate, 0)
            ) {
                continue;
            }
            if (candidate.score > best_score) {
                best = candidate;
                best_score = candidate.score;
                has_best = 1;
            }
        }
        if (!has_best) break;
        int64_t added = pcc_gc_backend4_select_page_objects_unlocked(
            &best,
            budget - selected,
            0
        );
        if (added <= 0) {
            break;
        }
        selected += added;
    }
    return selected;
}

static int64_t pcc_gc_backend4_select_relocation_pages_unlocked(
    int64_t page_budget
) {
    int64_t selected = 0;
    int64_t pages = 0;
    while (pages < page_budget) {
        PccGcZPageEvacuationCandidate best;
        int has_best = 0;
        int64_t best_score = -1;
        for (
            PccGcZPageNode *zp = pcc_gc_backend4_zpages;
            zp != NULL;
            zp = zp->next
        ) {
            PccGcZPageEvacuationCandidate candidate;
            if (
                !pcc_gc_backend4_zpage_candidate_snapshot(zp, &candidate, 1)
            ) {
                continue;
            }
            if (
                pcc_gc_backend4_evacuation_page_find_unlocked(
                    candidate.page
                ) != NULL
            ) {
                continue;
            }
            if (candidate.score > best_score) {
                best = candidate;
                best_score = candidate.score;
                has_best = 1;
            }
        }
        if (!has_best) break;
        int64_t object_budget = best.page->object_count;
        if (object_budget < 1) object_budget = 1;
        int64_t before_selected = selected;
        int64_t added = pcc_gc_backend4_select_page_objects_unlocked(
            &best,
            object_budget,
            1
        );
        if (added <= 0) break;
        selected += added;
        if (selected > before_selected) pages++;
    }
    return selected;
}

static int64_t pcc_gc_backend4_select_relocation_pages(int64_t page_budget) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (page_budget <= 0) return 0;
    pcc_gc_graph_lock();
    int64_t selected =
        pcc_gc_backend4_select_relocation_pages_unlocked(page_budget);
    pcc_gc_graph_unlock();
    return selected;
}

int64_t pcc_gc_select_relocation_set(int64_t budget) {
    pcc_gc_init_config();
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (budget <= 0) return 0;
    pcc_gc_graph_lock();
    int64_t selected = pcc_gc_select_relocation_set_unlocked(budget);
    pcc_gc_graph_unlock();
    return selected;
}

static int64_t pcc_gc_known_object_size_unlocked(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (n->obj == obj && !pcc_gc_object_node_is_freeing(n)) {
            return n->size;
        }
    }
    return 0;
}

static int64_t pcc_gc_known_object_size(PyObject *obj) {
    pcc_gc_graph_lock();
    int64_t size = pcc_gc_known_object_size_unlocked(obj);
    pcc_gc_graph_unlock();
    return size;
}

static PyObject *pcc_gc_relocate_copy_unlocked(PyObject *from, int64_t size) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) {
        return NULL;
    }
    if (from == NULL || PY_IS_TAGGED_INT(from)) return NULL;
    if (size < (int64_t)sizeof(PyObjectHeader)) return NULL;
    if (pcc_gc_forwarding_find(from) != NULL) return NULL;
    if (pcc_gc_relocation_set_find(from) == NULL) return NULL;
    PyObjectHeader *from_h = py_header(from);
    if ((from_h->flags & PY_FLAG_GC_PINNED) != 0) return NULL;
    if (!pcc_gc_colored_relocate_copy_supported_tag(from_h->type_tag)) {
        return NULL;
    }

    int64_t known_size = pcc_gc_known_object_size_unlocked(from);
    if (known_size <= 0 || size > known_size) return NULL;

    PyObject *to = pcc_gc_alloc(
        size,
        from_h->type_tag,
        py_header_flags_load(from_h)
            & ~(PY_FLAG_GC_RELOCATION_CANDIDATE | PY_FLAG_GC_RELOCATION_TARGET)
    );
    if (to == NULL) return NULL;
    memcpy(to, from, (size_t)size);
    PyObjectHeader *to_h = py_header(to);
    to_h->refcount = 1;
    py_header_flags_and(
        to_h, ~(PY_FLAG_GC_RELOCATION_CANDIDATE | PY_FLAG_GC_RELOCATION_TARGET)
    );
    if (pcc_gc_relocate_copy_payload(from, to, size) != 0) {
        py_decref(to);
        return NULL;
    }
    if (pcc_gc_install_forwarding(from, to) != 0) {
        py_decref(to);
        return NULL;
    }
    PccGcZPage *from_page = NULL;
    PccGcZPageNode *from_zpage = pcc_gc_backend4_zpage_find_unlocked(from);
    if (from_zpage != NULL) {
        from_page = from_zpage->page;
    }
    __atomic_add_fetch(
        &pcc_gc_backend4_evacuated_bytes_count,
        size,
        __ATOMIC_RELAXED
    );
    pcc_gc_relocation_set_remove(from);
    if (
        from_page != NULL
        && !pcc_gc_backend4_relocation_set_contains_page_unlocked(from_page)
    ) {
        pcc_gc_backend4_evacuation_page_remove_unlocked(from_page);
    }
    pcc_gc_backend4_zpage_remove_unlocked(from);
    return to;
}

PyObject *pcc_gc_relocate_copy(PyObject *from, int64_t size) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    PyObject *to = pcc_gc_relocate_copy_unlocked(from, size);
    pcc_gc_graph_unlock();
    return to;
}

static int64_t pcc_gc_relocate_selected_unlocked(int64_t budget) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (budget <= 0) return 0;
    int64_t moved = 0;
    PccGcRelocationNode *n = pcc_gc_relocation_set;
    while (n != NULL && moved < budget) {
        PccGcRelocationNode *next = n->next;
        PyObject *to = pcc_gc_relocate_copy_unlocked(
            n->obj,
            pcc_gc_known_object_size_unlocked(n->obj)
        );
        if (to != NULL) {
            py_decref(to);
            moved++;
            if ((moved % PCC_GC_SAFEPOINT_BATCH) == 0) {
                pcc_thread_safepoint();
            }
        }
        n = next;
    }
    if (moved > 0 && pcc_gc_relocation_set != NULL) {
        __atomic_add_fetch(
            &pcc_gc_backend4_evacuation_incomplete_batches_count,
            1,
            __ATOMIC_RELAXED
        );
    }
    return moved;
}

static int64_t pcc_gc_relocate_selected(int64_t budget) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (budget <= 0) return 0;
    pcc_gc_graph_lock();
    int64_t moved = pcc_gc_relocate_selected_unlocked(budget);
    pcc_gc_graph_unlock();
    return moved;
}

int64_t pcc_gc_backend4_evacuation_drain(int64_t budget) {
    pcc_gc_init_config();
    return pcc_gc_relocate_selected(budget);
}

static int64_t pcc_gc_backend4_relocate_selected_page_unlocked(
    PccGcZPage *page
) {
    if (page == NULL) return 0;
    int64_t moved = 0;
    PccGcRelocationNode *n = pcc_gc_relocation_set;
    while (n != NULL) {
        PccGcRelocationNode *next = n->next;
        PccGcZPageNode *zp = pcc_gc_backend4_zpage_find_unlocked(n->obj);
        if (zp != NULL && zp->page == page) {
            PyObject *to = pcc_gc_relocate_copy_unlocked(
                n->obj,
                pcc_gc_known_object_size_unlocked(n->obj)
            );
            if (to != NULL) {
                py_decref(to);
                moved++;
                if ((moved % PCC_GC_SAFEPOINT_BATCH) == 0) {
                    pcc_thread_safepoint();
                }
            }
        }
        n = next;
    }
    return moved;
}

int64_t pcc_gc_backend4_evacuation_page_drain(int64_t page_budget) {
    pcc_gc_init_config();
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return 0;
    if (page_budget <= 0) return 0;
    int64_t moved = 0;
    int64_t pages = 0;
    pcc_gc_graph_lock();
    while (pages < page_budget && pcc_gc_backend4_evacuation_pages != NULL) {
        PccGcZPage *page = pcc_gc_backend4_evacuation_pages->page;
        int64_t page_moved =
            pcc_gc_backend4_relocate_selected_page_unlocked(page);
        if (page_moved <= 0) break;
        moved += page_moved;
        pages++;
    }
    if (moved > 0 && pcc_gc_relocation_set != NULL) {
        __atomic_add_fetch(
            &pcc_gc_backend4_evacuation_incomplete_batches_count,
            1,
            __ATOMIC_RELAXED
        );
    }
    pcc_gc_graph_unlock();
    return moved;
}

static int64_t pcc_gc_install_forwarding_unlocked(PyObject *from, PyObject *to) {
    if (!pcc_gc_backend_uses_forwarding()) return -1;
    if (from == NULL || to == NULL) return -1;
    if (PY_IS_TAGGED_INT(from) || PY_IS_TAGGED_INT(to)) return -1;
    if (from == to) return -1;
    if (!pcc_gc_is_known_object(from) || !pcc_gc_is_known_object(to)) {
        return -1;
    }
    PyObjectHeader *from_h = py_header(from);
    if ((from_h->flags & PY_FLAG_GC_PINNED) != 0) {
        pcc_gc_relocation_pin_rejects++;
        return -2;
    }
    PccGcIdentityNode *from_identity = pcc_gc_identity_ensure(from);
    if (from_identity == NULL) return -1;
    if (!pcc_gc_identity_assign(to, from_identity->id)) return -1;

    PccGcForwardNode *existing = pcc_gc_forwarding_find(from);
    if (existing != NULL) {
        if (existing->to != to) {
            py_incref(to);
            PyObject *old = existing->to;
            existing->to = to;
            py_decref(old);
        }
    } else {
        PccGcForwardNode *n = (
            PccGcForwardNode *
        )calloc(1, sizeof(PccGcForwardNode));
        if (n == NULL) return -1;
        py_incref(to);
        n->from = from;
        n->to = to;
        n->next = pcc_gc_forwardings;
        pcc_gc_forwardings = n;
    }
    py_header_flags_or(from_h, PY_FLAG_GC_RELOCATION_CANDIDATE);
    py_header_flags_or(py_header(to), PY_FLAG_GC_RELOCATION_TARGET);
    pcc_gc_relocation_forwards++;
    return 0;
}

int64_t pcc_gc_install_forwarding(PyObject *from, PyObject *to) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t rc = pcc_gc_install_forwarding_unlocked(from, to);
    pcc_gc_graph_unlock();
    return rc;
}

static int64_t pcc_gc_step_generational_promotion(int64_t budget);

static void pcc_gc_minor_collect_reset(void) {
    __atomic_add_fetch(
        &pcc_gc_minor_collections, 1, __ATOMIC_RELAXED
    );
    if (pcc_gc_selected_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) {
        (void)pcc_gc_step_generational_promotion(1024);
    }
    __atomic_store_n(&pcc_gc_minor_bytes, 0, __ATOMIC_RELEASE);
}

static void pcc_gc_note_minor_alloc(int64_t bytes) {
    if (bytes <= 0 || bytes > pcc_gc_minor_alloc_max) return;
    __atomic_add_fetch(
        &pcc_gc_minor_allocations, 1, __ATOMIC_RELAXED
    );
    __atomic_add_fetch(&pcc_gc_minor_bytes, bytes, __ATOMIC_ACQ_REL);
}

static int64_t pcc_gc_align16(int64_t bytes) {
    if (bytes <= 0) return 0;
    if (bytes > INT64_MAX - 15) return 0;
    return (bytes + 15) & ~15LL;
}

static PccGcMinorBlock *pcc_gc_minor_new_block(int64_t min_bytes) {
    int64_t block_bytes = pcc_gc_minor_heap_size;
    if (block_bytes < min_bytes) block_bytes = min_bytes;
    block_bytes = pcc_gc_align16(block_bytes);
    if (block_bytes <= 0) return NULL;

    PccGcMinorBlock *block = (PccGcMinorBlock *)calloc(
        1, sizeof(PccGcMinorBlock)
    );
    if (block == NULL) return NULL;
    block->base = (uint8_t *)calloc(1, (size_t)block_bytes);
    if (block->base == NULL) {
        free(block);
        return NULL;
    }
    block->ptr = block->base;
    block->end = block->base + block_bytes;
    block->owner_thread_id = pcc_current_thread_id();
    pcc_gc_graph_lock();
    block->next = pcc_gc_minor_blocks;
    pcc_gc_minor_blocks = block;
    pcc_gc_graph_unlock();
    pcc_gc_minor_current = block;
    __atomic_add_fetch(
        &pcc_gc_minor_arena_refills, 1, __ATOMIC_RELAXED
    );
    return block;
}

void *pcc_gc_try_minor_alloc(int64_t bytes) {
    pcc_gc_init_config();
    if (pcc_gc_selected_backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) {
        return NULL;
    }
    pcc_gc_pending_minor_block = NULL;
    int64_t aligned = pcc_gc_align16(bytes);
    if (aligned <= 0 || aligned > pcc_gc_minor_alloc_max) {
        __atomic_add_fetch(
            &pcc_gc_minor_arena_fallbacks, 1, __ATOMIC_RELAXED
        );
        return NULL;
    }

    PccGcMinorBlock *block = pcc_gc_minor_current;
    if (
        block == NULL
        || (int64_t)(block->end - block->ptr) < aligned
    ) {
        if (block != NULL) pcc_gc_minor_collect_reset();
        block = pcc_gc_minor_new_block(aligned);
        if (block == NULL) {
            __atomic_add_fetch(
                &pcc_gc_minor_arena_fallbacks, 1, __ATOMIC_RELAXED
            );
            return NULL;
        }
    }

    void *mem = block->ptr;
    block->ptr += aligned;
    __atomic_add_fetch(&block->live_objects, 1, __ATOMIC_ACQ_REL);
    pcc_gc_pending_minor_block = block;
    __atomic_add_fetch(
        &pcc_gc_minor_arena_bumps, 1, __ATOMIC_RELAXED
    );
    pcc_gc_note_minor_alloc(aligned);
    memset(mem, 0, (size_t)bytes);
    return mem;
}

static int pcc_gc_backend_valid(int64_t backend) {
    return backend >= PCC_GC_KIND_REFCOUNT_CYCLE
        && backend <= PCC_GC_KIND_COLORED_RELOCATING;
}

static int pcc_gc_should_track_frame_roots(void) {
    return pcc_gc_selected_backend != PCC_GC_KIND_REFCOUNT_CYCLE
        || pcc_gc_backend0_frame_roots_enabled != 0;
}

int64_t pcc_gc_backend(void) {
    pcc_gc_init_config();
    return pcc_gc_selected_backend;
}

int64_t pcc_gc_set_backend(int64_t backend) {
    pcc_gc_init_config();
    if (!pcc_gc_backend_valid(backend)) return -1;
    int64_t old_backend = pcc_gc_selected_backend;
    if (backend == PCC_GC_KIND_REFCOUNT_CYCLE) {
        pcc_gc_backend0_frame_roots_enabled = 1;
    }
    pcc_gc_graph_lock();
    pcc_gc_selected_backend = backend;
    pcc_gc_mark_active_store(0);
    pcc_gc_cycle_requested_store(1);
    __atomic_store_n(&pcc_gc_debt_bytes, 0, __ATOMIC_RELEASE);
    if (!pcc_gc_tracks_objects()) {
        while (pcc_gc_objects != NULL) {
            PccGcObjectNode *next = pcc_gc_objects->next;
            free(pcc_gc_objects);
            pcc_gc_objects = next;
        }
        __atomic_store_n(&pcc_gc_live_bytes, 0, __ATOMIC_RELEASE);
    }
    pcc_gc_graph_unlock();
    if (!pcc_gc_backend_uses_forwarding()) {
        pcc_gc_forwarding_clear_all();
        pcc_gc_identity_clear_all();
    }
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) {
        pcc_gc_reset_relocation_set();
        pcc_gc_backend4_store_buffer_clear();
    }
    if (
        old_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
        && pcc_gc_selected_backend != PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        pcc_gc_cms_stop_worker();
    }
    pcc_gc_cms_maybe_start_worker();
    return 0;
}

const char *pcc_gc_backend_name(int64_t backend) {
    switch (backend) {
        case PCC_GC_KIND_REFCOUNT_CYCLE:
            return "refcount-cycle";
        case PCC_GC_KIND_INCREMENTAL_TRICOLOR:
            return "incremental-tricolor";
        case PCC_GC_KIND_CONCURRENT_MARK_SWEEP:
            return "concurrent-mark-sweep";
        case PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR:
            return "generational-minor-major";
        case PCC_GC_KIND_COLORED_RELOCATING:
            return "colored-relocating";
        default:
            return "unknown";
    }
}

int64_t pcc_gc_telemetry(int64_t metric) {
    pcc_gc_init_config();
    if (metric == PCC_GC_COUNTER_DEBT_BYTES) {
        return __atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_RELAXED);
    }
    if (metric == PCC_GC_COUNTER_MAX_PAUSE_US) {
        return __atomic_load_n(&pcc_gc_max_pause_us, __ATOMIC_RELAXED);
    }
    if (metric == PCC_GC_COUNTER_MINOR_ALLOCATIONS) {
        return __atomic_load_n(
            &pcc_gc_minor_allocations, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_MINOR_COLLECTIONS) {
        return __atomic_load_n(
            &pcc_gc_minor_collections, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_MINOR_BYTES) {
        return __atomic_load_n(&pcc_gc_minor_bytes, __ATOMIC_RELAXED);
    }
    if (metric == PCC_GC_COUNTER_CMS_WORKER_STARTS) {
        return __atomic_load_n(
            &pcc_gc_cms_worker_starts, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_QUEUE_PUSHES) {
        return __atomic_load_n(
            &pcc_gc_cms_queue_pushes, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_WORKER_DRAINS) {
        return __atomic_load_n(
            &pcc_gc_cms_worker_drains, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_MUTATOR_ASSISTS) {
        return __atomic_load_n(
            &pcc_gc_cms_mutator_assists, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_RELOCATION_FORWARDS) {
        return pcc_gc_relocation_forwards;
    }
    if (metric == PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS) {
        return pcc_gc_relocation_barrier_forwards;
    }
    if (metric == PCC_GC_COUNTER_RELOCATION_PIN_REJECTS) {
        return pcc_gc_relocation_pin_rejects;
    }
    if (metric == PCC_GC_COUNTER_CMS_WORKER_TRACES) {
        return __atomic_load_n(
            &pcc_gc_cms_worker_traces, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_MINOR_ARENA_REFILLS) {
        return __atomic_load_n(
            &pcc_gc_minor_arena_refills, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_MINOR_ARENA_BUMPS) {
        return __atomic_load_n(
            &pcc_gc_minor_arena_bumps, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_MINOR_ARENA_FALLBACKS) {
        return __atomic_load_n(
            &pcc_gc_minor_arena_fallbacks, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_WORKER_STOPS) {
        return __atomic_load_n(
            &pcc_gc_cms_worker_stops, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_WB_FLUSHES) {
        return __atomic_load_n(
            &pcc_gc_cms_wb_flushes, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_RELOCATION_SET_SIZE) {
        return pcc_gc_relocation_set_size();
    }
    if (metric == PCC_GC_COUNTER_FORWARDING_ENTRIES) {
        return pcc_gc_backend4_forwarding_entries();
    }
    if (metric == PCC_GC_COUNTER_STABLE_IDS) {
        return pcc_gc_backend4_stable_id_entries();
    }
    if (metric == PCC_GC_COUNTER_RELOCATION_FRAGMENTATION_SCORE) {
        return pcc_gc_backend4_fragmentation_score();
    }
    if (metric == PCC_GC_COUNTER_SCHEDULER_ROOTS) {
        return pcc_gc_scheduler_root_count();
    }
    if (metric == PCC_GC_COUNTER_FRAME_ROOT_SLOTS) {
        return pcc_gc_frame_root_slot_count();
    }
    if (metric == PCC_GC_COUNTER_COROUTINE_ROOT_SCORE) {
        return pcc_gc_coroutine_root_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BARRIERS) {
        return pcc_gc_backend4_generation_barrier_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_ENTRIES) {
        return pcc_gc_backend4_store_buffer_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_YOUNG_PROMOTIONS) {
        return pcc_gc_backend4_generation_promotion_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATES) {
        return pcc_gc_backend4_evacuation_candidate_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATED_BYTES) {
        return pcc_gc_backend4_evacuated_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_PAGE_POLICY_SCORE) {
        return pcc_gc_backend4_page_policy_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_DEFERS) {
        return pcc_gc_backend4_large_object_defer_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_DEFERRED_BYTES) {
        return pcc_gc_backend4_large_object_deferred_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATES) {
        return pcc_gc_backend4_small_page_candidate_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATES) {
        return pcc_gc_backend4_medium_page_candidate_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATE_BYTES) {
        return pcc_gc_backend4_evacuation_candidate_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATE_BYTES) {
        return pcc_gc_backend4_small_page_candidate_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATE_BYTES) {
        return pcc_gc_backend4_medium_page_candidate_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATE_ZPAGE_BYTES) {
        return pcc_gc_backend4_evacuation_candidate_zpage_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATE_ZPAGE_BYTES) {
        return pcc_gc_backend4_small_page_candidate_zpage_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATE_ZPAGE_BYTES) {
        return pcc_gc_backend4_medium_page_candidate_zpage_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_PAGE_CANDIDATES) {
        return pcc_gc_backend4_evacuation_page_candidate_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DRAIN_BATCHES) {
        return pcc_gc_backend4_store_buffer_drain_batches();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DRAINED_ENTRIES) {
        return pcc_gc_backend4_store_buffer_drained_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DUPLICATE_SKIPS) {
        return pcc_gc_backend4_store_buffer_duplicate_skips();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_HIGH_WATER) {
        return pcc_gc_backend4_store_buffer_high_water();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_PAGE_PRESSURE_SCORE) {
        return pcc_gc_backend4_page_pressure_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_OWNER_FANOUT_HIGH_WATER) {
        return pcc_gc_backend4_store_buffer_owner_fanout_high_water();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_OWNER_COUNT_HIGH_WATER) {
        return pcc_gc_backend4_store_buffer_owner_count_high_water();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_INCOMPLETE_DRAINS) {
        return pcc_gc_backend4_store_buffer_incomplete_drains();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_INCOMPLETE_BATCHES) {
        return pcc_gc_backend4_evacuation_incomplete_batches();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_BATCH_CAPACITY) {
        return pcc_gc_backend4_store_buffer_batch_capacity();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MAX_BATCH_SIZE) {
        return pcc_gc_backend4_store_buffer_max_batch_size();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_FULL_BATCHES) {
        return pcc_gc_backend4_store_buffer_full_batches();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_ENTRIES) {
        return pcc_gc_backend4_remembered_set_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_DUPLICATE_SKIPS) {
        return pcc_gc_backend4_remembered_set_duplicate_skips();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_HIGH_WATER) {
        return pcc_gc_backend4_remembered_set_high_water();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_ENTRIES) {
        return pcc_gc_backend4_remembered_page_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_SLOT_ENTRIES) {
        return pcc_gc_backend4_remembered_page_slot_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_HIGH_WATER) {
        return pcc_gc_backend4_remembered_page_high_water();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_COUNT) {
        return pcc_gc_backend4_zpage_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_CAPACITY_BYTES) {
        return pcc_gc_backend4_zpage_capacity_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTATION_BYTES) {
        return pcc_gc_backend4_zpage_fragmentation_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_LARGE_PAGES) {
        return pcc_gc_backend4_zpage_large_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_USED_BYTES) {
        return pcc_gc_backend4_zpage_used_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTATION_PER_MILLE) {
        return pcc_gc_backend4_zpage_fragmentation_per_mille();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_POLICY_SCORE) {
        return pcc_gc_backend4_zpage_policy_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_SLOTS) {
        return pcc_gc_backend4_zpage_remembered_slots();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARDS) {
        return pcc_gc_backend4_zpage_remembered_cards();
    }
    if (
        metric == PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARD_RATIO_PER_MILLE
    ) {
        return pcc_gc_backend4_zpage_remembered_card_ratio_per_mille();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_DIRTY_PAGES) {
        return pcc_gc_backend4_zpage_dirty_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTED_PAGES) {
        return pcc_gc_backend4_zpage_fragmented_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_YOUNG_PAGES) {
        return pcc_gc_backend4_zpage_young_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_OLD_PAGES) {
        return pcc_gc_backend4_zpage_old_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_FREE_PAGES) {
        return pcc_gc_backend4_zpage_free_pages();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_ZPAGE_FREE_CAPACITY_BYTES) {
        return pcc_gc_backend4_zpage_free_capacity_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_CAPACITY) {
        return pcc_gc_backend4_store_buffer_medium_capacity();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_PENDING) {
        return pcc_gc_backend4_store_buffer_medium_pending();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FLUSHES) {
        return pcc_gc_backend4_store_buffer_medium_flushes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FLUSHED_ENTRIES) {
        return pcc_gc_backend4_store_buffer_medium_flushed_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FULL_FLUSHES) {
        return pcc_gc_backend4_store_buffer_medium_full_flushes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_CROSS_THREAD_MEDIUM_FLUSHES) {
        return pcc_gc_backend4_store_buffer_cross_thread_medium_flushes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_STORE_BUFFER_CROSS_THREAD_MEDIUM_FLUSHED_ENTRIES) {
        return pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_EVACUATION_EFFICIENCY_PER_MILLE) {
        return pcc_gc_backend4_evacuation_efficiency_per_mille();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_FRAGMENTATION_BACKLOG_BYTES) {
        return pcc_gc_backend4_fragmentation_backlog_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_FRAGMENTATION_POLICY_SCORE) {
        return pcc_gc_backend4_fragmentation_policy_score();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_LIMIT_BYTES) {
        return pcc_gc_backend4_small_page_limit_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_LIMIT_BYTES) {
        return pcc_gc_backend4_medium_page_limit_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_DEFER_LIMIT_BYTES) {
        return pcc_gc_backend4_large_defer_limit_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_RECONSIDERATIONS) {
        return pcc_gc_backend4_large_object_reconsiderations();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_YOUNG_OBJECTS) {
        return pcc_gc_backend4_young_object_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_OLD_OBJECTS) {
        return pcc_gc_backend4_old_object_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_YOUNG_BYTES) {
        return pcc_gc_backend4_young_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_OLD_BYTES) {
        return pcc_gc_backend4_old_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_OBJECTS) {
        return pcc_gc_backend4_small_page_object_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_OBJECTS) {
        return pcc_gc_backend4_medium_page_object_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_PAGE_OBJECTS) {
        return pcc_gc_backend4_large_page_object_count();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_SMALL_PAGE_BYTES) {
        return pcc_gc_backend4_small_page_live_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_BYTES) {
        return pcc_gc_backend4_medium_page_live_bytes();
    }
    if (metric == PCC_GC_COUNTER_GENZGC_LARGE_PAGE_BYTES) {
        return pcc_gc_backend4_large_page_live_bytes();
    }
    if (metric == PCC_GC_COUNTER_CMS_WORKBUFFER_SCORE) {
        return __atomic_load_n(
            &pcc_gc_cms_queue_pushes, __ATOMIC_RELAXED
        );
    }
    if (metric == PCC_GC_COUNTER_CMS_PRODUCTION_SCORE) {
        return __atomic_load_n(
            &pcc_gc_cms_queue_pushes, __ATOMIC_RELAXED
        ) + __atomic_load_n(&pcc_gc_cms_worker_starts, __ATOMIC_RELAXED);
    }
    if (metric == PCC_GC_COUNTER_GEN_MINOR_PRODUCTIVITY_SCORE) {
        return __atomic_load_n(
            &pcc_gc_minor_arena_refills, __ATOMIC_RELAXED
        ) + __atomic_load_n(&pcc_gc_minor_arena_bumps, __ATOMIC_RELAXED);
    }
    if (metric == PCC_GC_COUNTER_GEN_REMEMBERED_UPDATE_SCORE) {
        return __atomic_load_n(
            &pcc_gc_minor_arena_refills, __ATOMIC_RELAXED
        ) + __atomic_load_n(&pcc_gc_minor_arena_bumps, __ATOMIC_RELAXED);
    }
    return pcc_gc_metric_load(metric);
}

static void pcc_gc_backend4_clear_large_deferred_flags(void) {
    pcc_gc_graph_lock();
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (o == NULL || PY_IS_TAGGED_INT(o)) continue;
        PyObjectHeader *h = py_header(o);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_LARGE_DEFERRED) != 0) {
            __atomic_add_fetch(
                &pcc_gc_backend4_large_object_reconsiderations_count,
                1,
                __ATOMIC_RELAXED
            );
            py_header_flags_and(h, ~PY_FLAG_GC_LARGE_DEFERRED);
        }
    }
    pcc_gc_graph_unlock();
}

static void pcc_gc_backend4_reseed_relocation_epoch_state(void) {
    if (pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING) return;
    pcc_gc_graph_lock();
    int64_t candidates = 0;
    int64_t candidate_bytes = 0;
    int64_t small_candidates = 0;
    int64_t medium_candidates = 0;
    int64_t small_bytes = 0;
    int64_t medium_bytes = 0;
    int64_t zpage_bytes = 0;
    int64_t small_zpage_bytes = 0;
    int64_t medium_zpage_bytes = 0;
    pcc_gc_backend4_evacuation_page_clear_unlocked();
    for (
        PccGcRelocationNode *n = pcc_gc_relocation_set;
        n != NULL;
        n = n->next
    ) {
        int64_t size = pcc_gc_known_object_size_unlocked(n->obj);
        if (size <= 0) continue;
        candidates++;
        candidate_bytes += size;
        if (size <= PCC_GC_BACKEND4_SMALL_PAGE_LIMIT) {
            small_candidates++;
            small_bytes += size;
        } else if (size <= PCC_GC_BACKEND4_MEDIUM_PAGE_LIMIT) {
            medium_candidates++;
            medium_bytes += size;
        }
    }
    for (
        PccGcZPage *page = pcc_gc_backend4_pages;
        page != NULL;
        page = page->next
    ) {
        if (!pcc_gc_backend4_relocation_set_contains_page_unlocked(page)) {
            continue;
        }
        pcc_gc_backend4_evacuation_page_add_unlocked(page);
        int64_t page_bytes = page->used_bytes;
        if (page_bytes <= 0) continue;
        zpage_bytes += page_bytes;
        if (page->page_class == 0) {
            small_zpage_bytes += page_bytes;
        } else if (page->page_class == 1) {
            medium_zpage_bytes += page_bytes;
        }
    }
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidates,
        candidates,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_bytes_count,
        candidate_bytes,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidates,
        small_candidates,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidates,
        medium_candidates,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_bytes_count,
        small_bytes,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_bytes_count,
        medium_bytes,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,
        zpage_bytes,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_zpage_bytes_count,
        small_zpage_bytes,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_zpage_bytes_count,
        medium_zpage_bytes,
        __ATOMIC_RELAXED
    );
    pcc_gc_graph_unlock();
}

void pcc_gc_telemetry_reset(void) {
    pcc_gc_init_config();
    for (int i = 0; i <= PCC_GC_COUNTER_WORK_STEPS; i++) {
        __atomic_store_n(&pcc_gc_metrics[i], 0, __ATOMIC_RELAXED);
    }
    __atomic_store_n(&pcc_gc_max_pause_us, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_allocations, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_collections, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_bytes, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_arena_refills, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_arena_bumps, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_minor_arena_fallbacks, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_queue_pushes, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_worker_drains, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_mutator_assists, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_worker_traces, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_worker_stops, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&pcc_gc_cms_wb_flushes, 0, __ATOMIC_RELAXED);
    pcc_gc_relocation_forwards = 0;
    pcc_gc_relocation_barrier_forwards = 0;
    pcc_gc_relocation_pin_rejects = 0;
    __atomic_store_n(
        &pcc_gc_backend4_genzgc_store_barriers, 0, __ATOMIC_RELAXED
    );
    pcc_gc_backend4_reset_store_buffer_epoch_state();
    __atomic_store_n(
        &pcc_gc_backend4_young_promotions, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuated_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_large_object_defers, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_large_object_deferred_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_large_object_reconsiderations_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidates, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_bytes_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_small_page_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_medium_page_candidate_zpage_bytes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_drain_batches_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_drained_entries_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_duplicate_skips_count, 0, __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_incomplete_drains_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_evacuation_incomplete_batches_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_max_batch_size_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_full_batches_count,
        0,
        __ATOMIC_RELAXED
    );
    pcc_gc_backend4_reset_remembered_set_epoch_state();
    __atomic_store_n(
        &pcc_gc_backend4_remembered_set_duplicate_skips_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_medium_flushes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_medium_flushed_entries_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_medium_full_flushes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count,
        0,
        __ATOMIC_RELAXED
    );
    __atomic_store_n(
        &pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count,
        0,
        __ATOMIC_RELAXED
    );
    pcc_gc_backend4_reseed_relocation_epoch_state();
    pcc_gc_backend4_clear_large_deferred_flags();
}

int64_t pcc_gc_backend2_worker_buffer_score(void) {
    return __atomic_load_n(&pcc_gc_cms_queue_pushes, __ATOMIC_RELAXED);
}

int64_t pcc_gc_backend2_production_score(void) {
    return pcc_gc_backend2_worker_buffer_score()
        + __atomic_load_n(&pcc_gc_cms_worker_starts, __ATOMIC_RELAXED);
}

int64_t pcc_gc_backend3_minor_productivity_score(void) {
    return __atomic_load_n(&pcc_gc_minor_arena_refills, __ATOMIC_RELAXED)
        + __atomic_load_n(&pcc_gc_minor_arena_bumps, __ATOMIC_RELAXED);
}

int64_t pcc_gc_backend3_remembered_update_score(void) {
    return pcc_gc_backend3_minor_productivity_score();
}

static int pcc_gc_is_tracing_backend(void) {
    return pcc_gc_selected_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP;
}

static int pcc_gc_tracks_objects(void) {
    return pcc_gc_selected_backend != PCC_GC_KIND_REFCOUNT_CYCLE;
}

static int pcc_gc_is_known_object(PyObject *o) {
    if (!pcc_gc_tracks_objects()) return 0;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (n->obj == o && !pcc_gc_object_node_is_freeing(n)) return 1;
    }
    return 0;
}

int64_t pcc_gc_object_is_known_no_lock(PyObject *obj) {
    return pcc_gc_is_known_object(obj) ? 1 : 0;
}

int64_t pcc_gc_object_is_known(PyObject *obj) {
    pcc_gc_graph_lock();
    int64_t known = pcc_gc_is_known_object(obj) ? 1 : 0;
    pcc_gc_graph_unlock();
    return known;
}

static int pcc_gc_is_sweep_candidate(PyObject *o) {
    if (!pcc_gc_tracks_objects()) return 0;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    if (!pcc_gc_is_known_object(o)) return 0;
    PyObjectHeader *h = py_header(o);
    return (py_header_flags_load(h) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0;
}

static int pcc_gc_has_sweep_candidate_unlocked(void) {
    if (!pcc_gc_tracks_objects()) return 0;
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (
            (py_header_flags_load(py_header(o)) & PY_FLAG_GC_SWEEP_CANDIDATE)
            != 0
        ) {
            return 1;
        }
    }
    return 0;
}

static int pcc_gc_has_sweep_candidate(void) {
    pcc_gc_graph_lock();
    int has_candidate = pcc_gc_has_sweep_candidate_unlocked();
    pcc_gc_graph_unlock();
    return has_candidate;
}

static void pcc_gc_clear_slot(PyObject **slot) {
    if (slot == NULL) return;
    PyObject *child = *slot;
    *slot = NULL;
    if (child == NULL || PY_IS_TAGGED_INT(child)) return;
    if (pcc_gc_is_sweep_candidate(child)) return;
    py_decref(child);
}

static void pcc_gc_clear_referents(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)o;
        for (int64_t i = 0; i < l->length; i++) {
            pcc_gc_clear_slot(&l->items[i]);
        }
        l->length = 0;
    } else if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)o;
        for (int64_t i = 0; i < t->len; i++) {
            pcc_gc_clear_slot(&t->items[i]);
        }
        t->len = 0;
    } else if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)o;
        if (d->entries != NULL) {
            for (int64_t i = 0; i < d->entries_used; i++) {
                DictEntry *e = &d->entries[i];
                if (e->key != NULL) {
                    pcc_gc_clear_slot(&e->key);
                    pcc_gc_clear_slot(&e->value);
                    e->hash = 0;
                }
            }
        }
        if (d->indices != NULL) {
            for (int64_t i = 0; i < d->capacity; i++) {
                d->indices[i] = PY_DICT_EMPTY;
            }
        }
        d->size = 0;
        d->entries_used = 0;
    } else if (tag == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)o;
        if (s->entries != NULL) {
            for (int64_t i = 0; i < s->capacity; i++) {
                PyObject *key = s->entries[i].key;
                if (key != NULL && key != py_set_dummy) {
                    s->entries[i].key = NULL;
                    if (!pcc_gc_is_sweep_candidate(key)) py_decref(key);
                } else {
                    s->entries[i].key = NULL;
                }
                s->entries[i].hash = 0;
            }
        }
        s->size = 0;
        s->fill = 0;
    } else if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        pcc_gc_clear_slot(&f->captures);
        pcc_gc_clear_slot(&f->self_obj);
    } else if (tag == PY_TYPE_ITER) {
        PyIterObject *it = (PyIterObject *)o;
        pcc_gc_clear_slot(&it->seq);
    } else if (tag == PY_TYPE_GEN) {
        PyGenObject *g = (PyGenObject *)o;
        pcc_gc_clear_slot(&g->frame);
        pcc_gc_clear_slot(&g->send_value);
    } else if (tag == PY_TYPE_COROUTINE) {
        PccGcCoroutineObject *c = (PccGcCoroutineObject *)o;
        pcc_gc_clear_slot(&c->captures);
        pcc_gc_clear_slot(&c->args);
        pcc_gc_clear_slot(&c->result);
    } else if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *c = (PyContinuationObject *)o;
        PyContinuationStackChunk *chunk = c->stack_chunk;
        if (chunk != NULL && chunk->slots != NULL) {
            for (int64_t i = 0; i < chunk->slot_count; i++) {
                pcc_gc_clear_slot(&chunk->slots[i]);
            }
        }
    } else if (tag == PY_TYPE_TASK) {
        PyTaskObject *t = (PyTaskObject *)o;
        pcc_gc_clear_slot(&t->coro);
        pcc_gc_clear_slot(&t->result);
        pcc_gc_clear_slot(&t->waiter);
    } else if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *t = (PyVirtualThreadObject *)o;
        pcc_gc_clear_slot(&t->continuation);
        pcc_gc_clear_slot(&t->result);
    } else if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        pcc_gc_clear_slot((PyObject **)&e->exc_class);
        pcc_gc_clear_slot(&e->message);
        pcc_gc_clear_slot(&e->cause);
        pcc_gc_clear_slot(&e->context);
    } else if (tag == PY_TYPE_PROPERTY) {
        PyPropertyObject *p = (PyPropertyObject *)o;
        pcc_gc_clear_slot(&p->fget);
        pcc_gc_clear_slot(&p->fset);
        pcc_gc_clear_slot(&p->fdel);
    } else if (tag == PY_TYPE_CLASSMETHOD) {
        PyClassMethodObject *m = (PyClassMethodObject *)o;
        pcc_gc_clear_slot(&m->func);
    } else if (tag == PY_TYPE_STATICMETHOD) {
        PyStaticMethodObject *m = (PyStaticMethodObject *)o;
        pcc_gc_clear_slot(&m->func);
    } else if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *mv = (PyMemoryViewObject *)o;
        pcc_gc_clear_slot(&mv->base);
    } else if (tag == PY_TYPE_CLASS) {
        PyClassObject *cls = (PyClassObject *)o;
        pcc_gc_clear_slot(&cls->attrs);
    } else if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = inst->cls;
        if (cls != NULL) {
            int32_t n_fields = cls->n_fields;
            if (n_fields < 0) n_fields = 0;
            for (int32_t i = 0; i < n_fields; i++) {
                pcc_gc_clear_slot(&inst->fields[i]);
            }
            if ((cls->h.flags & 2) == 0) {
                pcc_gc_clear_slot(&inst->fields[n_fields]);
            }
        }
    } else if (tag == PY_TYPE_WEAKREF) {
        PyWeakRefObject *wr = (PyWeakRefObject *)o;
        pcc_gc_clear_slot(&wr->callback);
    } else if (tag == PY_TYPE_THREAD) {
        PccGcThreadObject *t = (PccGcThreadObject *)o;
        pcc_gc_clear_slot(&t->callable);
        pcc_gc_clear_slot(&t->args);
        pcc_gc_clear_slot(&t->result);
    }
}

/* PASS-1 of the two-phase sweep: invalidate weakrefs and clear referent slots
 * WITHOUT freeing or clearing the sweep-candidate flag, so every pending object
 * stays a recognizable sweep candidate while cycles are broken. Mirror of the
 * pcc-Python port _clear_unreachable. See investigation
 * gc-5backend-object-lifetime-contract-no-libpython.md. */
static void pcc_gc_clear_unreachable(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_weakref_invalidate(o);
    pcc_gc_clear_referents(o);
}

/* PASS-2 of the two-phase sweep: free an object whose referents were ALREADY
 * cleared by pcc_gc_clear_unreachable. Must not run before every pending
 * object has been cleared. */
static void pcc_gc_finalize_unreachable(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    PyObjectHeader *h = py_header(o);
    pcc_gc_note_object_freeing(o);
    pcc_refcount_forget(&h->refcount);
    py_gc_untrack(o);
    h->refcount = 0;
    switch (h->type_tag) {
        case PY_TYPE_INT:       py_dealloc_int(o);       break;
        case PY_TYPE_FLOAT:     py_dealloc_float(o);     break;
        case PY_TYPE_STR:       py_dealloc_str(o);       break;
        case PY_TYPE_LIST:      py_dealloc_list(o);      break;
        case PY_TYPE_TUPLE:     py_dealloc_tuple(o);     break;
        case PY_TYPE_DICT:      py_dealloc_dict(o);      break;
        case PY_TYPE_SET:       py_dealloc_set(o);       break;
        case PY_TYPE_FUNC:      py_dealloc_func(o);      break;
        case PY_TYPE_CLASS:     py_class_dealloc(o);     break;
        case PY_TYPE_INSTANCE:  py_instance_dealloc(o);  break;
        case PY_TYPE_EXC:       py_dealloc_exc(o);       break;
        case PY_TYPE_ITER:      py_dealloc_iter(o);      break;
        case PY_TYPE_GEN:       py_dealloc_gen(o);       break;
        case PY_TYPE_COROUTINE: py_dealloc_coroutine(o); break;
        case PY_TYPE_CONTINUATION: py_dealloc_continuation(o); break;
        case PY_TYPE_TASK:      py_dealloc_task(o);      break;
        case PY_TYPE_VIRTUAL_THREAD: py_dealloc_virtual_thread(o); break;
        case PY_TYPE_MEMORYVIEW: py_dealloc_memoryview(o); break;
        case PY_TYPE_WEAKREF:   py_dealloc_weakref(o);   break;
        case PY_TYPE_THREAD_LOCK: py_dealloc_thread_lock(o); break;
        case PY_TYPE_THREAD_RLOCK: py_dealloc_thread_rlock(o); break;
        case PY_TYPE_THREAD_EVENT: py_dealloc_thread_event(o); break;
        case PY_TYPE_THREAD_CONDITION: py_dealloc_thread_condition(o); break;
        case PY_TYPE_THREAD_SEMAPHORE: py_dealloc_thread_semaphore(o); break;
        case PY_TYPE_THREAD: py_dealloc_thread_thread(o); break;
        default:
            if (h->type_tag >= PY_TYPE_USER_CLASS_START) py_instance_dealloc(o);
            else py_dealloc_generic(o);
            break;
    }
}

static void pcc_gc_recheck_reachability_after_finalizers(void);

static int64_t pcc_gc_sweep_unreachable(int64_t budget) {
    if (budget <= 0) return 0;
    /* PASS 0 (CPython PEP 442), mirror of the port: run __del__ on unreachable
     * members BEFORE any clear/free, while their fields are intact.
     * py_user_del_dispatch runs __del__ at most once and sets PY_FLAG_FINALIZED,
     * so the PASS-2 dealloc does not re-run it. See investigation
     * gc-5backend-cycle-finalizer-not-run-no-libpython.md. */
    for (PccGcObjectNode *fn = pcc_gc_objects; fn != NULL; fn = fn->next) {
        if (!pcc_gc_object_node_is_active(fn)) continue;
        PyObject *o = fn->obj;
        int32_t flags = py_header_flags_load(py_header(o));
        if ((flags & PY_FLAG_GC_SWEEP_CANDIDATE) != 0 &&
            (flags & (PY_FLAG_GC_PINNED | PY_FLAG_GC_FRESH_ALLOC)) == 0) {
            py_user_del_dispatch(o);
        }
    }
    /* PEP 442: after finalizers, exclude any object a __del__ resurrected from
     * the clear/free passes below. Mirror of the pcc-Python port. */
    pcc_gc_recheck_reachability_after_finalizers();
    /* Two-phase sweep (clear-then-free), mirror of the pcc-Python port. PASS 1
     * clears the referents of up to `budget` unreachable objects WITHOUT
     * freeing any, so every still-pending object keeps PY_FLAG_GC_SWEEP_CANDIDATE
     * and pcc_gc_clear_slot's candidate guard skips the decref of sibling cycle
     * members. PASS 2 frees the SAME objects (same list order + flag filter,
     * flags unchanged by PASS 1). Interleaved clear+free (the old single pass)
     * caused a use-after-free / refcount underflow when one unreachable object
     * referenced another that had already been finalized. See investigation
     * gc-5backend-object-lifetime-contract-no-libpython.md. */
    int64_t cleared = 0;
    PccGcObjectNode *n = pcc_gc_objects;
    while (n != NULL && cleared < budget) {
        PccGcObjectNode *next = n->next;
        if (pcc_gc_object_node_is_active(n)) {
            PyObject *o = n->obj;
            int32_t flags = py_header_flags_load(py_header(o));
            if ((flags & PY_FLAG_GC_SWEEP_CANDIDATE) != 0 &&
                (flags & (PY_FLAG_GC_PINNED | PY_FLAG_GC_FRESH_ALLOC)) == 0) {
                pcc_gc_clear_unreachable(o);
                cleared++;
            }
        }
        n = next;
    }
    int64_t reclaimed = 0;
    n = pcc_gc_objects;
    while (n != NULL) {
        PccGcObjectNode *next = n->next;
        if (pcc_gc_object_node_is_active(n)) {
            PyObject *o = n->obj;
            PyObjectHeader *h = py_header(o);
            int32_t flags = py_header_flags_load(h);
            if ((flags & PY_FLAG_GC_SWEEP_CANDIDATE) != 0) {
                if ((flags & (PY_FLAG_GC_PINNED | PY_FLAG_GC_FRESH_ALLOC)) != 0) {
                    py_header_flags_and(h, ~PY_FLAG_GC_SWEEP_CANDIDATE);
                } else if (reclaimed < cleared) {
                    pcc_gc_finalize_unreachable(o);
                    reclaimed++;
                }
            }
        }
        n = next;
    }
    return reclaimed;
}

static void pcc_gc_gray_object(PyObject *o) {
    if (o != NULL && !PY_IS_TAGGED_INT(o)) {
        PccGcForwardNode *forwarding = pcc_gc_forwarding_find(o);
        if (forwarding != NULL && forwarding->to != NULL) {
            o = forwarding->to;
        }
    }
    if (!pcc_gc_is_known_object(o)) return;
    PyObjectHeader *h = py_header(o);
    if ((py_header_flags_load(h) & PY_FLAG_GC_BLACK) != 0) return;
    py_header_flags_update(h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_GRAY);
}

static void pcc_gc_gray_root_object(PyObject *o) {
    if (o != NULL && !PY_IS_TAGGED_INT(o)) {
        PccGcForwardNode *forwarding = pcc_gc_forwarding_find(o);
        if (forwarding != NULL && forwarding->to != NULL) {
            o = forwarding->to;
        }
    }
    if (!pcc_gc_is_known_object(o)) return;
    PyObjectHeader *h = py_header(o);
    py_header_flags_update(h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_GRAY);
}

static PyObject *pcc_gc_resolve_root_slot_unlocked(PyObject **slot) {
    if (slot == NULL) return NULL;
    PyObject *value = *slot;
    if (value == NULL || PY_IS_TAGGED_INT(value)) return value;
    if (!pcc_gc_is_known_object(value)) return value;
    PccGcForwardNode *forwarding = pcc_gc_forwarding_find(value);
    py_header_flags_and(py_header(value), ~PY_FLAG_GC_RELOCATION_CANDIDATE);
    if (forwarding == NULL || forwarding->to == NULL) return value;
    PyObject *resolved = forwarding->to;
    if (resolved == value) return value;
    py_incref(resolved);
    *slot = resolved;
    py_decref(value);
    return resolved;
}

static void pcc_gc_mark_forwarded_source_inactive(PyObject *from) {
    if (from == NULL || PY_IS_TAGGED_INT(from)) return;
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (n->obj != from) continue;
        if (pcc_gc_object_node_is_freeing(n)) return;
        if (n->size > 0) pcc_gc_live_bytes_subtract(n->size);
        pcc_gc_backend4_zpage_remove_unlocked(from);
        pcc_gc_object_node_set_freeing(n);
        return;
    }
}

static PyObject *pcc_gc_generational_oldify_copy(PyObject *from) {
    if (
        pcc_gc_selected_backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || from == NULL
        || PY_IS_TAGGED_INT(from)
    ) {
        return NULL;
    }
    PccGcForwardNode *existing = pcc_gc_forwarding_find(from);
    if (existing != NULL) {
        return existing->to;
    }
    if (!pcc_gc_is_known_object(from)) return NULL;

    PyObjectHeader *from_h = py_header(from);
    int32_t from_flags = py_header_flags_load(from_h);
    if ((from_flags & PY_FLAG_GC_YOUNG) == 0) return NULL;
    if ((from_flags & PY_FLAG_GC_PINNED) != 0) return NULL;
    if (!pcc_gc_relocate_copy_supported_tag(from_h->type_tag)) return NULL;

    int64_t size = pcc_gc_known_object_size(from);
    if (size < (int64_t)sizeof(PyObjectHeader)) return NULL;

    PyObject *to = (PyObject *)calloc(1, (size_t)size);
    if (to == NULL) return NULL;
    memcpy(to, from, (size_t)size);

    PyObjectHeader *to_h = py_header(to);
    to_h->refcount = 0;
    to_h->flags = (
        to_h->flags
        & ~(
            PY_FLAG_GC_YOUNG
            | PY_FLAG_GC_MINOR_ARENA
            | PY_FLAG_GC_REMEMBERED
            | PY_FLAG_GC_RELOCATION_CANDIDATE
        )
    ) | PY_FLAG_GC_OLD;

    PccGcObjectNode *n = (PccGcObjectNode *)calloc(
        1, sizeof(PccGcObjectNode)
    );
    if (n == NULL) {
        free(to);
        return NULL;
    }
    n->obj = to;
    n->size = size;
    n->minor_block = NULL;
    n->next = pcc_gc_objects;
    pcc_gc_objects = n;
    __atomic_add_fetch(&pcc_gc_live_bytes, size, __ATOMIC_ACQ_REL);

    if (pcc_gc_install_forwarding(from, to) != 0) {
        PccGcObjectNode **cur = &pcc_gc_objects;
        while (*cur != NULL) {
            if (*cur == n) {
                *cur = n->next;
                break;
            }
            cur = &(*cur)->next;
        }
        pcc_gc_live_bytes_subtract(size);
        pcc_gc_identity_remove(to);
        free(n);
        free(to);
        return NULL;
    }

    pcc_gc_mark_forwarded_source_inactive(from);
    py_header_flags_update(from_h, PY_FLAG_GC_YOUNG, PY_FLAG_GC_OLD);
    return to;
}

static void pcc_gc_promote_young_object(PyObject *o) {
    if (!pcc_gc_is_known_object(o)) return;
    PyObjectHeader *h = py_header(o);
    if ((py_header_flags_load(h) & PY_FLAG_GC_YOUNG) != 0) {
        if (pcc_gc_generational_oldify_copy(o) != NULL) return;
        py_header_flags_update(h, PY_FLAG_GC_YOUNG, PY_FLAG_GC_OLD);
        if (pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING) {
            pcc_gc_backend4_zpage_note_owner_promoted_unlocked(o);
        }
    }
}

static void pcc_gc_promote_young_slot(PyObject **slot) {
    if (slot == NULL) return;
    PyObject *child = *slot;
    if (child == NULL || PY_IS_TAGGED_INT(child)) return;
    PyObject *oldified = pcc_gc_generational_oldify_copy(child);
    if (oldified != NULL && oldified != child) {
        py_incref(oldified);
        *slot = oldified;
        py_decref(child);
        return;
    }
    pcc_gc_promote_young_object(child);
}

static void pcc_gc_promote_young_borrowed_slot(PyObject **slot) {
    if (slot == NULL) return;
    PyObject *child = *slot;
    if (child == NULL || PY_IS_TAGGED_INT(child)) return;
    PyObject *oldified = pcc_gc_generational_oldify_copy(child);
    if (oldified != NULL && oldified != child) {
        *slot = oldified;
        return;
    }
    pcc_gc_promote_young_object(child);
}

static void pcc_gc_trace_referents(
    PyObject *o,
    void (*visit)(PyObject *child)
);
static int64_t pcc_gc_root_slot_count_from_map(const int32_t *frame_map);

static void pcc_gc_promote_frame_roots(int64_t budget) {
    if (budget <= 0) return;
    for (
        PccGcFrameNode *f = pcc_gc_frames;
        f != NULL;
        f = f->next
    ) {
        int64_t n_slots = pcc_gc_root_slot_count_from_map(f->frame_map);
        if (n_slots <= 0 || f->slots == NULL) continue;
        for (int64_t i = 0; i < n_slots; i++) {
            pcc_gc_promote_young_slot(&f->slots[i]);
        }
    }
    for (
        PccGcContinuationRootNode *c = pcc_gc_continuation_roots;
        c != NULL;
        c = c->next
    ) {
        int64_t n_slots = pcc_gc_root_slot_count_from_map(c->frame_map);
        if (n_slots <= 0 || c->slots == NULL) continue;
        for (int64_t i = 0; i < n_slots; i++) {
            pcc_gc_promote_young_slot(&c->slots[i]);
        }
    }
}

static void pcc_gc_promote_scheduler_roots(int64_t budget) {
    if (budget <= 0) return;
    for (
        PccGcSchedulerRootNode *r = pcc_gc_scheduler_roots;
        r != NULL;
        r = r->next
    ) {
        if (r->slot == NULL) continue;
        pcc_gc_promote_young_slot(r->slot);
    }
}

static void pcc_gc_promote_extension_module_state_root(
    PyObject *root,
    void *ctx
) {
    (void)ctx;
    pcc_gc_promote_young_object(root);
}

static void pcc_gc_promote_remembered_owner_referents(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)o;
        for (int64_t i = 0; i < l->length; i++) {
            pcc_gc_promote_young_slot(&l->items[i]);
        }
        return;
    } else if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)o;
        for (int64_t i = 0; i < t->len; i++) {
            pcc_gc_promote_young_slot(&t->items[i]);
        }
        return;
    } else if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)o;
        if (d->entries != NULL) {
            for (int64_t i = 0; i < d->entries_used; i++) {
                DictEntry *e = &d->entries[i];
                if (e->key != NULL) {
                    pcc_gc_promote_young_slot(&e->key);
                    pcc_gc_promote_young_slot(&e->value);
                }
            }
        }
        return;
    } else if (tag == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)o;
        if (s->entries != NULL) {
            for (int64_t i = 0; i < s->capacity; i++) {
                PyObject *k = s->entries[i].key;
                if (k != NULL && k != py_set_dummy) {
                    pcc_gc_promote_young_slot(&s->entries[i].key);
                }
            }
        }
        return;
    } else if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        pcc_gc_promote_young_slot(&f->captures);
        pcc_gc_promote_young_slot(&f->self_obj);
        return;
    } else if (tag == PY_TYPE_ITER) {
        PyIterObject *it = (PyIterObject *)o;
        pcc_gc_promote_young_slot(&it->seq);
        return;
    } else if (tag == PY_TYPE_GEN) {
        PyGenObject *g = (PyGenObject *)o;
        pcc_gc_promote_young_slot(&g->frame);
        pcc_gc_promote_young_slot(&g->send_value);
        return;
    } else if (tag == PY_TYPE_COROUTINE) {
        PccGcCoroutineObject *c = (PccGcCoroutineObject *)o;
        pcc_gc_promote_young_slot(&c->captures);
        pcc_gc_promote_young_slot(&c->args);
        pcc_gc_promote_young_slot(&c->result);
        return;
    } else if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *c = (PyContinuationObject *)o;
        PyContinuationStackChunk *chunk = c->stack_chunk;
        if (chunk != NULL && chunk->slots != NULL) {
            for (int64_t i = 0; i < chunk->slot_count; i++) {
                pcc_gc_promote_young_slot(&chunk->slots[i]);
            }
        }
        return;
    } else if (tag == PY_TYPE_TASK) {
        PyTaskObject *t = (PyTaskObject *)o;
        pcc_gc_promote_young_slot(&t->coro);
        pcc_gc_promote_young_slot(&t->result);
        pcc_gc_promote_young_slot(&t->waiter);
        return;
    } else if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *t = (PyVirtualThreadObject *)o;
        pcc_gc_promote_young_slot(&t->continuation);
        pcc_gc_promote_young_slot(&t->result);
        return;
    } else if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        pcc_gc_promote_young_slot((PyObject **)&e->exc_class);
        pcc_gc_promote_young_slot(&e->message);
        pcc_gc_promote_young_slot(&e->cause);
        pcc_gc_promote_young_slot(&e->context);
        return;
    } else if (tag == PY_TYPE_PROPERTY) {
        PyPropertyObject *p = (PyPropertyObject *)o;
        pcc_gc_promote_young_slot(&p->fget);
        pcc_gc_promote_young_slot(&p->fset);
        pcc_gc_promote_young_slot(&p->fdel);
        return;
    } else if (tag == PY_TYPE_CLASSMETHOD) {
        PyClassMethodObject *m = (PyClassMethodObject *)o;
        pcc_gc_promote_young_slot(&m->func);
        return;
    } else if (tag == PY_TYPE_STATICMETHOD) {
        PyStaticMethodObject *m = (PyStaticMethodObject *)o;
        pcc_gc_promote_young_slot(&m->func);
        return;
    } else if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *mv = (PyMemoryViewObject *)o;
        pcc_gc_promote_young_slot(&mv->base);
        return;
    } else if (tag == PY_TYPE_CLASS) {
        PyClassObject *cls = (PyClassObject *)o;
        if (cls->bases != NULL) {
            for (int32_t i = 0; i < cls->n_bases; i++) {
                pcc_gc_promote_young_borrowed_slot(
                    (PyObject **)&cls->bases[i]
                );
            }
        }
        if (cls->mro != NULL) {
            for (int32_t i = 0; i < cls->n_mro; i++) {
                pcc_gc_promote_young_borrowed_slot(
                    (PyObject **)&cls->mro[i]
                );
            }
        }
        if (cls->methods != NULL) {
            for (int32_t i = 0; i < cls->n_methods; i++) {
                pcc_gc_promote_young_borrowed_slot(&cls->methods[i].func);
            }
        }
        pcc_gc_promote_young_borrowed_slot(&cls->del_method);
        pcc_gc_promote_young_slot(&cls->attrs);
        pcc_gc_promote_young_borrowed_slot((PyObject **)&cls->metaclass);
        return;
    } else if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = inst->cls;
        if (cls != NULL) {
            int32_t n_fields = cls->n_fields;
            if (n_fields < 0) n_fields = 0;
            for (int32_t i = 0; i < n_fields; i++) {
                pcc_gc_promote_young_slot(&inst->fields[i]);
            }
            if ((cls->h.flags & 2) == 0) {
                pcc_gc_promote_young_slot(&inst->fields[n_fields]);
            }
        }
        return;
    } else if (tag == PY_TYPE_WEAKREF) {
        PyWeakRefObject *wr = (PyWeakRefObject *)o;
        pcc_gc_promote_young_slot(&wr->callback);
        return;
    } else if (tag == PY_TYPE_THREAD) {
        PccGcThreadObject *t = (PccGcThreadObject *)o;
        pcc_gc_promote_young_slot(&t->callable);
        pcc_gc_promote_young_slot(&t->args);
        pcc_gc_promote_young_slot(&t->result);
        return;
    }
    pcc_gc_trace_referents(o, pcc_gc_promote_young_object);
}

static void pcc_gc_trace_referents(
    PyObject *o,
    void (*visit)(PyObject *child)
) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)o;
        for (int64_t i = 0; i < l->length; i++) visit(l->items[i]);
    } else if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)o;
        for (int64_t i = 0; i < t->len; i++) visit(t->items[i]);
    } else if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)o;
        if (d->entries != NULL) {
            for (int64_t i = 0; i < d->entries_used; i++) {
                DictEntry *e = &d->entries[i];
                if (e->key != NULL) {
                    visit(e->key);
                    visit(e->value);
                }
            }
        }
    } else if (tag == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)o;
        if (s->entries != NULL) {
            for (int64_t i = 0; i < s->capacity; i++) {
                PyObject *k = s->entries[i].key;
                if (k != NULL && k != py_set_dummy) visit(k);
            }
        }
    } else if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        visit(f->captures);
        visit(f->self_obj);
    } else if (tag == PY_TYPE_CLASS) {
        PyClassObject *cls = (PyClassObject *)o;
        if (cls->bases != NULL) {
            for (int32_t i = 0; i < cls->n_bases; i++) {
                visit((PyObject *)cls->bases[i]);
            }
        }
        if (cls->mro != NULL) {
            for (int32_t i = 0; i < cls->n_mro; i++) {
                visit((PyObject *)cls->mro[i]);
            }
        }
        visit(cls->attrs);
        visit((PyObject *)cls->metaclass);
    } else if (tag == PY_TYPE_ITER) {
        PyIterObject *it = (PyIterObject *)o;
        visit(it->seq);
    } else if (tag == PY_TYPE_GEN) {
        PyGenObject *g = (PyGenObject *)o;
        visit(g->frame);
        visit(g->send_value);
    } else if (tag == PY_TYPE_COROUTINE) {
        PccGcCoroutineObject *c = (PccGcCoroutineObject *)o;
        visit(c->captures);
        visit(c->args);
        visit(c->result);
    } else if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *c = (PyContinuationObject *)o;
        PyContinuationStackChunk *chunk = c->stack_chunk;
        if (chunk != NULL && chunk->slots != NULL) {
            for (int64_t i = 0; i < chunk->slot_count; i++) {
                visit(chunk->slots[i]);
            }
        }
    } else if (tag == PY_TYPE_TASK) {
        PyTaskObject *t = (PyTaskObject *)o;
        visit(t->coro);
        visit(t->result);
        visit(t->waiter);
    } else if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *t = (PyVirtualThreadObject *)o;
        visit(t->continuation);
        visit(t->result);
    } else if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        visit((PyObject *)e->exc_class);
        visit(e->message);
        visit(e->cause);
        visit(e->context);
    } else if (tag == PY_TYPE_PROPERTY) {
        PyPropertyObject *p = (PyPropertyObject *)o;
        visit(p->fget);
        visit(p->fset);
        visit(p->fdel);
    } else if (tag == PY_TYPE_CLASSMETHOD) {
        PyClassMethodObject *m = (PyClassMethodObject *)o;
        visit(m->func);
    } else if (tag == PY_TYPE_STATICMETHOD) {
        PyStaticMethodObject *m = (PyStaticMethodObject *)o;
        visit(m->func);
    } else if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *mv = (PyMemoryViewObject *)o;
        visit(mv->base);
    } else if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = inst->cls;
        if (cls != NULL) {
            visit((PyObject *)cls);
            int32_t n_fields = cls->n_fields;
            if (n_fields < 0) n_fields = 0;
            for (int32_t i = 0; i < n_fields; i++) visit(inst->fields[i]);
            if ((cls->h.flags & 2) == 0) visit(inst->fields[n_fields]);
        }
    } else if (tag == PY_TYPE_WEAKREF) {
        PyWeakRefObject *wr = (PyWeakRefObject *)o;
        visit(wr->callback);
    } else if (tag == PY_TYPE_THREAD) {
        PccGcThreadObject *t = (PccGcThreadObject *)o;
        visit(t->callable);
        visit(t->args);
        visit(t->result);
    }
}

static int64_t pcc_gc_cms_trace_gray_object_unlocked(PyObject *o) {
    if (pcc_gc_mark_active_load() == 0) return 0;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    if (!pcc_gc_is_known_object(o)) return 0;
    PyObjectHeader *h = py_header(o);
    if ((py_header_flags_load(h) & PY_FLAG_GC_GRAY) == 0) return 0;
    pcc_gc_trace_referents(o, pcc_gc_gray_object);
    py_header_flags_update(h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_BLACK);
    return 1;
}

static int pcc_gc_gray_exists(void) {
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        if (
            (py_header_flags_load(py_header(n->obj)) & PY_FLAG_GC_GRAY)
            != 0
        ) return 1;
    }
    return 0;
}

static int64_t pcc_gc_root_slot_count_from_map(const int32_t *frame_map) {
    if (frame_map == NULL) return 0;
    int32_t n_slots = frame_map[0];
    if (n_slots < 0 || n_slots > 100000) return 0;
    return (int64_t)n_slots;
}

static int64_t pcc_gc_gray_mapped_roots_unlocked(
    const int32_t *frame_map,
    PyObject **slots,
    int resolve
) {
    int64_t n_slots = pcc_gc_root_slot_count_from_map(frame_map);
    if (n_slots <= 0 || slots == NULL) return 0;
    for (int64_t i = 0; i < n_slots; i++) {
        PyObject *root = resolve
            ? pcc_gc_resolve_root_slot_unlocked(&slots[i])
            : slots[i];
        pcc_gc_gray_root_object(root);
    }
    return n_slots;
}

static int64_t pcc_gc_visit_mapped_roots_unlocked(
    const int32_t *frame_map,
    PyObject **slots,
    PccGcRootVisitor visit,
    void *ctx
) {
    int64_t n_slots = pcc_gc_root_slot_count_from_map(frame_map);
    if (n_slots <= 0 || slots == NULL || visit == NULL) return 0;
    for (int64_t i = 0; i < n_slots; i++) {
        visit(slots[i], ctx);
    }
    return n_slots;
}

static int64_t pcc_gc_rewrite_mapped_roots_unlocked(
    const int32_t *frame_map,
    PyObject **slots
) {
    int64_t n_slots = pcc_gc_root_slot_count_from_map(frame_map);
    if (n_slots <= 0 || slots == NULL) return 0;
    int64_t rewritten = 0;
    for (int64_t i = 0; i < n_slots; i++) {
        PyObject *before = slots[i];
        PyObject *after = pcc_gc_resolve_root_slot_unlocked(&slots[i]);
        if (after != before) rewritten++;
    }
    return rewritten;
}

static void pcc_gc_gray_runtime_root(PyObject *root, void *ctx) {
    (void)ctx;
    pcc_gc_gray_root_object(root);
}

static void pcc_gc_gray_current_roots(void) {
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        if ((py_header_flags_load(h) & PY_FLAG_GC_PINNED) != 0) {
            pcc_gc_gray_root_object(n->obj);
        }
    }
    for (PccGcFrameNode *f = pcc_gc_frames; f != NULL; f = f->next) {
        (void)pcc_gc_gray_mapped_roots_unlocked(
            f->frame_map,
            f->slots,
            1
        );
    }
    for (
        PccGcContinuationRootNode *c = pcc_gc_continuation_roots;
        c != NULL;
        c = c->next
    ) {
        (void)pcc_gc_gray_mapped_roots_unlocked(
            c->frame_map,
            c->slots,
            1
        );
    }
    for (
        PccGcSchedulerRootNode *r = pcc_gc_scheduler_roots;
        r != NULL;
        r = r->next
    ) {
        if (r->slot != NULL) {
            pcc_gc_gray_root_object(
                pcc_gc_resolve_root_slot_unlocked(r->slot)
            );
        }
    }
    pcc_capi_visit_extension_module_state_roots(
        pcc_gc_gray_runtime_root,
        NULL
    );
}

void pcc_gc_visit_runtime_roots(PccGcRootVisitor visit, void *ctx) {
    if (visit == NULL) return;
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    for (PccGcFrameNode *f = pcc_gc_frames; f != NULL; f = f->next) {
        (void)pcc_gc_visit_mapped_roots_unlocked(
            f->frame_map,
            f->slots,
            visit,
            ctx
        );
    }
    for (
        PccGcContinuationRootNode *c = pcc_gc_continuation_roots;
        c != NULL;
        c = c->next
    ) {
        (void)pcc_gc_visit_mapped_roots_unlocked(
            c->frame_map,
            c->slots,
            visit,
            ctx
        );
    }
    for (
        PccGcSchedulerRootNode *r = pcc_gc_scheduler_roots;
        r != NULL;
        r = r->next
    ) {
        if (r->slot != NULL) {
            visit(*r->slot, ctx);
        }
    }
    pcc_capi_visit_extension_module_state_roots(visit, ctx);
    pcc_gc_graph_unlock();
}

static void pcc_gc_seed_roots(void) {
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        int32_t flags = py_header_flags_load(h);
        if (
            (flags & PY_FLAG_GC_FRESH_ALLOC) != 0
            && pcc_gc_explicit_collect_active == 0
        ) {
            py_header_flags_update(
                h,
                PY_FLAG_GC_COLOR_MASK | PY_FLAG_GC_FRESH_ALLOC,
                PY_FLAG_GC_BLACK
            );
        } else {
            py_header_flags_update(
                h,
                PY_FLAG_GC_COLOR_MASK | PY_FLAG_GC_FRESH_ALLOC,
                PY_FLAG_GC_WHITE
            );
        }
    }
    pcc_gc_gray_current_roots();
}

static int64_t pcc_gc_drain_all_gray_unlocked(void) {
    int64_t processed = 0;
    for (;;) {
        int64_t pass = 0;
        for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
            if (!pcc_gc_object_node_is_active(n)) continue;
            PyObjectHeader *h = py_header(n->obj);
            if ((py_header_flags_load(h) & PY_FLAG_GC_GRAY) == 0) continue;
            pcc_gc_trace_referents(n->obj, pcc_gc_gray_object);
            py_header_flags_update(
                h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_BLACK
            );
            processed++;
            pass++;
        }
        if (pass == 0) break;
    }
    return processed;
}

/* PEP 442 reachability recheck (mirror of the pcc-Python port). A __del__
 * dispatched in PASS 0 may have RESURRECTED an unreachable object (stored it
 * where a root reaches it). Re-mark from roots (seed whitens all but preserves
 * PY_FLAG_GC_SWEEP_CANDIDATE; gray roots; drain) and clear the candidate flag on
 * any object that is now reachable (no longer white), so PASS 1/PASS 2 skip it
 * — otherwise we clear+free a live object (heap corruption / double-free on
 * #1/#2, cleared fields -> AttributeError on #3/#4). Objects still unreachable
 * stay white|candidate and are reclaimed exactly as before. See
 * gc-5backend-finalizer-resurrection-no-libpython.md. */
static void pcc_gc_recheck_reachability_after_finalizers(void) {
    pcc_gc_seed_roots();
    (void)pcc_gc_drain_all_gray_unlocked();
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_SWEEP_CANDIDATE) != 0 &&
            (flags & PY_FLAG_GC_WHITE) == 0) {
            py_header_flags_and(h, ~PY_FLAG_GC_SWEEP_CANDIDATE);
        }
    }
}

static void pcc_gc_begin_mark_cycle(void) {
    pcc_gc_seed_roots();
    pcc_gc_mark_active_store(1);
    pcc_gc_cycle_requested_store(0);
    if (!pcc_gc_gray_exists()) pcc_gc_mark_active_store(0);
}

static int pcc_gc_finish_tracing_cycle(void) {
    int64_t stw = pcc_stop_the_world();
    if (stw != 0) return 0;
    /* Roots can change while #1/#2 tracing runs incrementally or
     * concurrently.  The final white->sweep-candidate cut must rescan the
     * current root set under the stop-the-world boundary and drain anything
     * newly grayed before deciding which white objects are unreachable. */
    pcc_gc_gray_current_roots();
    (void)pcc_gc_drain_all_gray_unlocked();
    /* The final white->sweep-candidate cut is the atomic phase for the
     * tracing skeletons (#1/#2).  Default builds take the no-op path. */
    for (PccGcObjectNode *n = pcc_gc_objects; n != NULL; n = n->next) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        if ((py_header_flags_load(h) & PY_FLAG_GC_WHITE) != 0) {
            py_header_flags_or(h, PY_FLAG_GC_SWEEP_CANDIDATE);
        } else {
            py_header_flags_and(h, ~PY_FLAG_GC_SWEEP_CANDIDATE);
        }
    }
    if (stw == 0) (void)pcc_resume_world();
    return 1;
}

static int64_t pcc_gc_step_trace_cycle_unlocked(
    int64_t budget,
    int finish_cycle
) {
    if (budget <= 0) {
        return 0;
    }

    int64_t processed = 0;

    if (pcc_gc_mark_active_load() == 0) {
        if (pcc_gc_cycle_requested_load() == 0) {
            return processed;
        }
        if (pcc_threads_enabled() && pcc_gc_in_auto_step) {
            return processed;
        }
        pcc_gc_begin_mark_cycle();
    }

    for (
        PccGcObjectNode *n = pcc_gc_objects;
        n != NULL && processed < budget;
        n = n->next
    ) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        if ((py_header_flags_load(h) & PY_FLAG_GC_GRAY) != 0) {
            pcc_gc_trace_referents(n->obj, pcc_gc_gray_object);
            py_header_flags_update(
                h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_BLACK
            );
            processed++;
        }
    }

    if (finish_cycle && !pcc_gc_gray_exists()) {
        if (pcc_gc_finish_tracing_cycle()) {
            pcc_gc_mark_active_store(0);
            pcc_gc_cycle_requested_store(0);
        }
    }

    return processed;
}

static int64_t pcc_gc_cms_worker_trace_cycle_unlocked(int64_t budget) {
    return pcc_gc_step_trace_cycle_unlocked(budget, 1);
}

static int64_t pcc_gc_step_trace_cycle(int64_t budget) {
    if (budget <= 0) return 0;
    pcc_gc_graph_lock();
    int64_t processed = pcc_gc_step_trace_cycle_unlocked(budget, 1);
    pcc_gc_graph_unlock();
    return processed;
}

static int64_t pcc_gc_step_generational_promotion(int64_t budget) {
    if (budget <= 0) return 0;
    int64_t processed = 0;
    pcc_gc_graph_lock();
    pcc_gc_promote_frame_roots(budget);
    pcc_gc_promote_scheduler_roots(budget);
    pcc_capi_visit_extension_module_state_roots(
        pcc_gc_promote_extension_module_state_root,
        NULL
    );
    for (
        PccGcObjectNode *n = pcc_gc_objects;
        n != NULL && processed < budget;
        n = n->next
    ) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_REMEMBERED) != 0) {
            pcc_gc_promote_remembered_owner_referents(n->obj);
            py_header_flags_and(h, ~PY_FLAG_GC_REMEMBERED);
            processed++;
            if ((processed % PCC_GC_SAFEPOINT_BATCH) == 0) {
                pcc_thread_safepoint();
            }
        }
    }
    for (
        PccGcObjectNode *n = pcc_gc_objects;
        n != NULL && processed < budget;
        n = n->next
    ) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObjectHeader *h = py_header(n->obj);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_YOUNG) != 0) {
            if (pcc_gc_forwarding_find(n->obj) != NULL) continue;
            py_header_flags_update(
                h, PY_FLAG_GC_YOUNG, PY_FLAG_GC_OLD
            );
            processed++;
            if ((processed % PCC_GC_SAFEPOINT_BATCH) == 0) {
                pcc_thread_safepoint();
            }
        }
    }
    pcc_gc_graph_unlock();

    if (processed > 0) {
        pcc_thread_safepoint();
    }
    return processed;
}

static int64_t pcc_gc_step_colored_remembered_roots(int64_t budget) {
    if (budget <= 0) return 0;
    int64_t processed = 0;
    int64_t drained = 0;
    int64_t batch_limit = budget;
    if (batch_limit > PCC_GC_BACKEND4_STORE_BUFFER_BATCH_CAPACITY) {
        batch_limit = PCC_GC_BACKEND4_STORE_BUFFER_BATCH_CAPACITY;
    }
    pcc_gc_graph_lock();
    pcc_gc_backend4_store_buffer_flush_all_medium_locked();
    while (pcc_gc_backend4_store_buffer != NULL && drained < batch_limit) {
        PccGcStoreBufferNode *n = pcc_gc_backend4_store_buffer;
        pcc_gc_backend4_store_buffer = n->next;
        PyObject *owner = n->owner;
        PyObject **slot = n->slot;
        PyObject *value = n->value;
        free(n);
        pcc_gc_backend4_store_buffer_dec_unlocked();
        drained++;
        if (!pcc_gc_is_known_object(owner)) {
            py_decref(value);
            continue;
        }
        PyObjectHeader *h = py_header(owner);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_REMEMBERED) == 0) {
            py_decref(value);
            continue;
        }
        pcc_gc_promote_young_object(value);
        if (slot != NULL) {
            pcc_gc_promote_young_slot(slot);
        } else {
            pcc_gc_promote_remembered_owner_referents(owner);
        }
        if (!pcc_gc_backend4_store_buffer_owner_pending(owner)) {
            py_header_flags_and(h, ~PY_FLAG_GC_REMEMBERED);
        }
        processed++;
        py_decref(value);
        if ((processed % PCC_GC_SAFEPOINT_BATCH) == 0) {
            pcc_thread_safepoint();
        }
    }
    if (drained > 0) {
        __atomic_add_fetch(
            &pcc_gc_backend4_store_buffer_drain_batches_count,
            1,
            __ATOMIC_RELAXED
        );
        __atomic_add_fetch(
            &pcc_gc_backend4_store_buffer_drained_entries_count,
            drained,
            __ATOMIC_RELAXED
        );
        pcc_gc_backend4_store_buffer_note_max_batch(drained);
        if (drained >= PCC_GC_BACKEND4_STORE_BUFFER_BATCH_CAPACITY) {
            __atomic_add_fetch(
                &pcc_gc_backend4_store_buffer_full_batches_count,
                1,
                __ATOMIC_RELAXED
            );
        }
        if (pcc_gc_backend4_store_buffer != NULL) {
            __atomic_add_fetch(
                &pcc_gc_backend4_store_buffer_incomplete_drains_count,
                1,
                __ATOMIC_RELAXED
            );
        }
    }
    pcc_gc_graph_unlock();
    if (processed > 0) pcc_thread_safepoint();
    return processed;
}

static int64_t pcc_gc_step_colored_generation_aging(int64_t budget) {
    if (budget <= 0) return 0;
    int64_t processed = 0;
    pcc_gc_graph_lock();
    for (
        PccGcObjectNode *n = pcc_gc_objects;
        n != NULL && processed < budget;
        n = n->next
    ) {
        if (!pcc_gc_object_node_is_active(n)) continue;
        PyObject *o = n->obj;
        if (pcc_gc_forwarding_find(o) != NULL) continue;
        PyObjectHeader *h = py_header(o);
        int32_t flags = py_header_flags_load(h);
        if ((flags & PY_FLAG_GC_YOUNG) == 0) continue;
        py_header_flags_update(h, PY_FLAG_GC_YOUNG, PY_FLAG_GC_OLD);
        if (pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING) {
            pcc_gc_backend4_zpage_note_owner_promoted_unlocked(o);
        }
        __atomic_add_fetch(
            &pcc_gc_backend4_young_promotions, 1, __ATOMIC_RELAXED
        );
        processed++;
        if ((processed % PCC_GC_SAFEPOINT_BATCH) == 0) {
            pcc_thread_safepoint();
        }
    }
    pcc_gc_graph_unlock();
    if (processed > 0) pcc_thread_safepoint();
    return processed;
}

int64_t pcc_gc_step(int64_t budget) {
    pcc_gc_init_config();
    if (budget <= 0) return 0;
    pcc_gc_metric_add(PCC_GC_COUNTER_WORK_STEPS, 1);
    int64_t start_us = pcc_gc_now_us();
    int64_t processed = 0;
    if (pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP) {
        pcc_gc_cms_flush_wb_buffer();
    }
    if (
        pcc_gc_selected_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        processed += pcc_gc_step_trace_cycle(budget);
    } else if (
        pcc_gc_selected_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
    ) {
        processed += pcc_gc_step_generational_promotion(budget);
        if (
            pcc_gc_explicit_collect_active
            && processed < budget
            && (
                pcc_gc_cycle_requested_load() != 0
                || pcc_gc_mark_active_load() != 0
                || pcc_gc_has_sweep_candidate() != 0
            )
        ) {
            processed += pcc_gc_step_trace_cycle(budget - processed);
        }
    } else if (
        pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        if (pcc_gc_explicit_collect_active) {
            processed += pcc_gc_step_trace_cycle(budget - processed);
        } else {
            processed += pcc_gc_step_colored_remembered_roots(
                budget - processed
            );
            if (processed < budget) {
                processed += pcc_gc_step_colored_generation_aging(
                    budget - processed
                );
            }
            if (processed < budget) {
                processed += pcc_gc_backend4_evacuation_page_drain(
                    budget - processed
                );
            }
            if (processed < budget) {
                int64_t selected = pcc_gc_backend4_select_relocation_pages(
                    budget - processed
                );
                if (selected > 0) {
                    int64_t moved = pcc_gc_backend4_evacuation_page_drain(
                        budget - processed
                    );
                    processed += moved > 0 ? moved : selected;
                }
            }

            if (
                processed < budget
                && (
                    pcc_gc_cycle_requested_load() != 0
                    || pcc_gc_mark_active_load() != 0
                    || pcc_gc_has_sweep_candidate() != 0
                )
            ) {
                /* Colored relocation changes the interpretation of read-barrier
                 * state.  Keep the phase transition STW even though this backend
                 * still uses a side-table candidate flag instead of multi-mapping. */
                int64_t stw = pcc_stop_the_world();
                processed += pcc_gc_step_trace_cycle(budget - processed);
                if (stw == 0) (void)pcc_resume_world();
            }
        }
    }
    pcc_gc_record_pause(start_us, pcc_gc_now_us());
    if (
        pcc_gc_selected_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        pcc_gc_discharge_debt(processed);
        if (
            pcc_gc_mark_active_load() == 0
            && pcc_gc_cycle_requested_load() == 0
        ) {
            __atomic_store_n(&pcc_gc_debt_bytes, 0, __ATOMIC_RELEASE);
        }
    }
    return processed;
}

int64_t pcc_gc_has_tracing_sweep(void) {
    pcc_gc_init_config();
    if (
        pcc_gc_selected_backend != PCC_GC_KIND_INCREMENTAL_TRICOLOR
        && pcc_gc_selected_backend != PCC_GC_KIND_CONCURRENT_MARK_SWEEP
        && pcc_gc_selected_backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING
    ) {
        return 0;
    }
    return pcc_gc_has_sweep_candidate() != 0 ? 1 : 0;
}

int64_t pcc_gc_collect_tracing(void) {
    pcc_gc_init_config();
    if (
        pcc_gc_selected_backend != PCC_GC_KIND_INCREMENTAL_TRICOLOR
        && pcc_gc_selected_backend != PCC_GC_KIND_CONCURRENT_MARK_SWEEP
        && pcc_gc_selected_backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING
    ) {
        return 0;
    }

    if (pcc_gc_has_sweep_candidate() == 0) return 0;
    int64_t stw = pcc_stop_the_world();
    if (stw != 0) return 0;
    int64_t reclaimed = pcc_gc_sweep_unreachable(1024);
    if (stw == 0) (void)pcc_resume_world();
    return reclaimed;
}

void pcc_gc_begin_explicit_tracing_collect(void) {
    pcc_gc_init_config();
    pcc_gc_explicit_collect_active = 1;
    if (pcc_gc_selected_backend != PCC_GC_KIND_REFCOUNT_CYCLE) {
        pcc_gc_cycle_requested_store(1);
    }
}

void pcc_gc_end_explicit_tracing_collect(void) {
    pcc_gc_explicit_collect_active = 0;
}

/* Reentrancy probe for pcc_gc_collect: pcc_gc_explicit_collect_active is set
 * across the whole begin..end (mark+sweep) window of an explicit tracing
 * collect, so a non-zero value means a collect is already in progress. The flag
 * is file-static here, so py_obj.c (the pcc_gc_collect reentrancy guard, cc
 * mode) reads it through this getter. The pcc-Python port reads the shared
 * global directly via global_addr. See
 * gc-5backend-reentrant-collect-during-finalizer-no-libpython.md. */
int32_t pcc_gc_explicit_collect_is_active(void) {
    return pcc_gc_explicit_collect_active;
}

static void pcc_gc_maybe_auto_step(void) {
    if (pcc_gc_in_auto_step) return;
    if (pcc_gc_selected_backend != PCC_GC_KIND_INCREMENTAL_TRICOLOR) return;
    if (pcc_threads_enabled()) return;
    if (
        __atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_RELAXED)
        < pcc_gc_debt_threshold()
    ) return;
    pcc_gc_in_auto_step = 1;
    (void)pcc_gc_step(pcc_gc_budget_from_debt());
    pcc_gc_in_auto_step = 0;
}

static void pcc_gc_cms_maybe_assist(void) {
    if (pcc_gc_in_auto_step) return;
    if (pcc_gc_selected_backend != PCC_GC_KIND_CONCURRENT_MARK_SWEEP) return;
    if (
        __atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_RELAXED)
        < pcc_gc_debt_threshold()
    ) return;
    __atomic_add_fetch(
        &pcc_gc_cms_mutator_assists, 1, __ATOMIC_RELAXED
    );
    pcc_gc_in_auto_step = 1;
    (void)pcc_gc_step(pcc_gc_budget_from_debt());
    pcc_gc_in_auto_step = 0;
}

void pcc_gc_note_alloc(int64_t bytes) {
    pcc_gc_init_config();
    if (bytes < 0) bytes = 0;
    pcc_gc_metric_add(PCC_GC_COUNTER_ALLOCATIONS, 1);
    if (pcc_gc_selected_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR) {
        __atomic_add_fetch(&pcc_gc_debt_bytes, bytes, __ATOMIC_ACQ_REL);
        pcc_gc_maybe_auto_step();
    } else if (
        pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        __atomic_add_fetch(&pcc_gc_debt_bytes, bytes, __ATOMIC_ACQ_REL);
        if (pcc_threads_enabled()) {
            pcc_gc_cms_maybe_start_worker();
        }
        pcc_gc_cms_queue_push(bytes);
        pcc_gc_cms_maybe_assist();
    }
}

void pcc_gc_note_object_allocated_sized(PyObject *o, int64_t size) {
    pcc_gc_init_config();
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    if (size < (int64_t)sizeof(PyObjectHeader)) {
        size = (int64_t)sizeof(PyObjectHeader);
    }
    if (!pcc_gc_tracks_objects()) return;
    pcc_gc_graph_lock();
    if (
        pcc_gc_selected_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || pcc_gc_selected_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        PyObjectHeader *h = py_header(o);
        int32_t color = (
            pcc_gc_mark_active_load() != 0
            ? PY_FLAG_GC_BLACK
            : PY_FLAG_GC_WHITE
        );
        h->flags = (
            h->flags & ~PY_FLAG_GC_COLOR_MASK
        ) | color | PY_FLAG_GC_FRESH_ALLOC;
        pcc_gc_cycle_requested_store(1);
    } else if (
        pcc_gc_selected_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
    ) {
        PyObjectHeader *h = py_header(o);
        h->flags = (h->flags & ~(
            PY_FLAG_GC_COLOR_MASK
            | PY_FLAG_GC_YOUNG
            | PY_FLAG_GC_OLD
        )) | (PY_FLAG_GC_YOUNG | PY_FLAG_GC_WHITE);
        if (pcc_gc_pending_minor_block != NULL) {
            h->flags |= PY_FLAG_GC_MINOR_ARENA;
        }
        pcc_gc_cycle_requested_store(1);
    } else if (
        pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        PyObjectHeader *h = py_header(o);
        int32_t gen_flags = h->flags & (PY_FLAG_GC_YOUNG | PY_FLAG_GC_OLD);
        h->flags = (h->flags & ~(
            PY_FLAG_GC_COLOR_MASK
            | PY_FLAG_GC_RELOCATION_CANDIDATE
            | PY_FLAG_GC_RELOCATION_TARGET
        )) | PY_FLAG_GC_WHITE;
        if (gen_flags == 0) {
            h->flags |= PY_FLAG_GC_YOUNG;
        }
        pcc_gc_cycle_requested_store(1);
    }
    PccGcObjectNode *n = (PccGcObjectNode *)calloc(1, sizeof(PccGcObjectNode));
    if (n == NULL) {
        pcc_gc_pending_minor_block = NULL;
        pcc_gc_graph_unlock();
        return;
    }
    n->size = size;
    __atomic_add_fetch(&pcc_gc_live_bytes, n->size, __ATOMIC_ACQ_REL);
    n->minor_block = pcc_gc_pending_minor_block;
    pcc_gc_pending_minor_block = NULL;
    n->obj = o;
    n->next = pcc_gc_objects;
    pcc_gc_objects = n;
    pcc_gc_backend4_zpage_track_alloc_unlocked(o, n->size);
    pcc_gc_graph_unlock();
}

void pcc_gc_note_object_allocated(PyObject *o) {
    pcc_gc_note_object_allocated_sized(o, (int64_t)sizeof(PyObjectHeader));
}

void pcc_gc_note_object_freeing(PyObject *o) {
    pcc_gc_init_config();
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    pcc_gc_graph_lock();
    pcc_gc_forwarding_remove(o);
    pcc_gc_identity_remove(o);
    pcc_gc_relocation_set_remove(o);
    pcc_gc_backend4_store_buffer_remove(o);
    pcc_gc_backend4_remembered_set_remove(o);
    pcc_gc_backend4_zpage_remove_unlocked(o);
    if (!pcc_gc_tracks_objects()) {
        pcc_gc_graph_unlock();
        return;
    }
    PccGcObjectNode **cur = &pcc_gc_objects;
    while (*cur != NULL) {
        if ((*cur)->obj == o) {
            PccGcObjectNode *dead = *cur;
            if (!pcc_gc_object_node_is_freeing(dead) && dead->size > 0) {
                pcc_gc_live_bytes_subtract(dead->size);
            }
            pcc_gc_object_node_set_freeing(dead);
            if (dead->minor_block != NULL) {
                pcc_gc_graph_unlock();
                return;
            }
            (void)pcc_gc_object_index_remove(o);
            *cur = dead->next;
            free(dead);
            pcc_gc_graph_unlock();
            return;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
}

static void pcc_gc_minor_release_block(PccGcMinorBlock *block) {
    if (block == NULL) return;
    int64_t live = __atomic_load_n(
        &block->live_objects, __ATOMIC_ACQUIRE
    );
    while (live > 0) {
        if (__atomic_compare_exchange_n(
                &block->live_objects,
                &live,
                live - 1,
                0,
                __ATOMIC_ACQ_REL,
                __ATOMIC_ACQUIRE
            )) {
            live--;
            break;
        }
    }
    if (live != 0) return;

    if (block == pcc_gc_minor_current) {
        block->ptr = block->base;
        __atomic_store_n(&pcc_gc_minor_bytes, 0, __ATOMIC_RELEASE);
        return;
    }
    if (block->owner_thread_id != pcc_current_thread_id()) {
        return;
    }

    PccGcMinorBlock **cur = &pcc_gc_minor_blocks;
    while (*cur != NULL) {
        if (*cur == block) {
            *cur = block->next;
            free(block->base);
            free(block);
            return;
        }
        cur = &(*cur)->next;
    }
}

void pcc_gc_free_object_memory(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    PyObjectHeader *h = py_header(o);
    int32_t flags = py_header_flags_load(h);
    if ((flags & PY_FLAG_GC_ZPAGE_ALLOC) != 0) {
        return;
    }
    if (pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING) {
        pcc_gc_graph_lock();
        int32_t zpage_owned =
            pcc_gc_backend4_zpage_owns_addr_unlocked((void *)o);
        pcc_gc_graph_unlock();
        if (zpage_owned != 0) return;
    }
    if ((flags & PY_FLAG_GC_MINOR_ARENA) == 0) {
        free(o);
        return;
    }

    pcc_gc_graph_lock();
    PccGcObjectNode **cur = &pcc_gc_objects;
    while (*cur != NULL) {
        if ((*cur)->obj == o) {
            PccGcObjectNode *dead = *cur;
            if (!pcc_gc_object_node_is_freeing(dead) && dead->size > 0) {
                pcc_gc_live_bytes_subtract(dead->size);
            }
            pcc_gc_backend4_zpage_remove_unlocked(o);
            PccGcMinorBlock *block = dead->minor_block;
            *cur = dead->next;
            free(dead);
            pcc_gc_minor_release_block(block);
            pcc_gc_graph_unlock();
            return;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
}

void pcc_gc_note_load(void) {
    pcc_gc_metric_add(PCC_GC_COUNTER_READ_BARRIERS, 1);
}

PyObject *pcc_gc_note_relocation_read(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return o;
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    PyObject *resolved = pcc_gc_note_relocation_read_unlocked(o);
    pcc_gc_graph_unlock();
    return resolved;
}

void pcc_gc_note_store(void) {
    pcc_gc_metric_add(PCC_GC_COUNTER_WRITE_BARRIERS, 1);
}

void pcc_gc_note_slot_write_barrier(
    PyObject *owner,
    PyObject **slot,
    PyObject *value
) {
    if (value == NULL || PY_IS_TAGGED_INT(value)) return;
    int64_t barrier_backend = pcc_gc_selected_backend;
    PyObjectHeader *value_h = py_header(value);
    if (owner == NULL) {
        if (
            barrier_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
            || barrier_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
            || barrier_backend == PCC_GC_KIND_COLORED_RELOCATING
        ) {
            int flush_cms_wb = 0;
            pcc_gc_graph_lock();
        int32_t value_flags = py_header_flags_load(value_h);
        int should_gray = (value_flags & PY_FLAG_GC_WHITE) != 0;
        if (barrier_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP) {
            should_gray = (value_flags & PY_FLAG_GC_GRAY) == 0;
        }
        if (
            pcc_gc_mark_active_load() != 0
            && pcc_gc_is_known_object(value)
            && should_gray
        ) {
                py_header_flags_update(
                    value_h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_GRAY
                );
                pcc_gc_mark_active_store(1);
                if (
                    barrier_backend
                    == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                ) {
                    flush_cms_wb = pcc_gc_cms_buffer_gray(value);
                }
            }
            pcc_gc_graph_unlock();
            if (flush_cms_wb) pcc_gc_cms_flush_wb_buffer();
        }
        return;
    }
    if (PY_IS_TAGGED_INT(owner)) return;
    PyObjectHeader *owner_h = py_header(owner);
    if (
        barrier_backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || barrier_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
    ) {
        int flush_cms_wb = 0;
        pcc_gc_graph_lock();
        if (!pcc_gc_is_known_object(owner) || !pcc_gc_is_known_object(value)) {
            pcc_gc_graph_unlock();
            return;
        }
        int should_shade = 0;
        if (
            barrier_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
        ) {
            should_shade = pcc_gc_mark_active_load() != 0;
        } else {
            should_shade =
                (py_header_flags_load(owner_h) & PY_FLAG_GC_BLACK) != 0;
        }
        int32_t value_flags = py_header_flags_load(value_h);
        int should_gray_value = (value_flags & PY_FLAG_GC_WHITE) != 0;
        if (barrier_backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP) {
            should_gray_value = (value_flags & PY_FLAG_GC_GRAY) == 0;
        }
        if (should_shade && should_gray_value) {
            py_header_flags_update(
                value_h, PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_GRAY
            );
            pcc_gc_mark_active_store(1);
            if (
                barrier_backend
                == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
            ) {
                flush_cms_wb = pcc_gc_cms_buffer_gray(value);
            }
        }
        pcc_gc_graph_unlock();
        if (flush_cms_wb) pcc_gc_cms_flush_wb_buffer();
    } else if (
        barrier_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || barrier_backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        pcc_gc_graph_lock();
        if (!pcc_gc_is_known_object(owner) || !pcc_gc_is_known_object(value)) {
            pcc_gc_graph_unlock();
            return;
        }
        if (
            (py_header_flags_load(owner_h) & PY_FLAG_GC_OLD) != 0
            && (py_header_flags_load(value_h) & PY_FLAG_GC_YOUNG) != 0
        ) {
            if (barrier_backend == PCC_GC_KIND_COLORED_RELOCATING) {
                if (pcc_gc_backend4_store_buffer_enqueue(owner, slot, value)) {
                    __atomic_add_fetch(
                        &pcc_gc_backend4_genzgc_store_barriers,
                        1,
                        __ATOMIC_RELAXED
                    );
                }
            } else if (
                barrier_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
            ) {
                py_header_flags_or(owner_h, PY_FLAG_GC_REMEMBERED);
            }
        }
        pcc_gc_graph_unlock();
    }
}

void pcc_gc_note_write_barrier(PyObject *owner, PyObject *value) {
    pcc_gc_note_slot_write_barrier(owner, NULL, value);
}

void pcc_gc_note_safepoint(void) {
    pcc_gc_metric_add(PCC_GC_COUNTER_SAFEPOINTS, 1);
}

void pcc_gc_note_pin(int32_t delta) {
    pcc_gc_metric_add(PCC_GC_COUNTER_PIN_BALANCE, delta);
}

void pcc_gc_scheduler_root_register(PyObject **slot) {
    pcc_gc_init_config();
    if (slot == NULL) return;
    PccGcSchedulerRootNode *node = (
        PccGcSchedulerRootNode *
    )calloc(1, sizeof(PccGcSchedulerRootNode));
    if (node == NULL) return;
    node->slot = slot;
    pcc_gc_graph_lock();
    node->next = pcc_gc_scheduler_roots;
    pcc_gc_scheduler_roots = node;
    pcc_gc_graph_unlock();
    pcc_gc_cycle_requested_store(1);
}

void pcc_gc_scheduler_root_unregister(PyObject **slot) {
    pcc_gc_init_config();
    if (slot == NULL) return;
    PccGcSchedulerRootNode *dead = NULL;
    pcc_gc_graph_lock();
    PccGcSchedulerRootNode **cur = &pcc_gc_scheduler_roots;
    while (*cur != NULL) {
        if ((*cur)->slot == slot) {
            dead = *cur;
            *cur = dead->next;
            break;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
    if (dead != NULL) {
        free(dead);
        pcc_gc_cycle_requested_store(1);
    }
}

PccGcSchedulerQueue *pcc_gc_scheduler_queue_new(void) {
    PccGcSchedulerQueue *queue = (
        PccGcSchedulerQueue *
    )calloc(1, sizeof(PccGcSchedulerQueue));
    if (queue == NULL) return NULL;
    queue->mutex = pcc_mutex_new();
    if (queue->mutex == NULL) {
        free(queue);
        return NULL;
    }
    return queue;
}

static void pcc_gc_scheduler_queue_entry_free(PccGcSchedulerQueueEntry *entry) {
    if (entry == NULL) return;
    pcc_gc_graph_lock();
    int64_t barrier_before = pcc_gc_relocation_barrier_forwards;
    (void)pcc_gc_load_ptr(NULL, &entry->value);
    if (
        pcc_gc_selected_backend == PCC_GC_KIND_COLORED_RELOCATING
        && entry->value != NULL
        && pcc_gc_relocation_forwards > 0
        && pcc_gc_relocation_barrier_forwards == barrier_before
    ) {
        pcc_gc_relocation_barrier_forwards++;
    }
    pcc_gc_scheduler_root_unregister(&entry->value);
    pcc_gc_graph_unlock();
    pcc_gc_store_root(&entry->value, NULL);
    free(entry);
}

void pcc_gc_scheduler_queue_free(PccGcSchedulerQueue *queue) {
    if (queue == NULL) return;
    if (queue->mutex != NULL) (void)pcc_mutex_lock(queue->mutex);
    PccGcSchedulerQueueEntry *entry = queue->head;
    queue->head = NULL;
    queue->tail = NULL;
    queue->length = 0;
    if (queue->mutex != NULL) (void)pcc_mutex_unlock(queue->mutex);
    while (entry != NULL) {
        PccGcSchedulerQueueEntry *next = entry->next;
        pcc_gc_scheduler_queue_entry_free(entry);
        entry = next;
    }
    pcc_mutex_free(queue->mutex);
    free(queue);
}

int64_t pcc_gc_scheduler_queue_push(
    PccGcSchedulerQueue *queue, PyObject *value
) {
    if (queue == NULL) return -1;
    PccGcSchedulerQueueEntry *entry = (
        PccGcSchedulerQueueEntry *
    )calloc(1, sizeof(PccGcSchedulerQueueEntry));
    if (entry == NULL) return -1;
    pcc_gc_graph_lock();
    pcc_gc_scheduler_root_register(&entry->value);
    pcc_gc_store_ptr(NULL, &entry->value, value);
    pcc_gc_graph_unlock();
    if (pcc_mutex_lock(queue->mutex) != 0) {
        pcc_gc_scheduler_queue_entry_free(entry);
        return -1;
    }
    if (queue->tail == NULL) {
        queue->head = entry;
        queue->tail = entry;
    } else {
        queue->tail->next = entry;
        queue->tail = entry;
    }
    queue->length++;
    return pcc_mutex_unlock(queue->mutex);
}

int64_t pcc_gc_scheduler_queue_pop_into(
    PccGcSchedulerQueue *queue, PyObject **out_slot
) {
    if (queue == NULL) return -1;
    if (pcc_mutex_lock(queue->mutex) != 0) return -1;
    PccGcSchedulerQueueEntry *entry = queue->head;
    if (entry == NULL) {
        (void)pcc_mutex_unlock(queue->mutex);
        return 0;
    }
    queue->head = entry->next;
    if (queue->head == NULL) queue->tail = NULL;
    queue->length--;
    (void)pcc_mutex_unlock(queue->mutex);
    pcc_gc_graph_lock();
    PyObject *value = pcc_gc_load_ptr(NULL, &entry->value);
    if (out_slot != NULL) pcc_gc_store_ptr(NULL, out_slot, value);
    pcc_gc_scheduler_root_unregister(&entry->value);
    pcc_gc_graph_unlock();
    pcc_gc_store_root(&entry->value, NULL);
    free(entry);
    return 1;
}

int64_t pcc_gc_scheduler_queue_len(PccGcSchedulerQueue *queue) {
    if (queue == NULL) return 0;
    if (pcc_mutex_lock(queue->mutex) != 0) return -1;
    int64_t length = queue->length;
    (void)pcc_mutex_unlock(queue->mutex);
    return length;
}

int64_t pcc_gc_scheduler_root_count(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t count = 0;
    for (
        PccGcSchedulerRootNode *n = pcc_gc_scheduler_roots;
        n != NULL;
        n = n->next
    ) {
        count++;
    }
    pcc_gc_graph_unlock();
    return count;
}

int64_t pcc_gc_frame_root_slot_count(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t slots = 0;
    for (
        PccGcFrameNode *n = pcc_gc_frames;
        n != NULL;
        n = n->next
    ) {
        slots += pcc_gc_root_slot_count_from_map(n->frame_map);
    }
    pcc_gc_graph_unlock();
    return slots;
}

int64_t pcc_gc_continuation_root_slot_count(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t slots = 0;
    for (
        PccGcContinuationRootNode *n = pcc_gc_continuation_roots;
        n != NULL;
        n = n->next
    ) {
        slots += pcc_gc_root_slot_count_from_map(n->frame_map);
    }
    pcc_gc_graph_unlock();
    return slots;
}

int64_t pcc_gc_coroutine_root_score(void) {
    return pcc_gc_scheduler_root_count()
        + pcc_gc_frame_root_slot_count()
        + pcc_gc_continuation_root_slot_count();
}

void pcc_gc_register_continuation_root(
    const void *frame_map,
    PyObject **slots
) {
    pcc_gc_init_config();
    if (frame_map == NULL || slots == NULL) return;
    int64_t n_slots = pcc_gc_root_slot_count_from_map((const int32_t *)frame_map);
    if (n_slots <= 0) return;
    PccGcContinuationRootNode *n = (
        PccGcContinuationRootNode *
    )calloc(1, sizeof(PccGcContinuationRootNode));
    if (n == NULL) return;
    n->frame_map = (const int32_t *)frame_map;
    n->slots = slots;
    pcc_gc_graph_lock();
    n->next = pcc_gc_continuation_roots;
    pcc_gc_continuation_roots = n;
    pcc_gc_cycle_requested_store(1);
    pcc_gc_graph_unlock();
}

void pcc_gc_unregister_continuation_root(PyObject **slots) {
    pcc_gc_init_config();
    if (slots == NULL) return;
    pcc_gc_graph_lock();
    PccGcContinuationRootNode **cur = &pcc_gc_continuation_roots;
    while (*cur != NULL) {
        if ((*cur)->slots == slots) {
            PccGcContinuationRootNode *dead = *cur;
            *cur = dead->next;
            free(dead);
            pcc_gc_cycle_requested_store(1);
            pcc_gc_graph_unlock();
            return;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
}

int64_t pcc_gc_trace_continuation_roots(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t traced = 0;
    for (
        PccGcContinuationRootNode *n = pcc_gc_continuation_roots;
        n != NULL;
        n = n->next
    ) {
        traced += pcc_gc_gray_mapped_roots_unlocked(
            n->frame_map,
            n->slots,
            0
        );
    }
    pcc_gc_graph_unlock();
    return traced;
}

int64_t pcc_gc_rewrite_continuation_roots(void) {
    pcc_gc_init_config();
    pcc_gc_graph_lock();
    int64_t rewritten = 0;
    for (
        PccGcContinuationRootNode *n = pcc_gc_continuation_roots;
        n != NULL;
        n = n->next
    ) {
        rewritten += pcc_gc_rewrite_mapped_roots_unlocked(
            n->frame_map,
            n->slots
        );
    }
    pcc_gc_graph_unlock();
    return rewritten;
}

void pcc_gc_note_frame_enter(const void *frame_map, PyObject **slots) {
    pcc_gc_init_config();
    if (!pcc_gc_should_track_frame_roots()) return;
    if (frame_map == NULL || slots == NULL) return;
    PccGcFrameNode *n = (PccGcFrameNode *)calloc(1, sizeof(PccGcFrameNode));
    if (n == NULL) return;
    n->frame_map = (const int32_t *)frame_map;
    n->slots = slots;
    pcc_gc_graph_lock();
    n->next = pcc_gc_frames;
    pcc_gc_frames = n;
    pcc_gc_cycle_requested_store(1);
    pcc_gc_graph_unlock();
}

void pcc_gc_note_frame_leave(PyObject **slots) {
    pcc_gc_init_config();
    if (!pcc_gc_should_track_frame_roots()) return;
    if (slots == NULL) return;
    pcc_gc_graph_lock();
    if (
        pcc_gc_selected_backend == PCC_GC_KIND_REFCOUNT_CYCLE
        && pcc_gc_frames == NULL
    ) {
        pcc_gc_graph_unlock();
        return;
    }
    PccGcFrameNode **cur = &pcc_gc_frames;
    while (*cur != NULL) {
        if ((*cur)->slots == slots) {
            PccGcFrameNode *dead = *cur;
            *cur = dead->next;
            free(dead);
            pcc_gc_cycle_requested_store(1);
            pcc_gc_graph_unlock();
            return;
        }
        cur = &(*cur)->next;
    }
    pcc_gc_graph_unlock();
}
