#include "py_internal.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/*
 * External device resources are deliberately outside the PyObject graph.
 * Every GC backend and both runtime authoring paths share this C-kernel
 * registry, so device memory is never scanned and moving collectors never
 * need a second set of pointer-update rules.
 */
typedef struct PccGcExternalResourceNode {
    uint64_t resource_id;
    uint64_t native_handle;
    int64_t registered_backend;
    int32_t kind;
    int32_t state;
    int64_t retain_count;
    int32_t fence_complete;
    PccGcExternalReleaseFn release_fn;
    void *release_context;
    PccGcExternalContextFreeFn context_free_fn;
    struct PccGcExternalResourceNode *next;
} PccGcExternalResourceNode;

enum {
    PCC_GC_EXTERNAL_STATE_LIVE = 1,
    PCC_GC_EXTERNAL_STATE_PENDING_RELEASE = 2
};

static PccGcExternalResourceNode *pcc_gc_external_resources = NULL;
static int32_t pcc_gc_external_lock = 0;
static uint64_t pcc_gc_external_next_id = 1;
static int64_t pcc_gc_external_active = 0;
static int64_t pcc_gc_external_pending = 0;
static int64_t pcc_gc_external_ready = 0;
static int64_t pcc_gc_external_released = 0;
static int64_t pcc_gc_external_release_failures = 0;
static int64_t pcc_gc_external_last_release_error = 0;

static void pcc_gc_external_lock_acquire(void) {
    while (__atomic_exchange_n(
        &pcc_gc_external_lock, 1, __ATOMIC_ACQUIRE
    ) != 0) {
        pcc_thread_safepoint();
    }
}

static void pcc_gc_external_lock_release(void) {
    __atomic_store_n(&pcc_gc_external_lock, 0, __ATOMIC_RELEASE);
}

static PccGcExternalResourceNode *pcc_gc_external_find_locked(
    uint64_t resource_id
) {
    PccGcExternalResourceNode *node = pcc_gc_external_resources;
    while (node != NULL) {
        if (node->resource_id == resource_id) return node;
        node = node->next;
    }
    return NULL;
}

uint64_t pcc_gc_external_resource_register(
    int32_t kind,
    uint64_t native_handle,
    PccGcExternalReleaseFn release_fn,
    void *release_context,
    PccGcExternalContextFreeFn context_free_fn
) {
    if (
        (kind != PCC_GC_EXTERNAL_RESOURCE_GPU_BUFFER
         && kind != PCC_GC_EXTERNAL_RESOURCE_GPU_FENCE)
        || native_handle == 0
        || release_fn == NULL
    ) {
        return 0;
    }
    int64_t backend = pcc_gc_backend();
    if (
        backend < PCC_GC_KIND_REFCOUNT_CYCLE
        || backend > PCC_GC_KIND_COLORED_RELOCATING
    ) {
        return 0;
    }

    PccGcExternalResourceNode *node =
        (PccGcExternalResourceNode *)calloc(1, sizeof(*node));
    if (node == NULL) return 0;
    node->native_handle = native_handle;
    node->registered_backend = backend;
    node->kind = kind;
    node->state = PCC_GC_EXTERNAL_STATE_LIVE;
    node->retain_count = 1;
    node->release_fn = release_fn;
    node->release_context = release_context;
    node->context_free_fn = context_free_fn;

    pcc_gc_external_lock_acquire();
    node->resource_id = pcc_gc_external_next_id++;
    if (node->resource_id == 0) {
        node->resource_id = pcc_gc_external_next_id++;
    }
    node->next = pcc_gc_external_resources;
    pcc_gc_external_resources = node;
    __atomic_add_fetch(&pcc_gc_external_active, 1, __ATOMIC_RELAXED);
    pcc_gc_external_lock_release();
    return node->resource_id;
}

int64_t pcc_gc_external_resource_retain(uint64_t resource_id) {
    int64_t result = -1;
    pcc_gc_external_lock_acquire();
    PccGcExternalResourceNode *node =
        pcc_gc_external_find_locked(resource_id);
    if (
        node != NULL
        && node->state == PCC_GC_EXTERNAL_STATE_LIVE
        && node->retain_count > 0
    ) {
        node->retain_count++;
        result = node->retain_count;
    }
    pcc_gc_external_lock_release();
    return result;
}

int64_t pcc_gc_external_resource_release_after_fence(
    uint64_t resource_id
) {
    int64_t result = -1;
    pcc_gc_external_lock_acquire();
    PccGcExternalResourceNode *node =
        pcc_gc_external_find_locked(resource_id);
    if (
        node != NULL
        && node->state == PCC_GC_EXTERNAL_STATE_LIVE
        && node->retain_count > 0
    ) {
        node->retain_count--;
        result = node->retain_count;
        if (node->retain_count == 0) {
            node->state = PCC_GC_EXTERNAL_STATE_PENDING_RELEASE;
            __atomic_add_fetch(
                &pcc_gc_external_pending, 1, __ATOMIC_RELAXED
            );
            if (node->fence_complete != 0) {
                __atomic_add_fetch(
                    &pcc_gc_external_ready, 1, __ATOMIC_RELEASE
                );
            }
        }
    }
    pcc_gc_external_lock_release();
    return result;
}

int64_t pcc_gc_external_resource_mark_fence_complete(
    uint64_t resource_id
) {
    int64_t result = -1;
    pcc_gc_external_lock_acquire();
    PccGcExternalResourceNode *node =
        pcc_gc_external_find_locked(resource_id);
    if (node != NULL) {
        result = 0;
        if (node->fence_complete == 0) {
            node->fence_complete = 1;
            if (node->state == PCC_GC_EXTERNAL_STATE_PENDING_RELEASE) {
                __atomic_add_fetch(
                    &pcc_gc_external_ready, 1, __ATOMIC_RELEASE
                );
            }
        }
    }
    pcc_gc_external_lock_release();
    return result;
}

int64_t pcc_gc_external_resource_poll(void) {
    if (__atomic_load_n(&pcc_gc_external_ready, __ATOMIC_ACQUIRE) == 0) {
        return 0;
    }

    int64_t processed = 0;
    for (;;) {
        pcc_gc_external_lock_acquire();
        PccGcExternalResourceNode **link = &pcc_gc_external_resources;
        while (
            *link != NULL
            && !(
                (*link)->state == PCC_GC_EXTERNAL_STATE_PENDING_RELEASE
                && (*link)->fence_complete != 0
            )
        ) {
            link = &(*link)->next;
        }
        PccGcExternalResourceNode *node = *link;
        if (node == NULL) {
            pcc_gc_external_lock_release();
            break;
        }

        /* Detach before invoking foreign code. A driver callback may re-enter
         * the runtime, and exactly-once release must not depend on that code. */
        *link = node->next;
        node->next = NULL;
        __atomic_sub_fetch(&pcc_gc_external_active, 1, __ATOMIC_RELAXED);
        __atomic_sub_fetch(&pcc_gc_external_pending, 1, __ATOMIC_RELAXED);
        __atomic_sub_fetch(&pcc_gc_external_ready, 1, __ATOMIC_RELEASE);
        pcc_gc_external_lock_release();

        int64_t release_rc = node->release_fn(
            node->native_handle, node->release_context
        );
        if (node->context_free_fn != NULL) {
            node->context_free_fn(node->release_context);
        }
        if (release_rc != 0) {
            __atomic_add_fetch(
                &pcc_gc_external_release_failures, 1, __ATOMIC_RELAXED
            );
            __atomic_store_n(
                &pcc_gc_external_last_release_error,
                release_rc,
                __ATOMIC_RELAXED
            );
        }
        __atomic_add_fetch(
            &pcc_gc_external_released, 1, __ATOMIC_RELAXED
        );
        processed++;
        free(node);
    }
    return processed;
}

int64_t pcc_gc_external_resource_backend(uint64_t resource_id) {
    int64_t result = -1;
    pcc_gc_external_lock_acquire();
    PccGcExternalResourceNode *node =
        pcc_gc_external_find_locked(resource_id);
    if (node != NULL) result = node->registered_backend;
    pcc_gc_external_lock_release();
    return result;
}

int64_t pcc_gc_external_resource_active_count(void) {
    return __atomic_load_n(&pcc_gc_external_active, __ATOMIC_RELAXED);
}

int64_t pcc_gc_external_resource_pending_count(void) {
    return __atomic_load_n(&pcc_gc_external_pending, __ATOMIC_RELAXED);
}

int64_t pcc_gc_external_resource_release_count(void) {
    return __atomic_load_n(&pcc_gc_external_released, __ATOMIC_RELAXED);
}

int64_t pcc_gc_external_resource_release_failure_count(void) {
    return __atomic_load_n(
        &pcc_gc_external_release_failures, __ATOMIC_RELAXED
    );
}

int64_t pcc_gc_external_resource_last_release_error(void) {
    return __atomic_load_n(
        &pcc_gc_external_last_release_error, __ATOMIC_RELAXED
    );
}

typedef struct PccGcExternalMetalBufferContext {
    char *runtime_library_path;
} PccGcExternalMetalBufferContext;

static int64_t pcc_gc_external_metal_buffer_release(
    uint64_t native_handle,
    void *opaque_context
) {
    PccGcExternalMetalBufferContext *context =
        (PccGcExternalMetalBufferContext *)opaque_context;
    if (context == NULL || context->runtime_library_path == NULL) return -1;
    return pcc_metal_buffer_runtime_release_prebuilt(
        context->runtime_library_path, native_handle
    );
}

static void pcc_gc_external_metal_buffer_context_free(
    void *opaque_context
) {
    PccGcExternalMetalBufferContext *context =
        (PccGcExternalMetalBufferContext *)opaque_context;
    if (context == NULL) return;
    free(context->runtime_library_path);
    free(context);
}

uint64_t pcc_gc_external_metal_buffer_register(
    const char *runtime_library_path,
    uint64_t native_handle
) {
    if (
        runtime_library_path == NULL
        || runtime_library_path[0] == '\0'
        || native_handle == 0
    ) {
        return 0;
    }
    size_t path_nbytes = strlen(runtime_library_path) + 1;
    PccGcExternalMetalBufferContext *context =
        (PccGcExternalMetalBufferContext *)calloc(1, sizeof(*context));
    if (context == NULL) return 0;
    context->runtime_library_path = (char *)malloc(path_nbytes);
    if (context->runtime_library_path == NULL) {
        free(context);
        return 0;
    }
    memcpy(context->runtime_library_path, runtime_library_path, path_nbytes);

    uint64_t resource_id = pcc_gc_external_resource_register(
        PCC_GC_EXTERNAL_RESOURCE_GPU_BUFFER,
        native_handle,
        pcc_gc_external_metal_buffer_release,
        context,
        pcc_gc_external_metal_buffer_context_free
    );
    if (resource_id == 0) {
        pcc_gc_external_metal_buffer_context_free(context);
    }
    return resource_id;
}
