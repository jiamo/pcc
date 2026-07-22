"""DLPack-shaped Metal tensor ownership for Kernel IR."""

import ctypes
import gc

import pytest

from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccFenceToken, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, MemoryScope, ScalarType
from pcc.kernel_ir.metal_buffer import allocate_metal_native_buffers_for_plan
from pcc.kernel_ir.metal_dlpack import (
    DLDataType,
    DLDevice,
    DLManagedTensor,
    DLManagedTensorDeleter,
    DLTensor,
    DLPACK_CAPSULE_NAME,
    STATUS_METAL_DLPACK_ALIAS_DROPPED,
    STATUS_METAL_DLPACK_CAPSULE_EXPORTED,
    STATUS_METAL_DLPACK_CAPSULE_IMPORTED,
    STATUS_METAL_DLPACK_NATIVE_RELEASED,
    STATUS_METAL_DLPACK_RECLAIM_PENDING,
    STATUS_METAL_DLPACK_RELEASE_DEFERRED,
    STATUS_METAL_DLPACK_TENSOR_EXPORTED,
    STATUS_METAL_DLPACK_TENSOR_IMPORTED,
    USED_DLPACK_CAPSULE_NAME,
    export_metal_dlpack_protocol,
    export_metal_dlpack_py_capsule,
    MetalDlpackOwnershipError,
    MetalDlpackTensorOwner,
    import_metal_dlpack_tensor,
    import_metal_dlpack_py_capsule,
    pycapsule_name,
)
from pcc.kernel_ir.metal_launch import plan_metal_launch


class _FakeCFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


class _FakeBufferRuntime:
    def __init__(self):
        self.next_ptr = 0xAA0000
        self.storage: dict[int, bytearray] = {}
        self.calls = []
        self.pcc_metal_buffer_runtime_create = _FakeCFunction(self.create)
        self.pcc_metal_buffer_runtime_length = _FakeCFunction(self.length)
        self.pcc_metal_buffer_runtime_release = _FakeCFunction(self.release)
        self.pcc_metal_buffer_runtime_write = _FakeCFunction(self.transfer_not_used)
        self.pcc_metal_buffer_runtime_read = _FakeCFunction(self.transfer_not_used)

    def create(self, nbytes, out_buffer):
        ptr = self.next_ptr
        self.next_ptr += 0x1000
        self.storage[ptr] = bytearray(int(nbytes.value))
        self.calls.append(("create", int(nbytes.value), ptr))
        ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(ptr)
        return 0

    def length(self, buffer, out_nbytes):
        ptr = int(buffer.value)
        self.calls.append(("length", ptr))
        ctypes.cast(out_nbytes, ctypes.POINTER(ctypes.c_uint64))[0] = ctypes.c_uint64(
            len(self.storage[ptr])
        )
        return 0

    def release(self, buffer):
        ptr = int(buffer.value)
        self.calls.append(("release", ptr))
        self.storage.pop(ptr, None)
        return 0

    def transfer_not_used(self, *args):
        raise AssertionError("DLPack ownership tests should not transfer host bytes")


def _matrix_module() -> KernelModule:
    func = KernelFunc(
        name="dlpack_matrix_kernel",
        params=(
            BufferParam("A", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
            BufferParam("C", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("A", "C"), {"extent": 6}),
            KernelOp("copy", ("A", "C")),
        ),
        grid=(1,),
        threads=32,
    )
    return KernelModule("dlpack_matrix_mod", funcs=(func,))


def _plan_and_allocations(tmp_path, fake):
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    plan = plan_metal_launch(_matrix_module(), args)
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    allocations = allocate_metal_native_buffers_for_plan(
        library_path,
        plan,
        cdll_factory=lambda path: fake,
    )
    return plan, allocations


def test_dlpack_export_import_is_one_shot_and_pod_handle_only(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    tensor = owner.export("A")
    exported = tensor.to_dict()

    assert exported["status"] == STATUS_METAL_DLPACK_TENSOR_EXPORTED
    assert exported["descriptor"]["descriptor_contains_pyobject"] is False
    assert exported["descriptor"]["dl_device_type"] == "kDLMetal"
    assert exported["descriptor"]["shape"] == [2, 3]
    assert "owner" not in exported["descriptor"]
    assert "allocation_set" not in exported["descriptor"]

    imported = import_metal_dlpack_tensor(
        tensor,
        expected_dtype="f32",
        expected_shape=(2, 3),
        expected_device="metal:0",
    )
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(imported.buffer_handle)

    assert imported.status == STATUS_METAL_DLPACK_TENSOR_IMPORTED
    assert imported.to_dict()["runtime_launch_executed"] is False
    assert set(imported.buffer_handle.dlpack_descriptor()) == {
        "handle_id",
        "nbytes",
        "dtype",
        "device",
    }
    assert args.validate() is args
    with pytest.raises(MetalDlpackOwnershipError, match="already consumed"):
        import_metal_dlpack_tensor(tensor)

    fence = PccFenceToken()
    tensor.deleter(fence)
    fence.complete()
    owner.reclaim_completed()
    allocations.release_all()


def test_dlpack_deleter_defers_native_release_until_fence_completion(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    tensor = owner.export("A")
    handle_id = tensor.descriptor.handle_id
    ptr = tensor.descriptor.native_mtlbuffer_ptr
    fence = PccFenceToken()

    release_result = tensor.deleter(fence)
    reclaim_before_fence = owner.reclaim_completed()

    assert release_result.status == STATUS_METAL_DLPACK_RELEASE_DEFERRED
    assert release_result.pending_count == 1
    assert release_result.native_release_executed is False
    assert reclaim_before_fence.status == STATUS_METAL_DLPACK_RECLAIM_PENDING
    assert ("release", ptr) not in fake.calls

    fence.complete()
    reclaim_after_fence = owner.reclaim_completed()

    assert reclaim_after_fence.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    assert reclaim_after_fence.reclaimed_handle_ids == (handle_id,)
    assert allocations.to_dict()["released_handle_ids"] == [handle_id]
    assert ("release", ptr) in fake.calls

    allocations.release_all()
    release_calls = [call for call in fake.calls if call[0] == "release"]
    assert release_calls.count(("release", ptr)) == 1
    assert len(release_calls) == 2


def test_dlpack_aliases_hold_native_buffer_until_last_alias_and_fence(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    first_alias = owner.export("A")
    second_alias = owner.export("A")
    ptr = first_alias.descriptor.native_mtlbuffer_ptr
    fence = PccFenceToken()

    first_drop = first_alias.deleter(fence)
    fence.complete()
    reclaim_while_aliased = owner.reclaim_completed()

    assert first_drop.status == STATUS_METAL_DLPACK_ALIAS_DROPPED
    assert first_drop.active_aliases == 1
    assert reclaim_while_aliased.status == STATUS_METAL_DLPACK_RECLAIM_PENDING
    assert ("release", ptr) not in fake.calls

    second_drop = second_alias.deleter(fence)
    reclaim_after_last_alias = owner.reclaim_completed()

    assert second_drop.status == STATUS_METAL_DLPACK_RELEASE_DEFERRED
    assert reclaim_after_last_alias.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    assert ("release", ptr) in fake.calls
    allocations.release_all()


def test_dlpack_rejects_bad_lifecycle_edges(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    tensor = owner.export("A")

    with pytest.raises(MetalDlpackOwnershipError, match="requires a PccFenceToken"):
        tensor.deleter(None)  # type: ignore[arg-type]

    fence = PccFenceToken()
    tensor.deleter(fence)
    with pytest.raises(MetalDlpackOwnershipError, match="already called"):
        tensor.deleter(fence)

    allocations.release_all()
    with pytest.raises(MetalDlpackOwnershipError, match="released allocation set"):
        owner.export("C")


def test_dlpack_py_capsule_consumes_once_and_renames_to_used(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    tensor = owner.export("A")

    exported = export_metal_dlpack_py_capsule(tensor)
    assert exported.status == STATUS_METAL_DLPACK_CAPSULE_EXPORTED
    assert exported.to_dict()["capsule_name"] == DLPACK_CAPSULE_NAME
    assert exported.to_dict()["abi"] == "DLManagedTensor"
    assert exported.to_dict()["descriptor"]["descriptor_contains_pyobject"] is False

    managed_pointer = ctypes.cast(
        ctypes.c_void_p(exported.pointer_id), ctypes.POINTER(DLManagedTensor)
    )
    dl_tensor = managed_pointer.contents.dl_tensor
    assert ctypes.addressof(managed_pointer.contents) == exported.pointer_id
    assert int(managed_pointer.contents.manager_ctx) == exported.pointer_id
    assert int(dl_tensor.data) == tensor.descriptor.native_mtlbuffer_ptr
    assert (dl_tensor.device.device_type, dl_tensor.device.device_id) == (8, 0)
    assert dl_tensor.ndim == 2
    assert [dl_tensor.shape[i] for i in range(dl_tensor.ndim)] == [2, 3]
    assert (dl_tensor.dtype.code, dl_tensor.dtype.bits, dl_tensor.dtype.lanes) == (
        2,
        32,
        1,
    )
    assert not bool(dl_tensor.strides)
    assert bool(managed_pointer.contents.deleter)

    imported = import_metal_dlpack_py_capsule(
        exported.capsule,
        expected_dtype="f32",
        expected_shape=(2, 3),
        expected_device="metal:0",
    )
    assert imported.status == STATUS_METAL_DLPACK_CAPSULE_IMPORTED
    assert imported.imported.status == STATUS_METAL_DLPACK_TENSOR_IMPORTED
    assert pycapsule_name(exported.capsule) == USED_DLPACK_CAPSULE_NAME
    assert imported.imported.buffer_handle.dlpack_descriptor() == {
        "handle_id": tensor.descriptor.handle_id,
        "nbytes": tensor.descriptor.nbytes,
        "dtype": "f32",
        "device": "metal:0",
    }

    with pytest.raises(MetalDlpackOwnershipError, match="name 'dltensor'"):
        import_metal_dlpack_py_capsule(exported.capsule)

    fence = PccFenceToken()
    release = imported.deleter(fence)
    assert release.status == STATUS_METAL_DLPACK_RELEASE_DEFERRED
    fence.complete()
    reclaimed = owner.reclaim_completed()
    assert reclaimed.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    allocations.release_all()


def test_dlmanagedtensor_ctypes_layout_matches_classic_64bit_c_abi():
    assert ctypes.sizeof(DLDevice) == 8
    assert ctypes.sizeof(DLDataType) == 4
    assert ctypes.sizeof(DLTensor) == 48
    assert ctypes.sizeof(DLManagedTensor) == 64
    assert DLTensor.data.offset == 0
    assert DLTensor.shape.offset == 24
    assert DLTensor.byte_offset.offset == 40
    assert DLManagedTensor.manager_ctx.offset == 48
    assert DLManagedTensor.deleter.offset == 56


def test_dlpack_py_capsule_rejects_non_default_stream(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)

    rejected = owner.export("A")
    with pytest.raises(MetalDlpackOwnershipError, match="stream synchronization"):
        export_metal_dlpack_py_capsule(rejected, stream=7)
    rejected_fence = PccFenceToken()
    rejected.deleter(rejected_fence)
    rejected_fence.complete()
    owner.reclaim_completed()

    tensor = owner.export("C")
    exported = export_metal_dlpack_py_capsule(tensor)
    with pytest.raises(MetalDlpackOwnershipError, match="stream synchronization"):
        import_metal_dlpack_py_capsule(exported.capsule, stream=9)

    imported = import_metal_dlpack_py_capsule(exported.capsule)
    fence = PccFenceToken()
    imported.deleter(fence)
    fence.complete()
    owner.reclaim_completed()

    allocations.release_all()


def test_dlpack_protocol_is_one_shot_default_stream_and_fence_guarded(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    release_fence = PccFenceToken()
    protocol = export_metal_dlpack_protocol(
        owner.export("A"), release_fence=release_fence
    )

    assert protocol.__dlpack_device__() == (8, 0)
    assert protocol.to_dict()["default_stream_only"] is True
    assert protocol.to_dict()["consumed"] is False
    with pytest.raises(MetalDlpackOwnershipError, match="stream synchronization"):
        protocol.__dlpack__(stream=7)
    with pytest.raises(MetalDlpackOwnershipError, match="versioned DLPack"):
        protocol.__dlpack__(max_version=(1, 0))
    with pytest.raises(MetalDlpackOwnershipError, match="requested device"):
        protocol.__dlpack__(dl_device=(1, 0))
    with pytest.raises(MetalDlpackOwnershipError, match="requested copy"):
        protocol.__dlpack__(copy=True)

    capsule = protocol.__dlpack__()
    assert pycapsule_name(capsule) == DLPACK_CAPSULE_NAME
    assert protocol.to_dict()["consumed"] is True
    with pytest.raises(MetalDlpackOwnershipError, match="already consumed"):
        protocol.__dlpack__()

    imported = import_metal_dlpack_py_capsule(capsule)
    imported.deleter(release_fence)
    assert owner.reclaim_completed().status == STATUS_METAL_DLPACK_RECLAIM_PENDING
    release_fence.complete()
    assert owner.reclaim_completed().status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    allocations.release_all()


def test_dlpack_imports_foreign_dlmanagedtensor_and_defers_foreign_deleter(tmp_path):
    shape = (ctypes.c_int64 * 2)(2, 3)
    deleter_calls: list[int] = []

    @DLManagedTensorDeleter
    def foreign_deleter(pointer):
        deleter_calls.append(ctypes.addressof(pointer.contents))

    managed = DLManagedTensor(
        dl_tensor=DLTensor(
            data=ctypes.c_void_p(0xCC0000),
            device=DLDevice(8, 0),
            ndim=2,
            dtype=DLDataType(2, 32, 1),
            shape=ctypes.cast(shape, ctypes.POINTER(ctypes.c_int64)),
            strides=ctypes.POINTER(ctypes.c_int64)(),
            byte_offset=0,
        ),
        manager_ctx=ctypes.c_void_p(0x1234),
        deleter=foreign_deleter,
    )
    new = ctypes.pythonapi.PyCapsule_New
    new.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    new.restype = ctypes.py_object
    # PyCapsule_New stores the name POINTER without copying; the bytes object
    # must outlive the capsule or PyCapsule_IsValid reads freed memory.
    capsule_name = DLPACK_CAPSULE_NAME.encode()
    capsule = new(
        ctypes.c_void_p(ctypes.addressof(managed)),
        capsule_name,
        None,
    )

    imported = import_metal_dlpack_py_capsule(
        capsule,
        expected_dtype="f32",
        expected_shape=(2, 3),
        expected_device="metal:0",
    )
    assert imported.to_dict()["external_producer"] is True
    assert imported.imported.native_mtlbuffer_ptr == 0xCC0000
    assert imported.imported.buffer_handle.nbytes == 24
    assert pycapsule_name(capsule) == USED_DLPACK_CAPSULE_NAME

    fence = PccFenceToken()
    deferred = imported.deleter(fence)
    assert deferred.status == STATUS_METAL_DLPACK_RELEASE_DEFERRED
    assert deleter_calls == []
    pending = imported.reclaim_completed()
    assert pending.status == STATUS_METAL_DLPACK_RECLAIM_PENDING
    assert deleter_calls == []
    fence.complete()
    released = imported.reclaim_completed()
    assert released.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    assert deleter_calls == [ctypes.addressof(managed)]


def test_unconsumed_dlpack_capsule_destructor_releases_alias(tmp_path):
    fake = _FakeBufferRuntime()
    plan, allocations = _plan_and_allocations(tmp_path, fake)
    owner = MetalDlpackTensorOwner(allocations, plan)
    exported = export_metal_dlpack_py_capsule(owner.export("A"))
    pointer = exported.descriptor.native_mtlbuffer_ptr

    del exported
    gc.collect()
    reclaimed = owner.reclaim_completed()

    assert reclaimed.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
    assert ("release", pointer) in fake.calls
    allocations.release_all()
