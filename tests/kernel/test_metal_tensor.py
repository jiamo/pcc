"""Typed matrix marshalling for Kernel IR Metal native buffers."""

import ctypes
import struct

import pytest

from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, MemoryScope, ScalarType
from pcc.kernel_ir.metal_buffer import allocate_metal_native_buffers_for_plan
from pcc.kernel_ir.metal_launch import plan_metal_launch
from pcc.kernel_ir.metal_tensor import (
    STATUS_METAL_MATRIX_BUFFERS_READY,
    STATUS_METAL_MATRIX_READBACK_VALIDATED,
    MetalTensorTransferError,
    pack_matrix_to_metal_bytes,
    read_metal_launch_matrix,
    unpack_matrix_from_metal_bytes,
    write_metal_launch_matrices,
)


class _FakeCFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


class _FakeBufferRuntime:
    def __init__(self):
        self.next_ptr = 0xF00000
        self.storage = {}
        self.calls = []
        self.pcc_metal_buffer_runtime_create = _FakeCFunction(self.create)
        self.pcc_metal_buffer_runtime_length = _FakeCFunction(self.length)
        self.pcc_metal_buffer_runtime_release = _FakeCFunction(self.release)
        self.pcc_metal_buffer_runtime_write = _FakeCFunction(self.write)
        self.pcc_metal_buffer_runtime_read = _FakeCFunction(self.read)

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

    def write(self, buffer, offset, src, nbytes):
        ptr = int(buffer.value)
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        self.calls.append(("write", ptr, offset_i, nbytes_i))
        if offset_i + nbytes_i > len(self.storage[ptr]):
            return 3
        self.storage[ptr][offset_i : offset_i + nbytes_i] = ctypes.string_at(src, nbytes_i)
        return 0

    def read(self, buffer, offset, dst, nbytes):
        ptr = int(buffer.value)
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        self.calls.append(("read", ptr, offset_i, nbytes_i))
        if offset_i + nbytes_i > len(self.storage[ptr]):
            return 3
        ctypes.memmove(dst, bytes(self.storage[ptr][offset_i : offset_i + nbytes_i]), nbytes_i)
        return 0


def _matrix_module() -> KernelModule:
    func = KernelFunc(
        name="matrix_copy_kernel",
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
    return KernelModule("matrix_copy_mod", funcs=(func,))


def _matrix_plan_and_allocations(tmp_path, fake):
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
    return library_path, plan, allocations


def test_matrix_pack_unpack_uses_row_major_launch_dtype():
    matrix = ((1.25, -2.5, 3.0), (4.5, 5.25, -6.75))
    payload = pack_matrix_to_metal_bytes(matrix, dtype="f32", shape=(2, 3), name="A")

    assert payload == struct.pack("<ffffff", 1.25, -2.5, 3.0, 4.5, 5.25, -6.75)
    assert unpack_matrix_from_metal_bytes(payload, dtype="f32", shape=(2, 3), name="A") == matrix


def test_write_and_read_launch_matrices_through_native_buffers(tmp_path):
    fake = _FakeBufferRuntime()
    library_path, plan, allocations = _matrix_plan_and_allocations(tmp_path, fake)
    matrix = ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
    try:
        transfer_set = write_metal_launch_matrices(
            library_path,
            allocations,
            plan,
            {"A": matrix},
            zero_fill_unprovided=True,
            cdll_factory=lambda path: fake,
        )
        a_read = read_metal_launch_matrix(
            library_path,
            allocations,
            plan,
            "A",
            cdll_factory=lambda path: fake,
        )
        c_read = read_metal_launch_matrix(
            library_path,
            allocations,
            plan,
            "C",
            cdll_factory=lambda path: fake,
        )
    finally:
        allocations.release_all()

    assert transfer_set.status == STATUS_METAL_MATRIX_BUFFERS_READY
    assert [transfer.name for transfer in transfer_set.transfers] == ["A", "C"]
    assert [transfer.zero_filled for transfer in transfer_set.transfers] == [False, True]
    assert a_read.status == STATUS_METAL_MATRIX_READBACK_VALIDATED
    assert a_read.matrix == matrix
    assert c_read.matrix == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert a_read.runtime_launch_executed is False
    assert c_read.runtime_launch_executed is False
    assert any(call[0] == "write" for call in fake.calls)
    assert any(call[0] == "read" for call in fake.calls)


def test_write_launch_matrices_rejects_wrong_shape(tmp_path):
    fake = _FakeBufferRuntime()
    library_path, plan, allocations = _matrix_plan_and_allocations(tmp_path, fake)
    try:
        with pytest.raises(MetalTensorTransferError, match="expected 2 rows"):
            write_metal_launch_matrices(
                library_path,
                allocations,
                plan,
                {"A": ((1.0, 2.0, 3.0),)},
            )
    finally:
        allocations.release_all()


def test_write_launch_matrices_rejects_released_allocations(tmp_path):
    fake = _FakeBufferRuntime()
    library_path, plan, allocations = _matrix_plan_and_allocations(tmp_path, fake)
    allocations.release_all()

    with pytest.raises(MetalTensorTransferError, match="already been released"):
        write_metal_launch_matrices(
            library_path,
            allocations,
            plan,
            {"A": ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))},
        )
