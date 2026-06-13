/* py_timer_heap.c - scalable virtual-thread timer structure (C mirror).
 *
 * Mirrors pcc/vthread/timer_oracle.py::MinHeapTimerQueue exactly. See the
 * header for the algorithm and the semantics contract, and
 * docs/design/pcc-vthread-oracles.md for the design rationale (why a min-heap
 * with lazy cancellation, and not a timing wheel, for this first slice).
 *
 * Structure:
 *   * ``nodes`` is a 0-based binary min-heap of (deadline, seq, timer_id).
 *     Ordering key is (deadline, seq): sift-up / sift-down compare deadline
 *     first, breaking ties by the monotonic ``seq`` so equal-deadline entries
 *     pop in insertion (FIFO) order, matching the C sorted-list ``<= deadline``
 *     stable walk.
 *   * ``live`` is an open-addressing hash table timer_id -> (deadline, seq). It
 *     is the source of truth for membership and cancellation. A heap node is
 *     stale iff live has no entry for its id (cancelled/expired) or the entry's
 *     (deadline, seq) differs (the id was rescheduled to a newer slot).
 */
#include "py_timer_heap.h"

#include <stdlib.h>
#include <string.h>

#define PCC_TIMER_HEAP_MIN_CAP 8
#define PCC_TIMER_LIVE_MIN_CAP 8

/* ---- live-map (open addressing, linear probe) --------------------------- */

static uint64_t pcc_timer_hash_id(int64_t timer_id) {
    /* splitmix64 finalizer: good spread for sequential ids. */
    uint64_t x = (uint64_t)timer_id + 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    x = x ^ (x >> 31);
    return x;
}

/* Find the slot for timer_id. If found (state==1), returns its index and sets
 * *found=1. Otherwise returns the first insertion-eligible index (empty or
 * tombstone) and sets *found=0. Assumes live_cap > live_used (spare slot). */
static int64_t pcc_timer_live_probe(
    const PccTimerHeap *h,
    int64_t timer_id,
    int *found
) {
    int64_t mask = h->live_cap - 1;
    int64_t idx = (int64_t)(pcc_timer_hash_id(timer_id) & (uint64_t)mask);
    int64_t first_free = -1;
    for (;;) {
        PccTimerLiveSlot *slot = &h->live[idx];
        if (slot->state == 0) {
            *found = 0;
            return first_free >= 0 ? first_free : idx;
        }
        if (slot->state == 2) {
            if (first_free < 0) first_free = idx;
        } else if (slot->timer_id == timer_id) {
            *found = 1;
            return idx;
        }
        idx = (idx + 1) & mask;
    }
}

static int pcc_timer_live_rehash(PccTimerHeap *h, int64_t new_cap) {
    PccTimerLiveSlot *old = h->live;
    int64_t old_cap = h->live_cap;
    PccTimerLiveSlot *fresh = (PccTimerLiveSlot *)calloc(
        (size_t)new_cap, sizeof(PccTimerLiveSlot)
    );
    if (fresh == NULL) return -1;
    h->live = fresh;
    h->live_cap = new_cap;
    h->live_used = 0;
    h->live_count = 0;
    if (old != NULL) {
        for (int64_t i = 0; i < old_cap; i++) {
            if (old[i].state == 1) {
                int found = 0;
                int64_t idx = pcc_timer_live_probe(h, old[i].timer_id, &found);
                PccTimerLiveSlot *slot = &h->live[idx];
                slot->timer_id = old[i].timer_id;
                slot->deadline = old[i].deadline;
                slot->seq = old[i].seq;
                slot->state = 1;
                h->live_used++;
                h->live_count++;
            }
        }
        free(old);
    }
    return 0;
}

static int pcc_timer_live_reserve(PccTimerHeap *h) {
    /* Keep load factor under 0.7 counting tombstones. */
    if ((h->live_used + 1) * 10 <= h->live_cap * 7) return 0;
    int64_t new_cap = h->live_cap;
    /* Grow if live entries are dense; else a same-size rehash purges
     * tombstones. */
    if ((h->live_count + 1) * 10 > h->live_cap * 5) {
        new_cap = h->live_cap * 2;
    }
    return pcc_timer_live_rehash(h, new_cap);
}

/* ---- min-heap ----------------------------------------------------------- */

/* Order: deadline, then seq. Returns <0 if a<b, 0 if equal, >0 if a>b. */
static int pcc_timer_node_cmp(
    const PccTimerHeapNode *a,
    const PccTimerHeapNode *b
) {
    if (a->deadline < b->deadline) return -1;
    if (a->deadline > b->deadline) return 1;
    if (a->seq < b->seq) return -1;
    if (a->seq > b->seq) return 1;
    return 0;
}

static int pcc_timer_heap_reserve(PccTimerHeap *h) {
    if (h->heap_len < h->heap_cap) return 0;
    int64_t new_cap = h->heap_cap > 0 ? h->heap_cap * 2 : PCC_TIMER_HEAP_MIN_CAP;
    PccTimerHeapNode *grown = (PccTimerHeapNode *)realloc(
        h->nodes, (size_t)new_cap * sizeof(PccTimerHeapNode)
    );
    if (grown == NULL) return -1;
    h->nodes = grown;
    h->heap_cap = new_cap;
    return 0;
}

static void pcc_timer_sift_up(PccTimerHeap *h, int64_t i) {
    PccTimerHeapNode item = h->nodes[i];
    while (i > 0) {
        int64_t parent = (i - 1) / 2;
        if (pcc_timer_node_cmp(&item, &h->nodes[parent]) >= 0) break;
        h->nodes[i] = h->nodes[parent];
        i = parent;
    }
    h->nodes[i] = item;
}

static void pcc_timer_sift_down(PccTimerHeap *h, int64_t i) {
    int64_t n = h->heap_len;
    PccTimerHeapNode item = h->nodes[i];
    for (;;) {
        int64_t left = 2 * i + 1;
        int64_t right = left + 1;
        int64_t smallest = i;
        const PccTimerHeapNode *best = &item;
        if (left < n && pcc_timer_node_cmp(&h->nodes[left], best) < 0) {
            smallest = left;
            best = &h->nodes[left];
        }
        if (right < n && pcc_timer_node_cmp(&h->nodes[right], best) < 0) {
            smallest = right;
            best = &h->nodes[right];
        }
        if (smallest == i) break;
        h->nodes[i] = h->nodes[smallest];
        i = smallest;
    }
    h->nodes[i] = item;
}

static void pcc_timer_heap_pop_root(PccTimerHeap *h) {
    /* Remove nodes[0]; move last into root and sift down. */
    h->heap_len--;
    if (h->heap_len > 0) {
        h->nodes[0] = h->nodes[h->heap_len];
        pcc_timer_sift_down(h, 0);
    }
}

/* True if the current heap root is stale (no matching live entry). */
static int pcc_timer_root_is_stale(const PccTimerHeap *h) {
    const PccTimerHeapNode *root = &h->nodes[0];
    int found = 0;
    int64_t idx = pcc_timer_live_probe(h, root->timer_id, &found);
    if (!found) return 1;
    const PccTimerLiveSlot *slot = &h->live[idx];
    return (slot->deadline != root->deadline || slot->seq != root->seq)
        ? 1
        : 0;
}

/* ---- public API --------------------------------------------------------- */

int pcc_timer_heap_init(PccTimerHeap *h) {
    if (h == NULL) return -1;
    memset(h, 0, sizeof(*h));
    h->live = (PccTimerLiveSlot *)calloc(
        PCC_TIMER_LIVE_MIN_CAP, sizeof(PccTimerLiveSlot)
    );
    if (h->live == NULL) return -1;
    h->live_cap = PCC_TIMER_LIVE_MIN_CAP;
    return 0;
}

void pcc_timer_heap_dispose(PccTimerHeap *h) {
    if (h == NULL) return;
    free(h->nodes);
    free(h->live);
    h->nodes = NULL;
    h->live = NULL;
    h->heap_len = 0;
    h->heap_cap = 0;
    h->live_count = 0;
    h->live_used = 0;
    h->live_cap = 0;
}

int pcc_timer_heap_insert(PccTimerHeap *h, int64_t deadline, int64_t timer_id) {
    if (h == NULL) return -1;
    if (pcc_timer_live_reserve(h) != 0) return -1;
    if (pcc_timer_heap_reserve(h) != 0) return -1;

    h->seq++;
    int64_t seq = h->seq;

    int found = 0;
    int64_t idx = pcc_timer_live_probe(h, timer_id, &found);
    PccTimerLiveSlot *slot = &h->live[idx];
    if (found) {
        /* Reschedule: overwrite the authoritative (deadline, seq); the old
         * heap node is now stale and will be skipped. */
        slot->deadline = deadline;
        slot->seq = seq;
    } else {
        slot->timer_id = timer_id;
        slot->deadline = deadline;
        slot->seq = seq;
        slot->state = 1;
        h->live_used++;
        h->live_count++;
    }

    int64_t i = h->heap_len;
    h->nodes[i].deadline = deadline;
    h->nodes[i].seq = seq;
    h->nodes[i].timer_id = timer_id;
    h->heap_len++;
    pcc_timer_sift_up(h, i);
    return 0;
}

int pcc_timer_heap_cancel(PccTimerHeap *h, int64_t timer_id) {
    if (h == NULL || h->live_count == 0) return 0;
    int found = 0;
    int64_t idx = pcc_timer_live_probe(h, timer_id, &found);
    if (!found) return 0;
    /* Lazy: mark dead in the live map only; the heap slot is skipped later. */
    h->live[idx].state = 2;
    h->live_count--;
    /* live_used stays: tombstone still occupies a probe slot until rehash. */
    return 1;
}

int pcc_timer_heap_is_registered(const PccTimerHeap *h, int64_t timer_id) {
    if (h == NULL || h->live_count == 0) return 0;
    int found = 0;
    (void)pcc_timer_live_probe(h, timer_id, &found);
    return found ? 1 : 0;
}

int64_t pcc_timer_heap_size(const PccTimerHeap *h) {
    return h == NULL ? 0 : h->live_count;
}

int pcc_timer_heap_peek(PccTimerHeap *h, int64_t *out_deadline) {
    if (h == NULL) return 0;
    while (h->heap_len > 0) {
        if (pcc_timer_root_is_stale(h)) {
            pcc_timer_heap_pop_root(h);
            continue;
        }
        if (out_deadline != NULL) *out_deadline = h->nodes[0].deadline;
        return 1;
    }
    return 0;
}

int64_t pcc_timer_heap_pop_expired(
    PccTimerHeap *h,
    int64_t now,
    int64_t *out_ids,
    int64_t out_cap
) {
    if (h == NULL) return 0;
    int64_t drained = 0;
    while (h->heap_len > 0) {
        if (pcc_timer_root_is_stale(h)) {
            /* Stale: cancelled or superseded. Drop without returning. */
            pcc_timer_heap_pop_root(h);
            continue;
        }
        if (h->nodes[0].deadline > now) {
            /* Root is in the future -> nothing else is due. */
            break;
        }
        /* If the caller passed a buffer, stop when it is full so the due id
         * stays registered for the next call rather than being lost. */
        if (out_ids != NULL && drained >= out_cap) break;

        int64_t timer_id = h->nodes[0].timer_id;
        /* Unregister (root retention ends on expiry). */
        int found = 0;
        int64_t idx = pcc_timer_live_probe(h, timer_id, &found);
        if (found) {
            h->live[idx].state = 2;
            h->live_count--;
        }
        pcc_timer_heap_pop_root(h);
        if (out_ids != NULL) out_ids[drained] = timer_id;
        drained++;
    }
    return drained;
}
