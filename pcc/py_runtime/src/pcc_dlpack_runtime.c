#include "py_internal.h"

#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef void (*PccPyCapsuleDestructor)(PyObject *);
extern PyObject *PyCapsule_New(
    void *pointer,
    const char *name,
    PccPyCapsuleDestructor destructor
);
extern void *PyCapsule_GetPointer(PyObject *capsule, const char *name);
extern const char *PyCapsule_GetName(PyObject *capsule);
extern int PyCapsule_IsValid(PyObject *capsule, const char *name);
extern int PyCapsule_SetName(PyObject *capsule, const char *name);

enum {
    PCC_DLPACK_DEVICE_METAL = 8,
    PCC_DLPACK_NAME_INVALID = 0,
    PCC_DLPACK_NAME_DLTENSOR = 1,
    PCC_DLPACK_NAME_USED_DLTENSOR = 2
};

typedef struct PccDlDevice {
    int32_t device_type;
    int32_t device_id;
} PccDlDevice;

typedef struct PccDlDataType {
    uint8_t code;
    uint8_t bits;
    uint16_t lanes;
} PccDlDataType;

typedef struct PccDlTensor {
    void *data;
    PccDlDevice device;
    int32_t ndim;
    PccDlDataType dtype;
    int64_t *shape;
    int64_t *strides;
    uint64_t byte_offset;
} PccDlTensor;

typedef struct PccDlManagedTensor PccDlManagedTensor;
typedef void (*PccDlManagedTensorDeleter)(PccDlManagedTensor *);

struct PccDlManagedTensor {
    PccDlTensor dl_tensor;
    void *manager_ctx;
    PccDlManagedTensorDeleter deleter;
};

typedef struct PccDlpackManager {
    PccDlManagedTensor managed;
    int64_t shape[PCC_DLPACK_MAX_NDIM];
    uint64_t external_resource_id;
    int32_t released;
} PccDlpackManager;

_Static_assert(sizeof(PccDlDevice) == 8, "DLDevice ABI drift");
_Static_assert(sizeof(PccDlDataType) == 4, "DLDataType ABI drift");
_Static_assert(sizeof(PccDlTensor) == 48, "DLTensor ABI drift");
_Static_assert(sizeof(PccDlManagedTensor) == 64, "DLManagedTensor ABI drift");
_Static_assert(
    sizeof(PccDlpackBufferHandlePacket) == 120,
    "PccDlpackBufferHandlePacket ABI drift"
);

static void pcc_dlpack_managed_deleter(PccDlManagedTensor *managed) {
    if (managed == NULL || managed->manager_ctx == NULL) return;
    PccDlpackManager *owner = (PccDlpackManager *)managed->manager_ctx;
    if (__atomic_exchange_n(&owner->released, 1, __ATOMIC_ACQ_REL) != 0) {
        return;
    }
    (void)pcc_gc_external_resource_release_after_fence(
        owner->external_resource_id
    );
    free(owner);
}

static void pcc_dlpack_capsule_destructor(PyObject *capsule) {
    if (!PyCapsule_IsValid(capsule, "dltensor")) return;
    PccDlManagedTensor *managed = (PccDlManagedTensor *)PyCapsule_GetPointer(
        capsule, "dltensor"
    );
    if (managed != NULL && managed->deleter != NULL) {
        managed->deleter(managed);
    }
}

static int pcc_dlpack_compute_nbytes(
    int32_t bits,
    int32_t lanes,
    int64_t ndim,
    const int64_t *shape,
    uint64_t *out_nbytes
) {
    if (
        bits <= 0 || bits > UINT8_MAX || lanes <= 0 || lanes > UINT16_MAX
        || ndim <= 0 || ndim > PCC_DLPACK_MAX_NDIM || shape == NULL
        || out_nbytes == NULL
    ) {
        return -1;
    }
    uint64_t lane_bits = (uint64_t)bits * (uint64_t)lanes;
    if (lane_bits == 0 || lane_bits % 8 != 0) return -1;
    uint64_t elements = 1;
    for (int64_t i = 0; i < ndim; i++) {
        if (shape[i] <= 0) return -1;
        uint64_t dim = (uint64_t)shape[i];
        if (elements > UINT64_MAX / dim) return -1;
        elements *= dim;
    }
    uint64_t item_bytes = lane_bits / 8;
    if (elements > UINT64_MAX / item_bytes) return -1;
    *out_nbytes = elements * item_bytes;
    return 0;
}

int64_t pcc_dlpack_buffer_handle_packet_size(void) {
    return (int64_t)sizeof(PccDlpackBufferHandlePacket);
}

PyObject *pcc_dlpack_metal_capsule_new(
    uint64_t external_resource_id,
    uint64_t native_handle,
    int32_t dtype_code,
    int32_t dtype_bits,
    int32_t dtype_lanes,
    int64_t ndim,
    const int64_t *shape
) {
    uint64_t nbytes = 0;
    if (
        external_resource_id == 0 || native_handle == 0
        || dtype_code < 0 || dtype_code > UINT8_MAX
        || pcc_gc_external_resource_backend(external_resource_id) < 0
        || pcc_dlpack_compute_nbytes(
            dtype_bits, dtype_lanes, ndim, shape, &nbytes
        ) != 0
    ) {
        return NULL;
    }

    PccDlpackManager *owner = (PccDlpackManager *)calloc(1, sizeof(*owner));
    if (owner == NULL) return NULL;
    for (int64_t i = 0; i < ndim; i++) owner->shape[i] = shape[i];
    owner->external_resource_id = external_resource_id;
    owner->managed.dl_tensor.data = (void *)(uintptr_t)native_handle;
    owner->managed.dl_tensor.device.device_type = PCC_DLPACK_DEVICE_METAL;
    owner->managed.dl_tensor.device.device_id = 0;
    owner->managed.dl_tensor.ndim = (int32_t)ndim;
    owner->managed.dl_tensor.dtype.code = (uint8_t)dtype_code;
    owner->managed.dl_tensor.dtype.bits = (uint8_t)dtype_bits;
    owner->managed.dl_tensor.dtype.lanes = (uint16_t)dtype_lanes;
    owner->managed.dl_tensor.shape = owner->shape;
    owner->managed.dl_tensor.strides = NULL;
    owner->managed.dl_tensor.byte_offset = 0;
    owner->managed.manager_ctx = owner;
    owner->managed.deleter = pcc_dlpack_managed_deleter;

    PyObject *capsule = PyCapsule_New(
        &owner->managed, "dltensor", pcc_dlpack_capsule_destructor
    );
    if (capsule == NULL) {
        free(owner);
        return NULL;
    }
    (void)nbytes;
    return capsule;
}

int64_t pcc_dlpack_capsule_name_code(PyObject *capsule) {
    const char *name = PyCapsule_GetName(capsule);
    if (name == NULL) return PCC_DLPACK_NAME_INVALID;
    if (strcmp(name, "dltensor") == 0) return PCC_DLPACK_NAME_DLTENSOR;
    if (strcmp(name, "used_dltensor") == 0) {
        return PCC_DLPACK_NAME_USED_DLTENSOR;
    }
    return PCC_DLPACK_NAME_INVALID;
}

int64_t pcc_dlpack_capsule_consume(
    PyObject *capsule,
    PccDlpackBufferHandlePacket *out_handle,
    void **out_managed_tensor
) {
    if (out_handle == NULL || out_managed_tensor == NULL) return -1;
    *out_managed_tensor = NULL;
    if (!PyCapsule_IsValid(capsule, "dltensor")) return 2;
    PccDlManagedTensor *managed = (PccDlManagedTensor *)PyCapsule_GetPointer(
        capsule, "dltensor"
    );
    if (
        managed == NULL || managed->manager_ctx == NULL
        || managed->deleter == NULL || managed->dl_tensor.data == NULL
        || managed->dl_tensor.device.device_type != PCC_DLPACK_DEVICE_METAL
        || managed->dl_tensor.device.device_id != 0
        || managed->dl_tensor.ndim <= 0
        || managed->dl_tensor.ndim > PCC_DLPACK_MAX_NDIM
        || managed->dl_tensor.shape == NULL
        || managed->dl_tensor.strides != NULL
        || managed->dl_tensor.byte_offset != 0
    ) {
        return -2;
    }
    PccDlpackManager *owner = (PccDlpackManager *)managed->manager_ctx;
    uint64_t nbytes = 0;
    if (pcc_dlpack_compute_nbytes(
        managed->dl_tensor.dtype.bits,
        managed->dl_tensor.dtype.lanes,
        managed->dl_tensor.ndim,
        managed->dl_tensor.shape,
        &nbytes
    ) != 0) {
        return -3;
    }

    memset(out_handle, 0, sizeof(*out_handle));
    out_handle->native_handle = (uint64_t)(uintptr_t)managed->dl_tensor.data;
    out_handle->external_resource_id = owner->external_resource_id;
    out_handle->nbytes = nbytes;
    out_handle->ndim = managed->dl_tensor.ndim;
    out_handle->device_type = managed->dl_tensor.device.device_type;
    out_handle->device_id = managed->dl_tensor.device.device_id;
    out_handle->dtype_code = managed->dl_tensor.dtype.code;
    out_handle->dtype_bits = managed->dl_tensor.dtype.bits;
    out_handle->dtype_lanes = managed->dl_tensor.dtype.lanes;
    for (int64_t i = 0; i < out_handle->ndim; i++) {
        out_handle->shape[i] = managed->dl_tensor.shape[i];
    }
    if (PyCapsule_SetName(capsule, "used_dltensor") != 0) {
        memset(out_handle, 0, sizeof(*out_handle));
        return -4;
    }
    *out_managed_tensor = managed;
    return 0;
}

int64_t pcc_dlpack_managed_tensor_release(void *managed_tensor) {
    PccDlManagedTensor *managed = (PccDlManagedTensor *)managed_tensor;
    if (managed == NULL || managed->deleter == NULL) return -1;
    managed->deleter(managed);
    return 0;
}

