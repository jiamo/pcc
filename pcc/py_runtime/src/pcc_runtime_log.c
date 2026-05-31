/* pcc runtime event logging.
 *
 * Controlled by:
 *   PCC_LOG=gc,alloc,store,refcount,weakref,finalizer,exception,dispatch,all
 *   PCC_LOG_FORMAT=text|json
 *   PCC_LOG_FILE=/path/to/log.jsonl   (default: stderr; '-' means stderr)
 *
 * Logs default to stderr by design so compiled program stdout stays stable.
 * PCC_LOG_FILE gives tests and tools a clean side-channel artifact.
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#define PCC_LOG_ALLOC      (1u << 0)
#define PCC_LOG_GC         (1u << 1)
#define PCC_LOG_REFCOUNT   (1u << 2)
#define PCC_LOG_WEAKREF    (1u << 3)
#define PCC_LOG_FINALIZER  (1u << 4)
#define PCC_LOG_EXCEPTION  (1u << 5)
#define PCC_LOG_DISPATCH   (1u << 6)
#define PCC_LOG_RUNTIME    (1u << 7)
#define PCC_LOG_ALL        0xffffffffu

static int32_t pcc_log_init_state = 0;
static unsigned int pcc_log_mask = 0;
static int pcc_log_json = 0;
static const char *pcc_log_file_path = NULL;

int64_t pcc_runtime_now_us(void) {
    struct timeval tv;
    if (gettimeofday(&tv, NULL) != 0) return 0;
    return (int64_t)tv.tv_sec * 1000000LL + (int64_t)tv.tv_usec;
}

int64_t pcc_runtime_monotonic_us(void) {
#if defined(CLOCK_MONOTONIC)
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        return (int64_t)ts.tv_sec * 1000000LL
             + (int64_t)(ts.tv_nsec / 1000);
    }
#endif
    return pcc_runtime_now_us();
}

static unsigned int pcc_log_category_mask(const char *category) {
    if (category == NULL) return 0;
    if (strcmp(category, "alloc") == 0) return PCC_LOG_ALLOC;
    if (strcmp(category, "gc") == 0) return PCC_LOG_GC;
    if (strcmp(category, "refcount") == 0) return PCC_LOG_REFCOUNT;
    if (strcmp(category, "weakref") == 0) return PCC_LOG_WEAKREF;
    if (strcmp(category, "finalizer") == 0) return PCC_LOG_FINALIZER;
    if (strcmp(category, "exception") == 0) return PCC_LOG_EXCEPTION;
    if (strcmp(category, "dispatch") == 0) return PCC_LOG_DISPATCH;
    if (strcmp(category, "runtime") == 0) return PCC_LOG_RUNTIME;
    return 0;
}

static int pcc_log_token_enabled(const char *tokens, const char *category) {
    if (tokens == NULL || tokens[0] == '\0' || category == NULL) return 0;
    if (strcmp(tokens, "1") == 0 || strcmp(tokens, "all") == 0) return 1;
    const char *p = tokens;
    size_t n = strlen(category);
    while (*p) {
        while (*p == ',' || *p == ' ' || *p == '\t') p++;
        if (strncmp(p, "all", 3) == 0 && (p[3] == '\0' || p[3] == ',')) return 1;
        if (strncmp(p, category, n) == 0 && (p[n] == '\0' || p[n] == ',' || p[n] == ' ' || p[n] == '\t')) {
            return 1;
        }
        while (*p && *p != ',') p++;
    }
    return 0;
}

static unsigned int pcc_log_parse_tokens(const char *tokens) {
    if (tokens == NULL || tokens[0] == '\0') return 0;
    if (strcmp(tokens, "1") == 0 || strcmp(tokens, "all") == 0) {
        return PCC_LOG_ALL;
    }
    unsigned int mask = 0;
    if (pcc_log_token_enabled(tokens, "alloc")) mask |= PCC_LOG_ALLOC;
    if (pcc_log_token_enabled(tokens, "gc")) mask |= PCC_LOG_GC;
    if (pcc_log_token_enabled(tokens, "refcount")) mask |= PCC_LOG_REFCOUNT;
    if (pcc_log_token_enabled(tokens, "weakref")) mask |= PCC_LOG_WEAKREF;
    if (pcc_log_token_enabled(tokens, "finalizer")) mask |= PCC_LOG_FINALIZER;
    if (pcc_log_token_enabled(tokens, "exception")) mask |= PCC_LOG_EXCEPTION;
    if (pcc_log_token_enabled(tokens, "dispatch")) mask |= PCC_LOG_DISPATCH;
    if (pcc_log_token_enabled(tokens, "runtime")) mask |= PCC_LOG_RUNTIME;
    if (pcc_log_token_enabled(tokens, "all")) mask = PCC_LOG_ALL;
    return mask;
}

static void pcc_runtime_log_init_once(void) {
    if (__atomic_load_n(&pcc_log_init_state, __ATOMIC_ACQUIRE) == 2) return;
    int32_t expected = 0;
    if (__atomic_compare_exchange_n(
            &pcc_log_init_state,
            &expected,
            1,
            0,
            __ATOMIC_ACQ_REL,
            __ATOMIC_ACQUIRE
        )) {
        pcc_log_mask = pcc_log_parse_tokens(getenv("PCC_LOG"));
        const char *fmt = getenv("PCC_LOG_FORMAT");
        pcc_log_json = fmt != NULL && strcmp(fmt, "json") == 0;
        pcc_log_file_path = getenv("PCC_LOG_FILE");
        __atomic_store_n(&pcc_log_init_state, 2, __ATOMIC_RELEASE);
        return;
    }
    while (__atomic_load_n(&pcc_log_init_state, __ATOMIC_ACQUIRE) != 2) {
        pcc_thread_safepoint();
    }
}

int pcc_runtime_log_enabled(const char *category) {
    pcc_runtime_log_init_once();
    return (pcc_log_mask & pcc_log_category_mask(category)) != 0;
}

static int pcc_runtime_log_code_enabled(int32_t category) {
    pcc_runtime_log_init_once();
    switch (category) {
        case 1: return (pcc_log_mask & PCC_LOG_ALLOC) != 0;
        case 2: return (pcc_log_mask & PCC_LOG_GC) != 0;
        case 3: return (pcc_log_mask & PCC_LOG_REFCOUNT) != 0;
        case 4: return (pcc_log_mask & PCC_LOG_WEAKREF) != 0;
        case 5: return (pcc_log_mask & PCC_LOG_FINALIZER) != 0;
        case 6: return (pcc_log_mask & PCC_LOG_EXCEPTION) != 0;
        case 7: return (pcc_log_mask & PCC_LOG_DISPATCH) != 0;
        default: return (pcc_log_mask & PCC_LOG_RUNTIME) != 0;
    }
}

static FILE *pcc_runtime_log_open_stream(int *should_close) {
    if (should_close != NULL) *should_close = 0;
    const char *path = pcc_log_file_path;
    if (path == NULL || path[0] == '\0' || strcmp(path, "-") == 0) {
        return stderr;
    }
    FILE *f = fopen(path, "a");
    if (f == NULL) {
        return stderr;
    }
    if (should_close != NULL) *should_close = 1;
    return f;
}

void pcc_runtime_log_event(const char *category,
                           const char *event,
                           int64_t value0,
                           int64_t value1,
                           const void *ptr) {
    if (!pcc_runtime_log_enabled(category)) return;
    long long ts = (long long)time(NULL);
    long long tid = (long long)pcc_current_thread_id();
    int should_close = 0;
    FILE *out = pcc_runtime_log_open_stream(&should_close);
    if (pcc_log_json) {
        fprintf(out,
            "{\"schema\":\"pcc.runtime_log.v1\",\"ts\":%lld,\"thread\":%lld,"
            "\"category\":\"%s\",\"event\":\"%s\",\"value0\":%lld,\"value1\":%lld,"
            "\"ptr\":\"%p\"}\n",
            ts, tid, category ? category : "", event ? event : "",
            (long long)value0, (long long)value1, ptr);
    } else {
        fprintf(out,
            "[pcc.%s] ts=%lld thread=%lld event=%s value0=%lld value1=%lld ptr=%p\n",
            category ? category : "log", ts, tid, event ? event : "",
            (long long)value0, (long long)value1, ptr);
    }
    fflush(out);
    if (should_close) fclose(out);
}

static const char *pcc_runtime_log_category_from_code(int32_t category) {
    switch (category) {
        case 1: return "alloc";
        case 2: return "gc";
        case 3: return "refcount";
        case 4: return "weakref";
        case 5: return "finalizer";
        case 6: return "exception";
        case 7: return "dispatch";
        default: return "runtime";
    }
}

static const char *pcc_runtime_log_event_from_code(int32_t category, int32_t event) {
    switch (category) {
        case 1:
            switch (event) {
                case 1: return "alloc_request";
                case 2: return "alloc_object";
                default: return "alloc_event";
            }
        case 2:
            switch (event) {
                case 1: return "collect_start";
                case 2: return "collect_stop";
                case 3: return "store_ptr";
                default: return "gc_event";
            }
        case 3:
            switch (event) {
                case 1: return "incref";
                case 2: return "decref";
                case 3: return "free";
                default: return "refcount_event";
            }
        case 4:
            switch (event) {
                case 1: return "new";
                case 2: return "invalidate";
                case 3: return "callback";
                case 4: return "dealloc";
                default: return "weakref_event";
            }
        case 5:
            switch (event) {
                case 1: return "lookup";
                case 2: return "call";
                case 3: return "done";
                case 4: return "skipped";
                default: return "finalizer_event";
            }
        case 6:
            switch (event) {
                case 1: return "alloc";
                case 2: return "new";
                case 3: return "raise";
                case 4: return "clear";
                case 5: return "set_cause";
                case 6: return "set_context";
                case 7: return "dealloc";
                case 8: return "new_with_value";
                case 9: return "new_with_class";
                default: return "exception_event";
            }
        case 7:
            switch (event) {
                case 1: return "getitem";
                case 2: return "slice";
                case 3: return "setitem";
                case 4: return "delitem";
                case 5: return "getattr";
                case 6: return "setattr";
                case 7: return "delattr";
                case 8: return "call";
                case 9: return "isinstance";
                default: return "dispatch_event";
            }
        default:
            return "event";
    }
}

void pcc_runtime_log_event_code(int32_t category, int32_t event,
                                int64_t value0, int64_t value1,
                                const void *ptr) {
    if (!pcc_runtime_log_code_enabled(category)) return;
    pcc_runtime_log_event(
        pcc_runtime_log_category_from_code(category),
        pcc_runtime_log_event_from_code(category, event),
        value0,
        value1,
        ptr
    );
}
