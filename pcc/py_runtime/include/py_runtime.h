/* pcc/py_runtime/include/py_runtime.h */
#ifndef PY_RUNTIME_H
#define PY_RUNTIME_H

#include <stdint.h>
#include <stddef.h>

/* Opaque PyObject; concrete definition lives in py_obj.c */
typedef struct PyObject PyObject;
typedef struct PyClassObject PyClassObject;
typedef struct PccGcSchedulerQueue PccGcSchedulerQueue;

/* Type tag values — used in PyObject header and tagged int */
enum {
    PY_TYPE_NONE    = 0,
    PY_TYPE_BOOL    = 1,
    PY_TYPE_INT     = 2,    /* bignum; non-tagged form */
    PY_TYPE_FLOAT   = 3,
    PY_TYPE_STR     = 4,
    PY_TYPE_LIST    = 5,
    PY_TYPE_DICT    = 6,
    PY_TYPE_TUPLE   = 7,
    PY_TYPE_SET     = 8,
    PY_TYPE_FUNC    = 9,
    PY_TYPE_CLASS   = 10,
    PY_TYPE_INSTANCE= 11,
    PY_TYPE_EXC     = 12,
    PY_TYPE_FILE    = 13,
    PY_TYPE_ITER    = 14,
    PY_TYPE_GEN     = 15,
    PY_TYPE_COMPLEX = 16,
    PY_TYPE_BYTES   = 17,
    PY_TYPE_BYTEARRAY = 18,
    PY_TYPE_MEMORYVIEW = 19,
    PY_TYPE_COROUTINE = 20,
    PY_TYPE_WEAKREF = 21,
    PY_TYPE_THREAD_LOCK = 22,
    PY_TYPE_THREAD_RLOCK = 23,
    PY_TYPE_THREAD_EVENT = 24,
    PY_TYPE_THREAD_CONDITION = 25,
    PY_TYPE_THREAD_SEMAPHORE = 26,
    PY_TYPE_THREAD = 27,
    PY_TYPE_TASK = 28,
    PY_TYPE_CONTINUATION = 29,
    PY_TYPE_VIRTUAL_THREAD = 30,
    PY_TYPE_VALUEBOX = 200,
    PY_TYPE_USER    = 100   /* user-defined classes >= this */
};

/* Built-in exception tags accepted by py_exc_new / py_exc_builtin_class. */
enum {
    PY_EXC_BASE              = 0,   /* BaseException */
    PY_EXC_EXCEPTION         = 1,   /* Exception */
    PY_EXC_VALUEERROR        = 2,
    PY_EXC_TYPEERROR         = 3,
    PY_EXC_KEYERROR          = 4,
    PY_EXC_INDEXERROR        = 5,
    PY_EXC_ATTRIBUTEERROR    = 6,
    PY_EXC_RUNTIMEERROR      = 7,
    PY_EXC_STOPITERATION     = 8,
    PY_EXC_ZERODIVISIONERROR = 9,
    PY_EXC_NAMEERROR         = 10,
    PY_EXC_NOTIMPLEMENTEDERROR = 11,
    PY_EXC_ARITHMETICERROR   = 12,
    PY_EXC_LOOKUPERROR       = 13,
    PY_EXC_OSERROR           = 14,
    PY_EXC_OVERFLOWERROR     = 15,
    PY_EXC_ASSERTIONERROR    = 16,
    PY_EXC_STOPASYNCITERATION = 17,
    PY_EXC_REFERENCEERROR    = 18,
    PY_EXC_N_BUILTIN         = 19
};

/* Every PyObject has this header prefix. */
typedef struct {
    int64_t refcount;
    int32_t  type_tag;
    int32_t  flags;        /* bit 0 = immortal, bit 1 = gc-tracked, ... */
} PyObjectHeader;

typedef struct PyContinuationStackChunk PyContinuationStackChunk;
typedef struct PyContinuationObject {
    PyObjectHeader h;
    void *resume_pc;
    PyContinuationStackChunk *stack_chunk;
    int64_t mounted;
    int64_t resume_abi;
} PyContinuationObject;

#define PCC_CONTINUATION_RESUME_ABI_LEGACY_NOARG 0
#define PCC_CONTINUATION_RESUME_ABI_VTHREAD 1

/* ---- GC interface ------------------------------------------------------ */
/* These are the memory-management ABI that codegen should target. The
 * default backend is reference counting; future tracing / generational /
 * moving collectors must preserve this surface rather than teaching codegen
 * about their internals. `py_incref` / `py_decref` are compatibility shims
 * for the refcount-shaped runtime helpers and should not be treated as the
 * foundational ABI for new code. */
PyObject *pcc_gc_alloc(int64_t size, int32_t type_tag, int32_t flags);
PyObject *pcc_gc_retain(PyObject *o);
void      pcc_gc_release(PyObject *o);
extern int32_t pcc_thread_stop_requested;
void      pcc_debug_check_release(const char *name, void *obj);
void      pcc_debug_bad_str_concat(void *a, void *b,
                                   int64_t tag_a, int64_t tag_b);
PyObject *pcc_gc_load_ptr(PyObject *owner, PyObject **slot);
void      pcc_gc_store_ptr(PyObject *owner, PyObject **slot, PyObject *value);
void      pcc_gc_store_root(PyObject **slot, PyObject *value);
void      pcc_gc_note_write_barrier(PyObject *owner, PyObject *value);
void      pcc_gc_note_slot_write_barrier(
    PyObject *owner,
    PyObject **slot,
    PyObject *value
);
void      pcc_gc_scheduler_root_register(PyObject **slot);
void      pcc_gc_scheduler_root_unregister(PyObject **slot);
void      pcc_gc_register_continuation_root(const void *frame_map, PyObject **slots);
void      pcc_gc_unregister_continuation_root(PyObject **slots);
int64_t   pcc_gc_trace_continuation_roots(void);
int64_t   pcc_gc_rewrite_continuation_roots(void);
PccGcSchedulerQueue *pcc_gc_scheduler_queue_new(void);
void      pcc_gc_scheduler_queue_free(PccGcSchedulerQueue *queue);
int64_t   pcc_gc_scheduler_queue_push(PccGcSchedulerQueue *queue, PyObject *value);
int64_t   pcc_gc_scheduler_queue_pop_into(PccGcSchedulerQueue *queue, PyObject **out_slot);
int64_t   pcc_gc_scheduler_queue_len(PccGcSchedulerQueue *queue);
/* Frame map format, v0: frame_map points at an int32 slot count.
 * slots points at a contiguous PyObject* local/root array. Future precise
 * maps can extend this block; a NULL frame_map means "no roots". */
void      pcc_gc_frame_enter(const void *frame_map, PyObject **slots);
void      pcc_gc_frame_leave(PyObject **slots);
void      pcc_gc_safepoint(void);
int64_t   pcc_gc_collect(int32_t reason);
void      pcc_gc_pin(PyObject *o);
void      pcc_gc_unpin(PyObject *o);
int64_t   pcc_gc_object_id(PyObject *o);
void      pcc_gc_reset_relocation_set(void);
int64_t   pcc_gc_select_relocation_set(int64_t budget);
int64_t   pcc_gc_backend4_evacuation_drain(int64_t budget);
int64_t   pcc_gc_backend4_evacuation_page_drain(int64_t page_budget);
int64_t   pcc_gc_relocation_set_contains(PyObject *o);
int64_t   pcc_gc_relocation_set_size(void);
int64_t   pcc_gc_install_forwarding(PyObject *from, PyObject *to);
PyObject *pcc_gc_relocate_copy(PyObject *o, int64_t size);
PyObject *pcc_gc_note_relocation_read(PyObject *o);

enum {
    PCC_GC_KIND_REFCOUNT_CYCLE = 0,
    PCC_GC_KIND_INCREMENTAL_TRICOLOR = 1,
    PCC_GC_KIND_CONCURRENT_MARK_SWEEP = 2,
    PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR = 3,
    PCC_GC_KIND_COLORED_RELOCATING = 4
};

enum {
    PCC_GC_COUNTER_ALLOCATIONS = 0,
    PCC_GC_COUNTER_WRITE_BARRIERS = 1,
    PCC_GC_COUNTER_READ_BARRIERS = 2,
    PCC_GC_COUNTER_SAFEPOINTS = 3,
    PCC_GC_COUNTER_PIN_BALANCE = 4,
    PCC_GC_COUNTER_WORK_STEPS = 5,
    PCC_GC_COUNTER_DEBT_BYTES = 6,
    PCC_GC_COUNTER_MAX_PAUSE_US = 7,
    PCC_GC_COUNTER_MINOR_ALLOCATIONS = 8,
    PCC_GC_COUNTER_MINOR_COLLECTIONS = 9,
    PCC_GC_COUNTER_MINOR_BYTES = 10,
    PCC_GC_COUNTER_CMS_WORKER_STARTS = 11,
    PCC_GC_COUNTER_CMS_QUEUE_PUSHES = 12,
    PCC_GC_COUNTER_CMS_WORKER_DRAINS = 13,
    PCC_GC_COUNTER_CMS_MUTATOR_ASSISTS = 14,
    PCC_GC_COUNTER_RELOCATION_FORWARDS = 15,
    PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS = 16,
    PCC_GC_COUNTER_RELOCATION_PIN_REJECTS = 17,
    PCC_GC_COUNTER_CMS_WORKER_TRACES = 18,
    PCC_GC_COUNTER_MINOR_ARENA_REFILLS = 19,
    PCC_GC_COUNTER_MINOR_ARENA_BUMPS = 20,
    PCC_GC_COUNTER_MINOR_ARENA_FALLBACKS = 21,
    PCC_GC_COUNTER_CMS_WORKER_STOPS = 22,
    PCC_GC_COUNTER_CMS_WB_FLUSHES = 23,
    PCC_GC_COUNTER_RELOCATION_SET_SIZE = 24,
    PCC_GC_COUNTER_FORWARDING_ENTRIES = 25,
    PCC_GC_COUNTER_STABLE_IDS = 26,
    PCC_GC_COUNTER_RELOCATION_FRAGMENTATION_SCORE = 27,
    PCC_GC_COUNTER_CMS_PRODUCTION_SCORE = 28,
    PCC_GC_COUNTER_CMS_WORKBUFFER_SCORE = 29,
    PCC_GC_COUNTER_GEN_MINOR_PRODUCTIVITY_SCORE = 30,
    PCC_GC_COUNTER_GEN_REMEMBERED_UPDATE_SCORE = 31,
    PCC_GC_COUNTER_SCHEDULER_ROOTS = 32,
    PCC_GC_COUNTER_FRAME_ROOT_SLOTS = 33,
    PCC_GC_COUNTER_COROUTINE_ROOT_SCORE = 34,
    PCC_GC_COUNTER_GENZGC_STORE_BARRIERS = 35,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_ENTRIES = 36,
    PCC_GC_COUNTER_GENZGC_YOUNG_PROMOTIONS = 37,
    PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATES = 38,
    PCC_GC_COUNTER_GENZGC_EVACUATED_BYTES = 39,
    PCC_GC_COUNTER_GENZGC_PAGE_POLICY_SCORE = 40,
    PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_DEFERS = 41,
    PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_DEFERRED_BYTES = 42,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATES = 43,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATES = 44,
    PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATE_BYTES = 45,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATE_BYTES = 46,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATE_BYTES = 47,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DRAIN_BATCHES = 48,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DRAINED_ENTRIES = 49,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_DUPLICATE_SKIPS = 50,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_HIGH_WATER = 51,
    PCC_GC_COUNTER_GENZGC_PAGE_PRESSURE_SCORE = 52,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_OWNER_FANOUT_HIGH_WATER = 53,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_OWNER_COUNT_HIGH_WATER = 54,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_INCOMPLETE_DRAINS = 55,
    PCC_GC_COUNTER_GENZGC_EVACUATION_INCOMPLETE_BATCHES = 56,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_BATCH_CAPACITY = 57,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MAX_BATCH_SIZE = 58,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_FULL_BATCHES = 59,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_ENTRIES = 60,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_DUPLICATE_SKIPS = 61,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_SET_HIGH_WATER = 62,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_CAPACITY = 63,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_PENDING = 64,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FLUSHES = 65,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FLUSHED_ENTRIES = 66,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_MEDIUM_FULL_FLUSHES = 67,
    PCC_GC_COUNTER_GENZGC_EVACUATION_EFFICIENCY_PER_MILLE = 68,
    PCC_GC_COUNTER_GENZGC_FRAGMENTATION_BACKLOG_BYTES = 69,
    PCC_GC_COUNTER_GENZGC_FRAGMENTATION_POLICY_SCORE = 70,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_LIMIT_BYTES = 71,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_LIMIT_BYTES = 72,
    PCC_GC_COUNTER_GENZGC_LARGE_DEFER_LIMIT_BYTES = 73,
    PCC_GC_COUNTER_GENZGC_LARGE_OBJECT_RECONSIDERATIONS = 74,
    PCC_GC_COUNTER_GENZGC_YOUNG_OBJECTS = 75,
    PCC_GC_COUNTER_GENZGC_OLD_OBJECTS = 76,
    PCC_GC_COUNTER_GENZGC_YOUNG_BYTES = 77,
    PCC_GC_COUNTER_GENZGC_OLD_BYTES = 78,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_OBJECTS = 79,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_OBJECTS = 80,
    PCC_GC_COUNTER_GENZGC_LARGE_PAGE_OBJECTS = 81,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_BYTES = 82,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_BYTES = 83,
    PCC_GC_COUNTER_GENZGC_LARGE_PAGE_BYTES = 84,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_CROSS_THREAD_MEDIUM_FLUSHES = 85,
    PCC_GC_COUNTER_GENZGC_STORE_BUFFER_CROSS_THREAD_MEDIUM_FLUSHED_ENTRIES = 86,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_ENTRIES = 87,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_SLOT_ENTRIES = 88,
    PCC_GC_COUNTER_GENZGC_REMEMBERED_PAGE_HIGH_WATER = 89,
    PCC_GC_COUNTER_GENZGC_ZPAGE_COUNT = 90,
    PCC_GC_COUNTER_GENZGC_ZPAGE_CAPACITY_BYTES = 91,
    PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTATION_BYTES = 92,
    PCC_GC_COUNTER_GENZGC_ZPAGE_LARGE_PAGES = 93,
    PCC_GC_COUNTER_GENZGC_ZPAGE_USED_BYTES = 94,
    PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTATION_PER_MILLE = 95,
    PCC_GC_COUNTER_GENZGC_ZPAGE_POLICY_SCORE = 96,
    PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_SLOTS = 97,
    PCC_GC_COUNTER_GENZGC_ZPAGE_DIRTY_PAGES = 98,
    PCC_GC_COUNTER_GENZGC_ZPAGE_FRAGMENTED_PAGES = 99,
    PCC_GC_COUNTER_GENZGC_ZPAGE_YOUNG_PAGES = 100,
    PCC_GC_COUNTER_GENZGC_ZPAGE_OLD_PAGES = 101,
    PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARDS = 102,
    PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARD_RATIO_PER_MILLE = 103,
    PCC_GC_COUNTER_GENZGC_ZPAGE_FREE_PAGES = 104,
    PCC_GC_COUNTER_GENZGC_ZPAGE_FREE_CAPACITY_BYTES = 105,
    PCC_GC_COUNTER_GENZGC_EVACUATION_CANDIDATE_ZPAGE_BYTES = 106,
    PCC_GC_COUNTER_GENZGC_SMALL_PAGE_CANDIDATE_ZPAGE_BYTES = 107,
    PCC_GC_COUNTER_GENZGC_MEDIUM_PAGE_CANDIDATE_ZPAGE_BYTES = 108,
    PCC_GC_COUNTER_GENZGC_EVACUATION_PAGE_CANDIDATES = 109
};

int64_t   pcc_gc_backend(void);
int64_t   pcc_gc_set_backend(int64_t backend);
const char *pcc_gc_backend_name(int64_t backend);
int64_t   pcc_gc_telemetry(int64_t metric);
void      pcc_gc_telemetry_reset(void);
int64_t   pcc_gc_step(int64_t budget);
int64_t   pcc_gc_backend4_verify_no_old_addresses(void);
int64_t   pcc_gc_backend4_fragmentation_score(void);
int64_t   pcc_gc_backend4_forwarding_entries(void);
int64_t   pcc_gc_backend4_stable_id_entries(void);
int64_t   pcc_gc_backend4_generation_barrier_score(void);
int64_t   pcc_gc_backend4_store_buffer_entries(void);
int64_t   pcc_gc_backend4_generation_promotion_score(void);
int64_t   pcc_gc_backend4_evacuation_candidate_score(void);
int64_t   pcc_gc_backend4_evacuated_bytes(void);
int64_t   pcc_gc_backend4_page_policy_score(void);
int64_t   pcc_gc_backend4_large_object_defer_score(void);
int64_t   pcc_gc_backend4_large_object_deferred_bytes(void);
int64_t   pcc_gc_backend4_small_page_candidate_score(void);
int64_t   pcc_gc_backend4_medium_page_candidate_score(void);
int64_t   pcc_gc_backend4_evacuation_candidate_bytes(void);
int64_t   pcc_gc_backend4_small_page_candidate_bytes(void);
int64_t   pcc_gc_backend4_medium_page_candidate_bytes(void);
int64_t   pcc_gc_backend4_evacuation_candidate_zpage_bytes(void);
int64_t   pcc_gc_backend4_small_page_candidate_zpage_bytes(void);
int64_t   pcc_gc_backend4_medium_page_candidate_zpage_bytes(void);
int64_t   pcc_gc_backend4_evacuation_page_candidate_score(void);
int64_t   pcc_gc_backend4_evacuation_page_candidate_bytes(void);
int64_t   pcc_gc_backend4_evacuation_page_dirty_cards(void);
int64_t   pcc_gc_backend4_store_buffer_drain_batches(void);
int64_t   pcc_gc_backend4_store_buffer_drained_entries(void);
int64_t   pcc_gc_backend4_store_buffer_duplicate_skips(void);
int64_t   pcc_gc_backend4_store_buffer_high_water(void);
int64_t   pcc_gc_backend4_page_pressure_score(void);
int64_t   pcc_gc_backend4_store_buffer_owner_fanout_high_water(void);
int64_t   pcc_gc_backend4_store_buffer_owner_count_high_water(void);
int64_t   pcc_gc_backend4_store_buffer_incomplete_drains(void);
int64_t   pcc_gc_backend4_evacuation_incomplete_batches(void);
int64_t   pcc_gc_backend4_store_buffer_batch_capacity(void);
int64_t   pcc_gc_backend4_store_buffer_max_batch_size(void);
int64_t   pcc_gc_backend4_store_buffer_full_batches(void);
int64_t   pcc_gc_backend4_store_buffer_medium_capacity(void);
int64_t   pcc_gc_backend4_store_buffer_medium_pending(void);
int64_t   pcc_gc_backend4_store_buffer_medium_flushes(void);
int64_t   pcc_gc_backend4_store_buffer_medium_flushed_entries(void);
int64_t   pcc_gc_backend4_store_buffer_medium_full_flushes(void);
int64_t   pcc_gc_backend4_store_buffer_cross_thread_medium_flushes(void);
int64_t   pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries(void);
int64_t   pcc_gc_backend4_remembered_set_entries(void);
int64_t   pcc_gc_backend4_remembered_set_duplicate_skips(void);
int64_t   pcc_gc_backend4_remembered_set_high_water(void);
int64_t   pcc_gc_backend4_remembered_page_entries(void);
int64_t   pcc_gc_backend4_remembered_page_slot_entries(void);
int64_t   pcc_gc_backend4_remembered_page_high_water(void);
int64_t   pcc_gc_backend4_remembered_page_contains_slot(PyObject **slot);
int64_t   pcc_gc_backend4_remembered_page_clear_slot(PyObject **slot);
int64_t   pcc_gc_backend4_zpage_contains_remembered_card(PyObject *owner, PyObject **slot);
int64_t   pcc_gc_backend4_zpage_clear_remembered_card(PyObject *owner, PyObject **slot);
#ifndef PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES
#define PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES 512
#endif
int64_t   pcc_gc_backend4_zpage_owner_offset_bytes(PyObject *owner);
int64_t   pcc_gc_backend4_zpage_owner_size_bytes(PyObject *owner);
int64_t   pcc_gc_backend4_zpage_owner_span_card(PyObject *owner);
int64_t   pcc_gc_backend4_zpage_owner_slot_span_card(PyObject *owner, PyObject **slot);
int64_t   pcc_gc_backend4_zpage_register_owner_payload_span(PyObject *owner, void *base, int64_t size_bytes);
int64_t   pcc_gc_backend4_zpage_count(void);
int64_t   pcc_gc_backend4_zpage_capacity_bytes(void);
int64_t   pcc_gc_backend4_zpage_fragmentation_bytes(void);
int64_t   pcc_gc_backend4_zpage_large_pages(void);
int64_t   pcc_gc_backend4_zpage_used_bytes(void);
int64_t   pcc_gc_backend4_zpage_allocated_bytes(void);
int64_t   pcc_gc_backend4_zpage_reclaimable_gap_bytes(void);
int64_t   pcc_gc_backend4_zpage_span_bytes(void);
int64_t   pcc_gc_backend4_zpage_fragmentation_per_mille(void);
int64_t   pcc_gc_backend4_zpage_policy_score(void);
int64_t   pcc_gc_backend4_zpage_remembered_slots(void);
int64_t   pcc_gc_backend4_zpage_remembered_cards(void);
int64_t   pcc_gc_backend4_zpage_remembered_card_ratio_per_mille(void);
int64_t   pcc_gc_backend4_zpage_dirty_pages(void);
int64_t   pcc_gc_backend4_zpage_fragmented_pages(void);
int64_t   pcc_gc_backend4_zpage_young_pages(void);
int64_t   pcc_gc_backend4_zpage_old_pages(void);
int64_t   pcc_gc_backend4_zpage_free_pages(void);
int64_t   pcc_gc_backend4_zpage_free_capacity_bytes(void);
int64_t   pcc_gc_backend4_zpage_free_span_bytes(void);
int64_t   pcc_gc_backend4_evacuation_efficiency_per_mille(void);
int64_t   pcc_gc_backend4_fragmentation_backlog_bytes(void);
int64_t   pcc_gc_backend4_fragmentation_policy_score(void);
int64_t   pcc_gc_backend4_small_page_limit_bytes(void);
int64_t   pcc_gc_backend4_medium_page_limit_bytes(void);
int64_t   pcc_gc_backend4_large_defer_limit_bytes(void);
int64_t   pcc_gc_backend4_large_object_reconsiderations(void);
int64_t   pcc_gc_backend4_young_object_count(void);
int64_t   pcc_gc_backend4_old_object_count(void);
int64_t   pcc_gc_backend4_young_bytes(void);
int64_t   pcc_gc_backend4_old_bytes(void);
int64_t   pcc_gc_backend4_small_page_object_count(void);
int64_t   pcc_gc_backend4_medium_page_object_count(void);
int64_t   pcc_gc_backend4_large_page_object_count(void);
int64_t   pcc_gc_backend4_small_page_live_bytes(void);
int64_t   pcc_gc_backend4_medium_page_live_bytes(void);
int64_t   pcc_gc_backend4_large_page_live_bytes(void);
int64_t   pcc_gc_backend2_production_score(void);
int64_t   pcc_gc_backend2_worker_buffer_score(void);
int64_t   pcc_gc_backend3_minor_productivity_score(void);
int64_t   pcc_gc_backend3_remembered_update_score(void);
int64_t   pcc_gc_scheduler_root_count(void);
int64_t   pcc_gc_frame_root_slot_count(void);
int64_t   pcc_gc_continuation_root_slot_count(void);
int64_t   pcc_gc_coroutine_root_score(void);
PyObject *py_gc_callbacks_list(void);
void      py_gc_callbacks_append(PyObject *callback);
void      py_gc_callbacks_remove(PyObject *callback);

/* ---- Runtime threading substrate -------------------------------------- */
/*
 * This is the shared substrate used by future concurrent GC backends and
 * native threading support. It is not a Python-level ``threading`` module.
 * The default build remains single-threaded: pcc_threads_enabled() returns
 * 0, pcc_current_thread_id() returns a stable synthetic id, and safepoint /
 * stop-the-world hooks are no-ops.
 *
 * Build with PCC_WITH_THREADS=1 to enable pthread-backed primitives. That
 * alone does not make Python object mutation free-threaded; containers, GC
 * side tables, and user-visible threading APIs are staged separately.
 */
#define PCC_REFCOUNT_KIND_NONATOMIC 0
#define PCC_REFCOUNT_KIND_ATOMIC    1
#define PCC_REFCOUNT_KIND_BIASED    2
#define PCC_REFCOUNT_KIND_DEFERRED  3

enum {
    PCC_REFCOUNT_STRATEGY_NONATOMIC = PCC_REFCOUNT_KIND_NONATOMIC,
    PCC_REFCOUNT_STRATEGY_ATOMIC    = PCC_REFCOUNT_KIND_ATOMIC,
    PCC_REFCOUNT_STRATEGY_BIASED    = PCC_REFCOUNT_KIND_BIASED,
    PCC_REFCOUNT_STRATEGY_DEFERRED  = PCC_REFCOUNT_KIND_DEFERRED
};

int64_t pcc_threads_enabled(void);
int64_t pcc_current_thread_id(void);
int64_t pcc_refcount_strategy(void);
void    pcc_thread_safepoint(void);
int64_t pcc_stop_the_world(void);
int64_t pcc_resume_world(void);
int64_t pcc_runtime_now_us(void);
int64_t pcc_runtime_monotonic_us(void);
void   *pcc_py_gc_minor_current_get(void);
void    pcc_py_gc_minor_current_set(void *block);
void   *pcc_py_gc_pending_minor_block_get(void);
void    pcc_py_gc_pending_minor_block_set(void *block);
void    pcc_py_gc_minor_graph_lock(void);
void    pcc_py_gc_minor_graph_unlock(void);
int32_t pcc_py_atomic_i32_load(void *slot);
void    pcc_py_atomic_i32_store(void *slot, int32_t value);
int32_t pcc_py_atomic_i32_add_fetch(void *slot, int32_t delta);
int64_t pcc_py_atomic_i64_load(void *slot);
void    pcc_py_atomic_i64_store(void *slot, int64_t value);
int64_t pcc_py_atomic_i64_add_fetch(void *slot, int64_t delta);
int64_t pcc_py_atomic_i64_dec_if_positive(void *slot);

/* ---- Native threading module substrate -------------------------------- */
/* Low-level C ABI backing the Python-visible threading shim. These helpers
 * are deliberately small: user-facing semantics live in pcc.py_stdlib.threading
 * while the runtime owns mutex/condition storage and thread identity. */
int64_t   py_threading_get_ident(void);
PyObject *py_threading_current_thread(void);
PyObject *py_threading_lock_new(void);
int64_t   py_threading_lock_acquire(PyObject *lock);
int64_t   py_threading_lock_acquire_vthread(PyObject *lock);
int64_t   py_threading_lock_release(PyObject *lock);
PyObject *py_threading_rlock_new(void);
int64_t   py_threading_rlock_acquire(PyObject *lock);
int64_t   py_threading_rlock_release(PyObject *lock);
PyObject *py_threading_event_new(void);
int64_t   py_threading_event_set(PyObject *event);
int64_t   py_threading_event_clear(PyObject *event);
int64_t   py_threading_event_is_set(PyObject *event);
int64_t   py_threading_event_wait(PyObject *event);
int64_t   py_threading_event_wait_vthread(PyObject *event);
PyObject *py_threading_condition_new(PyObject *lock);
int64_t   py_threading_condition_acquire(PyObject *cond);
int64_t   py_threading_condition_release(PyObject *cond);
int64_t   py_threading_condition_wait(PyObject *cond);
int64_t   py_threading_condition_wait_vthread(PyObject *cond);
int64_t   py_threading_condition_notify(PyObject *cond);
PyObject *py_threading_semaphore_new(int64_t initial);
int64_t   py_threading_semaphore_acquire(PyObject *sem);
int64_t   py_threading_semaphore_acquire_vthread(PyObject *sem);
int64_t   py_threading_semaphore_release(PyObject *sem);
PyObject *py_threading_thread_new(PyObject *callable, PyObject *args);
int64_t   py_threading_thread_is_alive(PyObject *thread);
int64_t   py_threading_thread_start(PyObject *thread);
int64_t   py_threading_thread_join(PyObject *thread);

/* ---- INCREF/DECREF compatibility -------------------------------------- */
void py_incref(PyObject *o);
void py_decref(PyObject *o);

/* ---- None -------------------------------------------------------------- */
extern PyObject *const py_None;
extern PyObject *const py_NotImplemented;

/* ---- Bool -------------------------------------------------------------- */
extern PyObject *const py_True;
extern PyObject *const py_False;
PyObject *py_bool_from_bit(int b);           /* b: 0 or 1 */

/* ---- Tagged int (fast path) + bignum (slow path) ---------------------- */
/* Tagged: low bit = 1 means tagged int; real value is (val >> 1).
 * Non-tagged: regular PyObject* with PY_TYPE_INT header. */
PyObject *py_int_from_i64(int64_t v);
int64_t   py_int_to_i64(PyObject *o, int *overflow);   /* returns 0 on overflow */
PyObject *py_int_add(PyObject *a, PyObject *b);
PyObject *py_int_sub(PyObject *a, PyObject *b);
PyObject *py_int_mul(PyObject *a, PyObject *b);
PyObject *py_int_floordiv(PyObject *a, PyObject *b);   /* Python floor semantics */
PyObject *py_int_truediv(PyObject *a, PyObject *b);    /* returns float */
PyObject *py_int_mod(PyObject *a, PyObject *b);        /* Python sign semantics */
PyObject *py_int_pow(PyObject *a, PyObject *b);
PyObject *py_int_pow_mod(PyObject *base, PyObject *exp, PyObject *mod);  /* 3-arg pow */
PyObject *py_int_neg(PyObject *a);
PyObject *py_int_and(PyObject *a, PyObject *b);
PyObject *py_int_or(PyObject *a, PyObject *b);
PyObject *py_int_xor(PyObject *a, PyObject *b);
PyObject *py_int_shl(PyObject *a, PyObject *b);
PyObject *py_int_shr(PyObject *a, PyObject *b);
int       py_int_cmp(PyObject *a, PyObject *b);        /* -1, 0, 1 */
PyObject *py_int_format_hex(PyObject *o, int64_t width, int64_t zero_pad);
PyObject *py_int_format_decimal(PyObject *o, int64_t width, int64_t zero_pad, int64_t comma);
/* bin()/hex()/oct() builtins: base-prefixed string ("0b101"/"0xff"/"0o10"). */
PyObject *py_builtin_bin(PyObject *o);
PyObject *py_builtin_hex(PyObject *o);
PyObject *py_builtin_oct(PyObject *o);
/* ``int(str)`` / ``int(str, base)`` — returns a tagged or heap int
 * matching strtoll(); on parse error, returns NULL. Base 0 auto-
 * detects 0x / 0o / 0b prefixes. Use base 10 for Python's default. */
PyObject *py_int_from_cstr(const char *s, int base);
PyObject *py_int_from_cstr_or_raise(const char *s, int base);  /* ValueError on invalid */

/* ---- Float ------------------------------------------------------------- */
PyObject *py_float_from_f64(double v);
double    py_float_to_f64(PyObject *o);
PyObject *py_float_add(PyObject *a, PyObject *b);
PyObject *py_float_sub(PyObject *a, PyObject *b);
PyObject *py_float_mul(PyObject *a, PyObject *b);
PyObject *py_float_format_fixed(PyObject *o, int64_t precision);
PyObject *py_float_repr_shortest(PyObject *o);
int64_t   py_float_is_integer(PyObject *o);     /* float.is_integer() -> bool */
/* ... sub, mul, div, mod, pow, neg, cmp ... */

/* ---- Complex ----------------------------------------------------------- */
PyObject *py_complex_new(double real, double imag);
PyObject *py_complex_real(PyObject *o);
PyObject *py_complex_imag(PyObject *o);
PyObject *py_complex_add(PyObject *a, PyObject *b);

/* ---- Bytes / bytearray / memoryview ------------------------------------ */
PyObject *py_bytes_new(const char *data, int64_t byte_len);
PyObject *py_bytearray_from_obj(PyObject *o);
PyObject *py_bytes_from_obj(PyObject *o);
PyObject *py_memoryview_new(PyObject *o);
PyObject *py_bytes_decode(PyObject *o);
PyObject *py_bytes_hex(PyObject *o);    /* bytes.hex() -> lowercase hex str */
PyObject *py_bytes_getitem(PyObject *o, PyObject *k);
PyObject *py_bytes_slice(PyObject *o, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_bytes_concat(PyObject *a, PyObject *b);
PyObject *py_bytes_repeat(PyObject *src, int64_t count);
int64_t   py_bytes_len(PyObject *o);
int64_t   py_bytearray_setitem(PyObject *o, PyObject *k, PyObject *v);

/* ---- Str --------------------------------------------------------------- */
PyObject *py_str_new(const char *utf8, int64_t byte_len);
int64_t   py_str_len(PyObject *s);             /* in codepoints */
int64_t   py_str_byte_len(PyObject *s);        /* in UTF-8 bytes */
const char *py_str_utf8(PyObject *s);          /* borrowed, NUL-terminated */
int64_t   py_str_ord(PyObject *s);             /* first codepoint, -1 on empty/invalid */
int64_t   py_str_ord_at_i64(PyObject *s, int64_t i); /* codepoint at index, -1 invalid */
int64_t   py_str_byte_at_i64(PyObject *s, int64_t i); /* raw UTF-8 byte, -1 invalid */
PyObject *py_str_latin1_encode(PyObject *s);
PyObject *py_str_utf8_encode(PyObject *s);
PyObject *py_str_byte_slice_i64(PyObject *s, int64_t lo, int64_t hi);
PyObject *py_str_concat(PyObject *a, PyObject *b);
PyObject *py_str_repeat(PyObject *s, PyObject *n);
PyObject *py_str_slice(PyObject *s, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_str_index(PyObject *s, PyObject *i);    /* returns single-char str */
int64_t   py_str_eq(PyObject *a, PyObject *b);
int64_t   py_str_contains(PyObject *s, PyObject *sub);
int64_t   py_str_find(PyObject *s, PyObject *sub);   /* -1 if not found */
int64_t   py_str_rfind(PyObject *s, PyObject *sub);  /* -1 if not found */
PyObject *py_str_upper(PyObject *s);
PyObject *py_str_lower(PyObject *s);
PyObject *py_str_capitalize(PyObject *s);
PyObject *py_str_swapcase(PyObject *s);
PyObject *py_str_title(PyObject *s);
PyObject *py_str_casefold(PyObject *s);
PyObject *py_str_expandtabs(PyObject *s, int64_t tabsize);
PyObject *py_str_rpartition(PyObject *s, PyObject *sep);
PyObject *py_str_translate(PyObject *s, PyObject *table);  /* dict {ord:ord|str|None} */
PyObject *py_str_maketrans(PyObject *x, PyObject *y);      /* {ord(x[i]):ord(y[i])} */
PyObject *py_str_strip(PyObject *s);
PyObject *py_str_split(PyObject *s, PyObject *sep);  /* returns list */
PyObject *py_str_split_maxsplit(PyObject *s, PyObject *sep, int64_t maxsplit);
PyObject *py_str_join(PyObject *sep, PyObject *list);
PyObject *py_str_replace(PyObject *s, PyObject *old, PyObject *replacement);
PyObject *py_str_replace_count(PyObject *s, PyObject *old, PyObject *replacement, int64_t maxreplace);
int64_t   py_str_startswith(PyObject *s, PyObject *prefix);
int64_t   py_str_endswith(PyObject *s, PyObject *suffix);
PyObject *py_chr_from_i64(int64_t codepoint);
PyObject *py_json_loads(PyObject *text);
PyObject *py_json_dumps(PyObject *obj);
PyObject *py_copy_copy(PyObject *o);
PyObject *py_copy_deepcopy(PyObject *o);
PyObject *py_pickle_dumps(PyObject *o, PyObject *protocol);
PyObject *py_pickle_loads(PyObject *data);

/* ---- List -------------------------------------------------------------- */
PyObject *py_list_new(int64_t initial_capacity);
void      py_list_append(PyObject *lst, PyObject *item);
PyObject *py_list_get(PyObject *lst, int64_t i);     /* new ref */
PyObject *py_list_getitem(PyObject *lst, int64_t i); /* a[i]; IndexError if OOB */
int64_t   py_list_get_i64(PyObject *lst, int64_t i); /* borrowed typed-int fast path */
int64_t   py_list_get_i64_nonnegative(PyObject *lst, int64_t i); /* non-negative typed-int fast path */
void      py_list_set(PyObject *lst, int64_t i, PyObject *item);
int64_t   py_list_len(PyObject *lst);
PyObject *py_list_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step);
int64_t   py_list_set_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step, PyObject *replacement);
int64_t   py_list_del_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_list_concat(PyObject *a, PyObject *b);
PyObject *py_list_repeat(PyObject *src, int64_t count);
void      py_list_extend(PyObject *a, PyObject *b);
void      py_list_insert(PyObject *lst, int64_t i, PyObject *item);
PyObject *py_list_pop(PyObject *lst, int64_t i);
void      py_list_remove(PyObject *lst, PyObject *item);
void      py_list_clear(PyObject *lst);
void      py_obj_clear(PyObject *obj);
int64_t   py_list_contains(PyObject *lst, PyObject *item);
int64_t   py_list_index(PyObject *lst, PyObject *item);
int64_t   py_list_count(PyObject *lst, PyObject *item);
void      py_list_reverse(PyObject *lst);

/* ---- Dict -------------------------------------------------------------- */
PyObject *py_dict_new(void);
void      py_dict_set(PyObject *d, PyObject *k, PyObject *v);
PyObject *py_dict_get(PyObject *d, PyObject *k);     /* NULL if missing */
PyObject *py_dict_getitem(PyObject *d, PyObject *k); /* d[k]; KeyError if missing */
PyObject *py_dict_fromkeys(PyObject *iterable, PyObject *value);  /* dict.fromkeys */
PyObject *py_dict_get_default(PyObject *d, PyObject *k, PyObject *def);
/* Returns 1 if k is in d, 0 otherwise. int64_t for pcc-Python ABI parity. */
int64_t   py_dict_contains(PyObject *d, PyObject *k);
/* Returns 0 on success, -1 if missing. int64_t for pcc-Python ABI parity. */
int64_t   py_dict_del(PyObject *d, PyObject *k);
void      py_dict_clear(PyObject *d);
int64_t   py_dict_len(PyObject *d);
PyObject *py_dict_keys(PyObject *d);                 /* list */
PyObject *py_dict_values(PyObject *d);               /* list */
PyObject *py_dict_items(PyObject *d);                /* list of tuples */
void      py_dict_update(PyObject *dst, PyObject *src);

/* ---- Tuple ------------------------------------------------------------- */
PyObject *py_tuple_new(int64_t n);
void      py_tuple_set_item(PyObject *t, int64_t i, PyObject *item); /* during construction only */
PyObject *py_tuple_get(PyObject *t, int64_t i);
int64_t   py_tuple_len(PyObject *t);
int64_t   py_tuple_count(PyObject *t, PyObject *item);
int64_t   py_tuple_index(PyObject *t, PyObject *item);  /* raises ValueError if absent */
PyObject *py_tuple_concat(PyObject *a, PyObject *b);
PyObject *py_tuple_repeat(PyObject *t, int64_t count);
PyObject *py_tuple_slice(PyObject *t, PyObject *lo, PyObject *hi, PyObject *step);

/* ---- Set --------------------------------------------------------------- */
PyObject *py_set_new(void);
void      py_set_add(PyObject *s, PyObject *item);
void      py_set_update(PyObject *dst, PyObject *src);
PyObject *py_set_intersection(PyObject *a, PyObject *b);
PyObject *py_set_difference(PyObject *a, PyObject *b);
PyObject *py_set_symmetric_difference(PyObject *a, PyObject *b);
int64_t   py_set_issubset(PyObject *a, PyObject *b);
int64_t   py_set_issuperset(PyObject *a, PyObject *b);
PyObject *py_set_items(PyObject *s);                 /* list */
/* Returns 1 if item is in the set, 0 otherwise. Returns int64_t so the
 * pcc-Python port (py_set.py) emits under pcc's default `int` lowering
 * without a type mismatch. */
int64_t   py_set_contains(PyObject *s, PyObject *item);
/* Removes item; returns 0 on success, -1 if item not present. */
int64_t   py_set_remove(PyObject *s, PyObject *item);
int64_t   py_set_len(PyObject *s);

/* ---- Generic object ops ----------------------------------------------- */
PyObject *py_obj_call(PyObject *callable, PyObject *args_tuple, PyObject *kwargs_dict);
PyObject *py_obj_call_method1(PyObject *o, const char *name, PyObject *arg);
PyObject *py_obj_add(PyObject *a, PyObject *b);
PyObject *py_obj_mod(PyObject *a, PyObject *b);
PyObject *py_str_mod(PyObject *fmt, PyObject *args);
PyObject *py_weakref_new(PyObject *target, PyObject *callback);
PyObject *py_weakref_call(PyObject *ref);
void      py_weakref_invalidate(PyObject *target);
PyObject *py_weak_value_dict_new(void);
int64_t   py_weak_value_dict_set(PyObject *dict, PyObject *key, PyObject *value);
int64_t   py_weak_value_dict_contains(PyObject *dict, PyObject *key);
int64_t   py_weak_value_dict_len(PyObject *dict);
PyObject *py_weak_key_dict_new(void);
int64_t   py_weak_key_dict_set(PyObject *dict, PyObject *key, PyObject *value);
int64_t   py_weak_key_dict_len(PyObject *dict);
void      py_dealloc_weakref(PyObject *ref);
PyObject *py_obj_getattr(PyObject *o, const char *name);
PyObject *py_obj_getattr_default(PyObject *o, const char *name);
int64_t   py_obj_setattr(PyObject *o, const char *name, PyObject *v);
int64_t   py_obj_delattr(PyObject *o, const char *name);
PyObject *py_obj_type_name(PyObject *o);
PyObject *py_type_builtin(PyObject *o);
PyObject *py_obj_getitem(PyObject *o, PyObject *k);
int64_t   py_obj_setitem(PyObject *o, PyObject *k, PyObject *v);
int64_t   py_obj_delitem(PyObject *o, PyObject *k);
int64_t   py_obj_len(PyObject *o);
int64_t   py_obj_contains(PyObject *container, PyObject *item);
PyObject *py_str_splitlines(PyObject *s);
PyObject *py_str_splitlines_keepends(PyObject *s, int keepends);
PyObject *py_str_lstrip(PyObject *s);
PyObject *py_str_rstrip(PyObject *s);
PyObject *py_str_strip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_lstrip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_rstrip_chars(PyObject *s, PyObject *chars);
int64_t   py_str_count(PyObject *s, PyObject *sub);
int64_t   py_str_isdigit(PyObject *s);
int64_t   py_str_isalpha(PyObject *s);
int64_t   py_str_isspace(PyObject *s);
int64_t   py_str_isalnum(PyObject *s);
int64_t   py_str_isupper(PyObject *s);
int64_t   py_str_islower(PyObject *s);
int64_t   py_str_index_of(PyObject *s, PyObject *sub);  /* find(); ValueError if absent */
int64_t   py_str_rindex_of(PyObject *s, PyObject *sub); /* rfind(); ValueError if absent */
/* ``sorted(x)`` — returns a new list with elements of ``x`` in
 * py_obj_eq / py_int_cmp order. ``x`` must be any py_obj_len /
 * py_obj_getitem-friendly container. Only numeric / string
 * element types order correctly; mixed types fall back to
 * py_obj_hash order (stable but not Python-equivalent). */
PyObject *py_obj_sorted(PyObject *x);
/* int64_t returns for pcc-Python ABI parity (default-int lowering). */
int64_t   py_obj_truthy(PyObject *o);                /* 0 or 1 */
int64_t   py_obj_type_tag(PyObject *o);
int64_t   py_obj_eq(PyObject *a, PyObject *b);
int       py_obj_cmp_threeway(PyObject *a, PyObject *b);  /* -1 / 0 / 1 */
PyObject *py_obj_min_max(PyObject *iterable, int64_t want_max);  /* min/max over iterable */
int64_t   py_obj_lt(PyObject *a, PyObject *b);
int64_t   py_obj_le(PyObject *a, PyObject *b);
int64_t   py_obj_gt(PyObject *a, PyObject *b);
int64_t   py_obj_ge(PyObject *a, PyObject *b);
int64_t   py_obj_hash(PyObject *o);
int64_t   py_obj_index_i64(PyObject *o);
PyObject *py_obj_repr(PyObject *o);
PyObject *py_obj_ascii(PyObject *o);
PyObject *py_obj_str(PyObject *o);
int64_t   py_obj_isinstance(PyObject *o, PyObject *cls);
PyObject *py_obj_iter(PyObject *o);
PyObject *py_obj_next(PyObject *it);

/* User-defined protocol / dunder dispatch helpers. These are exposed so the
 * C runtime and pcc-Python runtime ports can share one ABI surface. */
PyObject *py_user_str_dispatch(PyObject *o);
PyObject *py_user_repr_dispatch(PyObject *o);
int64_t   py_user_hash_dispatch(PyObject *o, int64_t *handled);
int64_t   py_user_eq_dispatch(PyObject *a, PyObject *b);
PyObject *py_user_iter_dispatch(PyObject *o);
PyObject *py_user_next_dispatch(PyObject *o);
PyObject *py_user_matmul_dispatch(PyObject *a, PyObject *b);

PyObject *py_call_merge_posargs(PyObject *base_tuple, PyObject *star_args);
PyObject *py_call_merge_kwargs(PyObject *base_kwargs, PyObject *star_kwargs);
PyObject *py_zip_star(PyObject *rows);
PyObject *py_obj_call_splat(PyObject *callable,
                            PyObject *base_args,
                            PyObject *star_args,
                            PyObject *base_kwargs,
                            PyObject *star_kwargs);
PyObject *py_module_attrs_dict(const char *module_name, int64_t create);
int64_t   py_module_attr_set(const char *module_name,
                             const char *attr_name,
                             PyObject *value);
PyObject *py_module_attr_get(const char *module_name, const char *attr_name);
PyObject *py_module_attr_value_or_default(PyObject **slot,
                                          PyObject *default_value);
int64_t   py_module_attr_del(const char *module_name, const char *attr_name);
int64_t   py_module_attr_len(const char *module_name);
PyObject *py_native_extension_import(const char *module_name, const char *path);
PyObject *py_native_extension_import_by_name(const char *module_name);

/* ---- Native function objects -------------------------------------------- */
/* A pcc-native function value. `entry` has ABI:
 *
 *     PyObject *entry(PyObject *captures_tuple, PyObject *args_tuple)
 *
 * Codegen synthesizes that adapter around the real typed FuncDef ABI, so the
 * runtime does not need to know the function's concrete parameter types.
 */
PyObject *py_func_new(void *entry, PyObject *captures_tuple);
PyObject *py_func_new_named(void *entry, PyObject *captures_tuple, const char *name);
PyObject *py_func_new_bound(
    void *entry,
    PyObject *captures_tuple,
    const char *name,
    PyObject *self_obj
);
PyObject *py_func_call(PyObject *callable, PyObject *args_tuple);
PyObject *py_functools_partial(PyObject *fn, PyObject *bound_args);
PyObject *py_instance_bind_method(PyObject *method, PyObject *self, const char *name);
PyObject *py_property_new(PyObject *fget, PyObject *fset, PyObject *fdel);
PyObject *py_classmethod_new(PyObject *func);

/* ---- Native generator objects ------------------------------------------ */
PyObject *py_gen_new(void *resume, PyObject *frame);
PyObject *py_gen_next(PyObject *gen);
PyObject *py_gen_send(PyObject *gen, PyObject *value);
PyObject *py_gen_throw(PyObject *gen, PyObject *exc);
PyObject *py_gen_close(PyObject *gen);
PyObject *py_gen_take_send(PyObject *gen);
int64_t   py_gen_state(PyObject *gen);
void      py_gen_set_state(PyObject *gen, int64_t state);
void      py_gen_set_done(PyObject *gen);
int64_t   py_gen_is_done(PyObject *gen);
PyObject *py_gen_finish(PyObject *gen, PyObject *value);

/* ---- Native coroutine shell objects ------------------------------------ */
PyObject *py_coroutine_new(const char *name);
PyObject *py_coroutine_new_native(const char *name, void *entry, PyObject *captures_tuple, PyObject *args_tuple);
PyObject *py_coroutine_run(PyObject *coro);
PyObject *py_coroutine_close(PyObject *coro);
PyObject *py_coroutine_class(void);
int64_t   py_coroutine_is_done(PyObject *coro);
PyObject *py_coroutine_get_result(PyObject *coro);
PyObject *py_await(PyObject *awaitable);
PyObject *py_asyncio_sleep(PyObject *delay);
PyObject *py_continuation_class(void);
PyObject *py_continuation_new(const void *frame_map, PyObject **slots, void *resume_pc);
PyObject *py_continuation_new_typed(const void *frame_map, PyObject **slots, void *resume_pc);
int64_t   py_continuation_mount(PyObject *cont, PyObject **slots_out);
int64_t   py_continuation_unmount(PyObject *cont, PyObject **slots_in, void *resume_pc);
int64_t   py_continuation_is_mounted(PyObject *cont);
void     *py_continuation_resume_pc(PyObject *cont);
int64_t   py_continuation_resume_abi(PyObject *cont);
int64_t   py_continuation_slot_count(PyObject *cont);
PyObject *py_continuation_get_slot(PyObject *cont, int64_t index);
int64_t   py_continuation_set_slot(PyObject *cont, int64_t index, PyObject *value);
PyObject *py_virtual_thread_new(PyObject *continuation);
PyObject *py_virtual_thread_current(void);
int64_t   py_virtual_thread_resume_generator(PyObject *vthread, PyObject *continuation);
int64_t   py_virtual_thread_start(PyObject *vthread);
int64_t   py_virtual_thread_park(PyObject *vthread);
int64_t   py_virtual_thread_unpark(PyObject *vthread);
int64_t   py_virtual_thread_sleep(PyObject *vthread, int64_t delay_ms);
int64_t   py_virtual_thread_poll_timers(void);
int64_t   py_virtual_thread_timer_count(void);
int64_t   py_virtual_thread_block_on_fd(PyObject *vthread, int64_t fd, int64_t events, int64_t timeout_ms);
int64_t   py_virtual_thread_poll_io(int64_t timeout_ms);
int64_t   py_virtual_thread_io_wait_count(void);
int64_t   py_virtual_thread_pin_enter(PyObject *vthread, const char *reason);
int64_t   py_virtual_thread_pin_leave(PyObject *vthread);
int64_t   py_virtual_thread_pin_count(PyObject *vthread);
int64_t   py_virtual_thread_pinned_count(void);
int64_t   py_virtual_thread_pin_event_count(void);
PyObject *py_virtual_thread_poll_ready(void);
int64_t   py_virtual_thread_ready_count(void);
int64_t   py_virtual_thread_carrier_count(void);
int64_t   py_virtual_thread_carrier_steal_count(void);
int64_t   py_virtual_thread_run_once(void);
int64_t   py_virtual_thread_run_until_idle(int64_t max_steps);
int64_t   py_virtual_thread_run_carrier_pool(int64_t carrier_count, int64_t max_steps);
int64_t   py_virtual_thread_carrier_pool_start(int64_t carrier_count);
int64_t   py_virtual_thread_carrier_pool_stop(void);
int64_t   py_virtual_thread_state(PyObject *vthread);
int64_t   py_virtual_thread_complete(PyObject *vthread, PyObject *result);
PyObject *py_virtual_thread_result(PyObject *vthread);
PyObject *py_task_new(PyObject *coro);
PyObject *py_task_step(PyObject *task);
int64_t   py_task_is_done(PyObject *task);
void      py_task_set_result(PyObject *task, PyObject *result);
void      py_task_set_waiter(PyObject *task, PyObject *waiter);
PyObject *py_task_get_coro(PyObject *task);
PyObject *py_task_get_result(PyObject *task);
PyObject *py_task_get_waiter(PyObject *task);
PyObject *py_context_enter(PyObject *manager);
int64_t   py_context_exit(PyObject *manager, PyObject *exc_type, PyObject *exc, PyObject *tb);
PyObject *py_obj_format(PyObject *o, PyObject *spec);

/* ---- File I/O ---------------------------------------------------------- */
PyObject *py_file_open(PyObject *path, PyObject *mode);
PyObject *py_file_read_all(PyObject *file);
PyObject *py_file_read(PyObject *file, int64_t limit);
PyObject *py_file_write(PyObject *file, PyObject *text);
void      py_file_close(PyObject *file);
PyObject *py_fileinput_new(PyObject *files, PyObject *openhook);
PyObject *py_fileinput_readline(PyObject *state);
PyObject *py_fileinput_filename(PyObject *state);
PyObject *py_fileinput_lineno(PyObject *state);
PyObject *py_fileinput_filelineno(PyObject *state);
PyObject *py_fileinput_isfirstline(PyObject *state);
PyObject *py_fileinput_close(PyObject *state);

/* ---- Printing ---------------------------------------------------------- */
void py_print(PyObject *o);                 /* writes repr + "\n" to stdout */
void py_print_many(PyObject *args_tuple, PyObject *sep, PyObject *end);
PyObject *py_sys_stdout_write(PyObject *text);
PyObject *py_sys_stderr_write(PyObject *text);

/* ---- Process startup --------------------------------------------------- */
/* Borrow the host process argc/argv so compiled Python programs can
 * observe their command-line arguments (directly or through CPython
 * fallback modules such as argparse). */
void py_set_program_args(int argc, const char **argv);
int64_t py_program_argc(void);
const char *py_program_argv(int64_t index);
void py_process_exit(int64_t code);
PyObject *py_sys_executable_str(void);
PyObject *py_sys_prefix_str(int64_t kind);
PyObject *py_os_getpid(void);
PyObject *py_subprocess_check_output(PyObject *argv);
int64_t py_subprocess_run(PyObject *argv, int32_t capture_output);
PyObject *py_sysconfig_get_config_var(PyObject *name);
PyObject *py_os_listdir(PyObject *path);
PyObject *py_shlex_split(PyObject *text);
PyObject *py_shutil_which(PyObject *name);
PyObject *py_tempdir_new(PyObject *prefix);
void py_tempdir_cleanup(PyObject *path);
PyObject *py_re_match(PyObject *pattern, PyObject *text);
PyObject *py_re_match_flags(PyObject *pattern, PyObject *text, int64_t flags);
PyObject *py_re_search(PyObject *pattern, PyObject *text);
PyObject *py_re_search_flags(PyObject *pattern, PyObject *text, int64_t flags);
PyObject *py_re_findall_flags(PyObject *pattern, PyObject *text, int64_t flags);
PyObject *py_re_compile_method(PyObject *pattern, int64_t flags, int64_t method_kind);
PyObject *py_time_monotonic(void);

/* ---- Narrow os.path subset --------------------------------------------- */
/* Native helpers used by the Python frontend for the no-libpython subset of
 * ``os.path``. ``join`` expects a list/tuple of path components and returns
 * a pcc string; ``basename`` returns the last path component; ``exists``
 * returns 0/1. */
PyObject *py_os_getenv(PyObject *key, PyObject *default_value);
PyObject *py_os_putenv(PyObject *key, PyObject *value);
PyObject *py_os_unsetenv(PyObject *key);
PyObject *py_os_path_join(PyObject *parts);
PyObject *py_os_path_basename(PyObject *path);
PyObject *py_os_path_dirname(PyObject *path);
PyObject *py_os_path_split(PyObject *path);
int       py_os_path_exists(PyObject *path);
int       py_os_path_isabs(PyObject *path);
int       py_os_path_isfile(PyObject *path);
int       py_os_path_isdir(PyObject *path);
PyObject *py_os_path_getmtime(PyObject *path);
PyObject *py_os_path_getsize(PyObject *path);
PyObject *py_os_path_abspath(PyObject *path);
PyObject *py_os_path_expanduser(PyObject *path);
PyObject *py_os_path_realpath(PyObject *path);
PyObject *py_os_path_commonpath(PyObject *paths);
PyObject *py_os_path_expandvars(PyObject *path);
PyObject *py_os_path_relpath(PyObject *path, PyObject *start);
PyObject *py_os_path_commonprefix(PyObject *paths);
PyObject *py_os_path_splitext(PyObject *path);
PyObject *py_os_path_normcase(PyObject *path);
PyObject *py_os_path_normpath(PyObject *path);
PyObject *py_os_path_splitdrive(PyObject *path);
PyObject *py_os_cpu_count(void);
/* Low-level platform-portable stat classifier — returns 0=missing,
 * 1=regular file, 2=directory, 3=other. The pcc-Python port of
 * py_os_path uses this to keep stat-buffer layout out of the
 * pcc-Python source. */
int32_t   py_path_stat_kind(const char *path);
/* Last-modification time as IEEE-754 seconds-since-epoch double; NaN
 * if stat() fails. Hides struct timespec layout from the pcc-Python
 * port. */
double      py_path_stat_mtime(const char *path);
int64_t     py_path_stat_size(const char *path);
/* Current working directory as a NUL-terminated cstring. Pointer is
 * borrowed (thread-local static buffer); copy before the next call. */
const char *py_path_getcwd(void);
const char *py_path_realpath(const char *path);
/* Boxed `sys.platform` value — same value Python's sys.platform
 * exposes (e.g. "darwin", "linux"). Picked at C compile time, no
 * libpython dependency. */
PyObject   *py_sys_platform_str(void);
/* Boxed `sys.path` value — list of one entry (cwd). */
PyObject   *py_sys_path_list(void);
/* Boxed `platform.machine()` value, e.g. "arm64" or "x86_64". */
PyObject   *py_platform_machine_str(void);
/* Boxed `platform.release()` value from uname(2). */
PyObject   *py_platform_release_str(void);
/* Boxed `os.getcwd()` value. NULL if getcwd() fails. */
PyObject   *py_os_getcwd_str(void);
/* `os.access(path, mode)` — returns 1 (accessible) / 0 (not). */
int32_t     py_os_access(PyObject *path, int32_t mode);
/* `os.write(fd, data)` — writes bytes/str data to fd. Returns number of bytes written. */
int32_t     py_os_write(int32_t fd, PyObject *data);


/* Minimal pcc-native HTTP substrate for bootstrap/package index work.
 * Supports plain http:// URLs and writes the response body to dest_path.
 * Arguments are pcc str objects. Returns 0 on success, negative on error. */
int64_t     py_http_download_to_file(PyObject *url, PyObject *dest_path);

/* ---- Exceptions (Phase 3) --------------------------------------------- */

/* Install `exc` as the thread-local current exception. Return-code
 * exception model: py_raise returns normally; callers (codegen-emitted
 * code) must check py_err_occurred() after each call that could raise
 * and branch to an error-handler / function epilogue. */
void py_raise(PyObject *exc);

/* Return the active exception (borrowed), or NULL if none is set. */
PyObject *py_current_exception(void);

/* 1 if an exception is currently pending in the TLS slot, else 0.
 * Used by codegen-emitted post-call checks in the return-code model.
 * Returns int64_t so the pcc-Python port (py_exc_tls.py) can emit it
 * under pcc's default `int` lowering without a type mismatch. */
int64_t py_err_occurred(void);

/* Drop the thread-local current-exception slot (decref + NULL). */
void py_clear_exception(void);

/* Allocate a new builtin exception with the given PY_EXC_* tag and
 * message. Returns a new owned reference; tag outside
 * [0, PY_EXC_N_BUILTIN) falls back to Exception. */
PyObject *py_exc_new(int64_t type_tag, const char *msg);

/* Allocate a builtin exception whose primary value/args[0] is an existing
 * PyObject rather than a C string. Used for StopIteration.value. `value` is
 * borrowed; the exception stores its own reference. */
PyObject *py_exc_new_with_value(int64_t type_tag, PyObject *value);

/* Allocate a user-defined exception using a pre-existing class object.
 * `cls` must be a PyClassObject*; `msg` may be NULL. Returns a new
 * owned reference. */
PyObject *py_exc_new_with_class(PyObject *cls, const char *msg);

/* Borrowed reference to the builtin exception class for a PY_EXC_* tag. */
PyClassObject *py_exc_builtin_class(int64_t type_tag);

/* `raise X from Y` chaining: set `exc.__cause__ = cause`. `exc` and
 * `cause` are both borrowed (ref is acquired on cause). Safe to call
 * with cause = NULL (clears existing cause). */
void py_exc_set_cause(PyObject *exc, PyObject *cause);

/* Implicit context chain — used by codegen when `raise Y` fires inside
 * an active `except` clause. `exc.__context__ = context`. */
void py_exc_set_context(PyObject *exc, PyObject *context);

/* Borrowed reference to the message PyStrObject stashed on an
 * exception by py_exc_new. Used by py_obj_str to implement ``str(e)``
 * on exception instances. Returns NULL if exc has no message. */
PyObject *py_exc_get_message(PyObject *exc);
PyObject *py_exc_get_cause(PyObject *exc);
PyObject *py_exc_get_context(PyObject *exc);
int64_t   py_exc_traceback_len(PyObject *exc);

/* Walk `exc`'s class MRO and test whether `type` appears. Either arg
 * may be an exception instance (we auto-project to the class) or a
 * PyClassObject*. Returns 1 on match, 0 otherwise. */
int64_t py_exc_matches(PyObject *exc, PyObject *type);

/* Append a PyFrameRecord to the exception's traceback. `func_name` and
 * `filename` are borrowed — the caller must guarantee they outlive the
 * exception (typically static rodata strings emitted by the compiler). */
void py_exc_append_frame(PyObject *exc,
                         const char *func_name,
                         const char *filename,
                         int32_t line);

/* Format exception traceback-style text and write to stdout. Used by
 * the unhandled-exception handler at program top level. */
void py_exc_print_unhandled(PyObject *exc);

/* ---- GC ---------------------------------------------------------------- */
void py_gc_init(void);
int64_t py_gc_collect(void);
void py_gc_track(PyObject *o);
void py_gc_untrack(PyObject *o);
void py_gc_enable(void);
void py_gc_disable(void);
int64_t py_gc_is_enabled(void);
int64_t py_gc_is_tracked(PyObject *o);
int64_t py_gc_get_count(int32_t generation);
int64_t py_gc_get_threshold(int32_t generation);
void py_gc_set_threshold(int32_t gen0, int32_t gen1, int32_t gen2);
void py_gc_freeze(void);
void py_gc_unfreeze(void);
int64_t py_gc_get_freeze_count(void);
PyObject *py_gc_get_objects(void);
PyObject *py_gc_get_referents(PyObject *o);
PyObject *py_gc_get_referrers(PyObject *o);

/* ---- Phase 4: CPython C-API fallback ----------------------------------- */
/* Opaque CPython ``PyObject *`` type — distinct from pcc's own PyObject*
 * and exposed as ``void *`` at the codegen ABI boundary. All CPython
 * pointers returned from these helpers own a reference that the caller
 * must release via :c:func:`py_cpy_decref` (the codegen emits the
 * decref when a dyn-typed value falls out of scope). */
void  py_cpy_ensure_init(void);
void *py_cpy_import(const char *name);
void *py_cpy_getattr(void *obj, const char *name);
int   py_cpy_setattr(void *obj, const char *name, void *value);
/* Consume any pending unhandled CPython exception at program exit and
 * return the corresponding process status.
 *
 * - no pending exception: returns 0
 * - SystemExit(None) / SystemExit(0): returns 0 and clears it
 * - SystemExit(n): returns n and clears it
 * - other exceptions: prints via CPython's traceback printer, clears
 *   the error indicator, and returns 1
 */
int   py_cpy_main_exitcode(void);
void *py_cpy_call_noargs(void *callable);
void *py_cpy_call1(void *callable, void *a);
void *py_cpy_call2(void *callable, void *a, void *b);
void *py_cpy_call3(void *callable, void *a, void *b, void *c);
/* Arbitrary-arity call. ``argv[0..n)`` must each own a reference; the
 * callee steals each reference (via PyTuple_SetItem) whether the call
 * succeeds or fails. Returns a new CPython reference, or NULL. */
void *py_cpy_call_argv(void *callable, int64_t n, void **argv);
int64_t py_cpy_len(void *obj);
void   *py_cpy_getitem(void *obj, void *key);
int     py_cpy_setitem(void *obj, void *key, void *value);
int     py_cpy_truthy(void *obj);
void   *py_cpy_iter(void *obj);
void   *py_cpy_iter_next(void *it);
PyObject *py_cpy_to_pcc_str(void *cpy_obj);
/* Best-effort CPython PyObject* -> pcc PyObject* converter. Handles
 * None/bool/int/float/str/list/tuple/dict/set recursively; unsupported
 * foreign objects fall back to str(obj). Returns a new pcc-owned ref. */
PyObject *py_cpy_to_pcc_obj(void *cpy_obj);
void  py_cpy_decref(void *obj);
void  py_cpy_incref(void *obj);
/* pcc <-> CPython scalar marshalling. */
void   *py_cpy_from_i64(int64_t value);
int64_t py_cpy_to_i64(void *obj);
void   *py_cpy_from_f64(double value);
double  py_cpy_to_f64(void *obj);
void   *py_cpy_from_pccstr(PyObject *s);
/* Universal pcc PyObject → CPython PyObject* converter. Rebuilds the
 * object by recursing on lists / tuples / dicts so CPython APIs called
 * from pcc-emitted code receive real CPython containers, not pcc-
 * internal ones. Returns NULL on error. Caller owns the new ref. */
void   *py_cpy_from_pcc_obj(PyObject *o);

/* Positional + keyword call. ``argv[0..n_pos)`` refs are stolen into
 * the positional tuple (caller must not decref). ``kw_vals`` are
 * borrowed by PyDict_SetItemString so the caller retains ownership.
 * ``kw_names`` are NUL-terminated C strings (static lifetime).
 * Returns a new owned ref or NULL on error. */
void   *py_cpy_call_kw(void *callable,
                       int64_t n_pos, void **argv,
                       int64_t n_kw, const char **kw_names, void **kw_vals);

/* Call ``callable(*args, **kwargs_dict)`` where ``kwargs_dict`` is
 * already a CPython mapping object. Positional refs are stolen into the
 * tuple; ``kwargs_dict`` is borrowed. Returns a new owned ref or NULL
 * on error. */
void   *py_cpy_call_kwdict(void *callable,
                           int64_t n_pos, void **argv,
                           void *kwargs_dict);
void   *py_cpy_call_kwdict_plus(void *callable,
                                int64_t n_pos, void **argv,
                                int64_t n_kw,
                                const char **kw_names, void **kw_vals,
                                void *kwargs_dict);
void   *py_cpy_call_list_kwdict(void *callable,
                                PyObject *args,
                                void *kwargs_dict);

/* Dynamic slice dispatch for pcc-native objects whose static type is not
 * specific enough at compile time. */
PyObject *py_obj_slice(PyObject *obj, PyObject *lo, PyObject *hi, PyObject *step);
int64_t   py_obj_set_slice(PyObject *obj, PyObject *lo, PyObject *hi, PyObject *step, PyObject *replacement);
int64_t   py_obj_del_slice(PyObject *obj, PyObject *lo, PyObject *hi, PyObject *step);

/* Call ``callable(*args)`` where ``args`` is a pcc list / tuple. The
 * helper converts the container to a CPython tuple via
 * ``py_cpy_from_pcc_obj`` and dispatches through ``PyObject_Call``.
 * Returns a new owned ref or NULL on error. */
void   *py_cpy_call_list(void *callable, PyObject *args);

/* Wrap a pcc user FuncDef's function pointer as a CPython callable so
 * it can be passed to ``sorted(..., key=<fn>)`` / ``re.sub(pat, <fn>,
 * text)`` / any other CPython API that consumes a ``PyObject *``
 * callable. ``fn_ptr`` must target a pcc function with signature
 * ``CPyObject *(CPyObject *, ...)`` — arity-specific variants
 * dispatch via per-arity trampoline + PyMethodDef. */
void   *py_cpy_wrap_pcc_0arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_1arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_2arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_3arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_4arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_5arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_6arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_7arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_8arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_9arg(void *fn_ptr);

/* ---- Substrate primitives (Phase 4a) ---------------------------------- */
/*
 * Low-level memory-access helpers used by pcc-Python ports of runtime
 * modules. Each helper is a one-liner; cc inlines them, pcc emits them
 * directly. They give pcc-Python C-struct-equivalent authoring
 * (malloc, free, offset-based load/store) without requiring native
 * raw-pointer syntax in the Python subset.
 */
void   *py_mem_alloc(size_t bytes);
void    py_mem_free(void *p);
void   *py_mem_zero(void *p, size_t bytes);
void   *py_mem_copy(void *dst, const void *src, size_t bytes);
int64_t py_mem_load_i64(const void *p, int64_t offset);
int32_t py_mem_load_i32(const void *p, int64_t offset);
int8_t  py_mem_load_i8(const void *p, int64_t offset);
void   *py_mem_load_ptr(const void *p, int64_t offset);
void    py_mem_store_i64(void *p, int64_t offset, int64_t v);
void    py_mem_store_i32(void *p, int64_t offset, int32_t v);
void    py_mem_store_i8(void *p, int64_t offset, int8_t v);
void    py_mem_store_ptr(void *p, int64_t offset, void *v);
void   *py_mem_ptr_add(void *p, int64_t offset);
int32_t py_mem_ptr_is_tagged_int(const void *p);
void   *py_mem_null_ptr(void);
int32_t py_mem_ptr_is_null(const void *p);
int32_t py_mem_ptr_eq(const void *a, const void *b);

/* Raw TLS-slot accessors for the exception runtime. Lives in
 * py_substrate.c so the cc-compiled C helpers (py_exc_tls.c) and the
 * pcc-Python port (py_exc_tls.py) can both reach it via extern. */
void   *py_tls_exc_get(void);
void    py_tls_exc_set(void *exc);

/* Function-call accessors for the three immortal singletons. These are
 * retained for the C runtime path; pcc-Python ports use pcc.unsafe
 * global intrinsics to read the exported globals directly. */
void   *py_subs_none(void);
void   *py_subs_true(void);
void   *py_subs_false(void);

/* Legacy function-style accessors for the builtin exception tables. */
const char *py_subs_exc_name(int32_t tag);
int32_t     py_subs_exc_parent(int32_t tag);
int32_t     py_subs_exc_n_builtin(void);

/* Legacy function-style accessors for the builtin exception cache. */
void       *py_subs_exc_cache_get(int32_t tag);
void        py_subs_exc_cache_set(int32_t tag, void *cls);

/* py_set_dummy tombstone sentinel accessor (value of the global
 * const pointer). Lives in substrate so py_set.c can be replaced. */
void       *py_subs_set_dummy(void);

/* OS substrate primitives for py_os.py. Thin wrappers around libc so
 * the pcc-Python port does not need native getenv/setenv/access
 * syntax. */
const char *py_subs_getenv(const char *name);
int32_t     py_subs_setenv(const char *name, const char *value);
int32_t     py_subs_unsetenv(const char *name);
int32_t     py_subs_path_exists(const char *path);
int64_t     py_subs_cstr_len(const char *s);
int8_t      py_subs_cstr_at(const char *s, int64_t i);
void       *py_subs_realloc(void *p, size_t bytes);

/* stdio substrate primitives for py_print.py. Thin write() wrapper
 * returns the number of bytes actually written. */
int64_t     py_subs_write_fd(int32_t fd, const void *buf, int64_t n);

/* String substrate for py_class.py method/field name lookup. */
int32_t     py_subs_strcmp(const char *a, const char *b);

/* Type-tag allocator counter for user-defined classes. Substrate hosts
 * it so the counter survives a swap of py_class.c for py_class.py. */
int32_t     py_subs_alloc_user_tag(void);

/* Lazily-bootstrapped root "object" class used as the universal MRO
 * tail. Hosted in substrate so a swap doesn't lose the once-only
 * static-storage object. Returns a PyClassObject*. */
void       *py_subs_object_root(void);

#endif /* PY_RUNTIME_H */
