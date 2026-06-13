#include "py_runtime.h"

#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

typedef void (*PccMetalFenceCompleteFn)(void *);
typedef int64_t (*PccMetalSourceRuntimeBridgeFn)(
    const char *metal_source,
    uint64_t metal_source_nbytes,
    void **buffers,
    void **scalars,
    PccMetalFenceCompleteFn fence_complete,
    void *fence_context,
    bool wait_until_completed
);
typedef int64_t (*PccMetalMetallibRuntimeBridgeFn)(
    const char *metallib_path,
    void **buffers,
    void **scalars,
    PccMetalFenceCompleteFn fence_complete,
    void *fence_context,
    bool wait_until_completed
);
typedef int64_t (*PccMetalBufferRuntimeCreateFn)(uint64_t, void **);
typedef int64_t (*PccMetalBufferRuntimeLengthFn)(void *, uint64_t *);
typedef int64_t (*PccMetalBufferRuntimeReleaseFn)(void *);
typedef int64_t (*PccMetalBufferRuntimeWriteFn)(void *, uint64_t, const void *, uint64_t);
typedef int64_t (*PccMetalBufferRuntimeReadFn)(void *, uint64_t, void *, uint64_t);

enum {
    PCC_METAL_RUNTIME_OK = 0,
    PCC_METAL_RUNTIME_ERR_MISSING_LIBRARY = -1,
    PCC_METAL_RUNTIME_ERR_MISSING_SYMBOL = -2,
    PCC_METAL_RUNTIME_ERR_MISSING_SOURCE = -3,
    PCC_METAL_RUNTIME_ERR_ASYNC_UNSUPPORTED = -4,
    PCC_METAL_RUNTIME_ERR_MISSING_BUFFERS = -5,
    PCC_METAL_RUNTIME_ERR_MISSING_SCALARS = -6,
    PCC_METAL_RUNTIME_ERR_ALLOC = -7,
    PCC_METAL_RUNTIME_ERR_DLOPEN = -8,
    PCC_METAL_RUNTIME_ERR_DLSYM = -9,
    PCC_METAL_RUNTIME_ERR_MISSING_OUTPUT = -10,
    PCC_METAL_RUNTIME_ERR_MISSING_DATA = -11,
    PCC_METAL_RUNTIME_ERR_MISSING_BUFFER = -12,
    PCC_METAL_RUNTIME_ERR_MISSING_METALLIB = -13
};

static int64_t pcc_metal_load_symbol(
    const char *library_path,
    const char *symbol,
    void **out_handle,
    void **out_symbol
) {
    if (library_path == NULL || library_path[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_LIBRARY;
    }
    if (symbol == NULL || symbol[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_SYMBOL;
    }
    if (out_handle == NULL || out_symbol == NULL) {
        return PCC_METAL_RUNTIME_ERR_MISSING_OUTPUT;
    }

    void *handle = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        return PCC_METAL_RUNTIME_ERR_DLOPEN;
    }

    dlerror();
    void *fn = dlsym(handle, symbol);
    const char *sym_error = dlerror();
    if (sym_error != NULL || fn == NULL) {
        dlclose(handle);
        return PCC_METAL_RUNTIME_ERR_DLSYM;
    }

    *out_handle = handle;
    *out_symbol = fn;
    return PCC_METAL_RUNTIME_OK;
}

static void **pcc_metal_build_buffer_slots(
    const uint64_t *native_buffer_ptrs,
    uint64_t num_buffers
) {
    if (num_buffers == 0) {
        return NULL;
    }
    void **buffers = (void **)calloc((size_t)num_buffers, sizeof(void *));
    if (buffers == NULL) {
        return NULL;
    }
    for (uint64_t i = 0; i < num_buffers; i++) {
        buffers[i] = (void *)(uintptr_t)native_buffer_ptrs[i];
    }
    return buffers;
}

static void **pcc_metal_build_scalar_slots(
    const uint8_t *scalar_payload,
    const uint64_t *scalar_offsets,
    uint64_t num_scalars
) {
    if (num_scalars == 0) {
        return NULL;
    }
    void **scalars = (void **)calloc((size_t)num_scalars, sizeof(void *));
    if (scalars == NULL) {
        return NULL;
    }
    for (uint64_t i = 0; i < num_scalars; i++) {
        scalars[i] = (void *)(uintptr_t)(scalar_payload + scalar_offsets[i]);
    }
    return scalars;
}

int64_t pcc_metal_source_runtime_call_prebuilt(
    const char *bridge_library_path,
    const char *symbol,
    const uint8_t *metal_source,
    uint64_t metal_source_nbytes,
    const uint64_t *native_buffer_ptrs,
    uint64_t num_buffers,
    const uint8_t *scalar_payload,
    const uint64_t *scalar_offsets,
    uint64_t num_scalars,
    int32_t wait_until_completed
) {
    if (bridge_library_path == NULL || bridge_library_path[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_LIBRARY;
    }
    if (symbol == NULL || symbol[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_SYMBOL;
    }
    if (metal_source == NULL || metal_source_nbytes == 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_SOURCE;
    }
    if (wait_until_completed == 0) {
        return PCC_METAL_RUNTIME_ERR_ASYNC_UNSUPPORTED;
    }
    if (num_buffers > 0 && native_buffer_ptrs == NULL) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFERS;
    }
    if (num_scalars > 0 && (scalar_payload == NULL || scalar_offsets == NULL)) {
        return PCC_METAL_RUNTIME_ERR_MISSING_SCALARS;
    }

    void **buffers = pcc_metal_build_buffer_slots(native_buffer_ptrs, num_buffers);
    if (num_buffers > 0 && buffers == NULL) {
        return PCC_METAL_RUNTIME_ERR_ALLOC;
    }

    void **scalars = pcc_metal_build_scalar_slots(
        scalar_payload,
        scalar_offsets,
        num_scalars
    );
    if (num_scalars > 0 && scalars == NULL) {
        free(buffers);
        return PCC_METAL_RUNTIME_ERR_ALLOC;
    }

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        bridge_library_path,
        symbol,
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        free(scalars);
        free(buffers);
        return load_rc;
    }
    PccMetalSourceRuntimeBridgeFn bridge = (PccMetalSourceRuntimeBridgeFn)symbol_ptr;

    int64_t rc = bridge(
        (const char *)metal_source,
        metal_source_nbytes,
        buffers,
        scalars,
        NULL,
        NULL,
        true
    );

    dlclose(handle);
    free(scalars);
    free(buffers);
    return rc;
}

int64_t pcc_metal_metallib_runtime_call_prebuilt(
    const char *bridge_library_path,
    const char *symbol,
    const char *metallib_path,
    const uint64_t *native_buffer_ptrs,
    uint64_t num_buffers,
    const uint8_t *scalar_payload,
    const uint64_t *scalar_offsets,
    uint64_t num_scalars,
    int32_t wait_until_completed
) {
    if (bridge_library_path == NULL || bridge_library_path[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_LIBRARY;
    }
    if (symbol == NULL || symbol[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_SYMBOL;
    }
    if (metallib_path == NULL || metallib_path[0] == '\0') {
        return PCC_METAL_RUNTIME_ERR_MISSING_METALLIB;
    }
    if (wait_until_completed == 0) {
        return PCC_METAL_RUNTIME_ERR_ASYNC_UNSUPPORTED;
    }
    if (num_buffers > 0 && native_buffer_ptrs == NULL) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFERS;
    }
    if (num_scalars > 0 && (scalar_payload == NULL || scalar_offsets == NULL)) {
        return PCC_METAL_RUNTIME_ERR_MISSING_SCALARS;
    }

    void **buffers = pcc_metal_build_buffer_slots(native_buffer_ptrs, num_buffers);
    if (num_buffers > 0 && buffers == NULL) {
        return PCC_METAL_RUNTIME_ERR_ALLOC;
    }

    void **scalars = pcc_metal_build_scalar_slots(
        scalar_payload,
        scalar_offsets,
        num_scalars
    );
    if (num_scalars > 0 && scalars == NULL) {
        free(buffers);
        return PCC_METAL_RUNTIME_ERR_ALLOC;
    }

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        bridge_library_path,
        symbol,
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        free(scalars);
        free(buffers);
        return load_rc;
    }
    PccMetalMetallibRuntimeBridgeFn bridge =
        (PccMetalMetallibRuntimeBridgeFn)symbol_ptr;

    int64_t rc = bridge(
        metallib_path,
        buffers,
        scalars,
        NULL,
        NULL,
        true
    );

    dlclose(handle);
    free(scalars);
    free(buffers);
    return rc;
}

int64_t pcc_metal_buffer_runtime_create_prebuilt(
    const char *runtime_library_path,
    uint64_t nbytes,
    uint64_t *out_buffer_ptr
) {
    if (out_buffer_ptr == NULL) {
        return PCC_METAL_RUNTIME_ERR_MISSING_OUTPUT;
    }
    *out_buffer_ptr = 0;

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        runtime_library_path,
        "pcc_metal_buffer_runtime_create",
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        return load_rc;
    }

    void *native_buffer = NULL;
    PccMetalBufferRuntimeCreateFn create_fn =
        (PccMetalBufferRuntimeCreateFn)symbol_ptr;
    int64_t rc = create_fn(nbytes, &native_buffer);
    if (rc == 0 && native_buffer != NULL) {
        *out_buffer_ptr = (uint64_t)(uintptr_t)native_buffer;
    }
    dlclose(handle);
    return rc;
}

int64_t pcc_metal_buffer_runtime_length_prebuilt(
    const char *runtime_library_path,
    uint64_t buffer_ptr,
    uint64_t *out_nbytes
) {
    if (buffer_ptr == 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFER;
    }
    if (out_nbytes == NULL) {
        return PCC_METAL_RUNTIME_ERR_MISSING_OUTPUT;
    }
    *out_nbytes = 0;

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        runtime_library_path,
        "pcc_metal_buffer_runtime_length",
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        return load_rc;
    }

    PccMetalBufferRuntimeLengthFn length_fn =
        (PccMetalBufferRuntimeLengthFn)symbol_ptr;
    int64_t rc = length_fn((void *)(uintptr_t)buffer_ptr, out_nbytes);
    dlclose(handle);
    return rc;
}

int64_t pcc_metal_buffer_runtime_write_prebuilt(
    const char *runtime_library_path,
    uint64_t buffer_ptr,
    uint64_t offset,
    const uint8_t *src,
    uint64_t nbytes
) {
    if (buffer_ptr == 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFER;
    }
    if (src == NULL && nbytes > 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_DATA;
    }

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        runtime_library_path,
        "pcc_metal_buffer_runtime_write",
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        return load_rc;
    }

    PccMetalBufferRuntimeWriteFn write_fn =
        (PccMetalBufferRuntimeWriteFn)symbol_ptr;
    int64_t rc = write_fn((void *)(uintptr_t)buffer_ptr, offset, src, nbytes);
    dlclose(handle);
    return rc;
}

int64_t pcc_metal_buffer_runtime_read_prebuilt(
    const char *runtime_library_path,
    uint64_t buffer_ptr,
    uint64_t offset,
    uint8_t *dst,
    uint64_t nbytes
) {
    if (buffer_ptr == 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFER;
    }
    if (dst == NULL && nbytes > 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_DATA;
    }

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        runtime_library_path,
        "pcc_metal_buffer_runtime_read",
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        return load_rc;
    }

    PccMetalBufferRuntimeReadFn read_fn =
        (PccMetalBufferRuntimeReadFn)symbol_ptr;
    int64_t rc = read_fn((void *)(uintptr_t)buffer_ptr, offset, dst, nbytes);
    dlclose(handle);
    return rc;
}

int64_t pcc_metal_buffer_runtime_release_prebuilt(
    const char *runtime_library_path,
    uint64_t buffer_ptr
) {
    if (buffer_ptr == 0) {
        return PCC_METAL_RUNTIME_ERR_MISSING_BUFFER;
    }

    void *handle = NULL;
    void *symbol_ptr = NULL;
    int64_t load_rc = pcc_metal_load_symbol(
        runtime_library_path,
        "pcc_metal_buffer_runtime_release",
        &handle,
        &symbol_ptr
    );
    if (load_rc != PCC_METAL_RUNTIME_OK) {
        return load_rc;
    }

    PccMetalBufferRuntimeReleaseFn release_fn =
        (PccMetalBufferRuntimeReleaseFn)symbol_ptr;
    int64_t rc = release_fn((void *)(uintptr_t)buffer_ptr);
    dlclose(handle);
    return rc;
}
