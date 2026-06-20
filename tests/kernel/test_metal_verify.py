"""CPU-oracle comparison for native Metal matrix readback."""

import ctypes

import pytest

from pcc.kernel_ir.cpu_reference import CpuReferenceResult
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, MemoryScope, ScalarType
from pcc.kernel_ir.metal_buffer import allocate_metal_native_buffers_for_plan
from pcc.kernel_ir.metal_launch import plan_metal_launch
from pcc.kernel_ir.metal_tensor import write_metal_launch_matrices
from pcc.kernel_ir.metal_verify import (
    STATUS_METAL_CPU_ORACLE_MATCH,
    MetalCpuOracleCompareError,
    verify_metal_launch_output_against_cpu_reference,
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
        self.next_ptr = 0xFA0000
        self.storage = {}
        self.pcc_metal_buffer_runtime_create = _FakeCFunction(self.create)
        self.pcc_metal_buffer_runtime_length = _FakeCFunction(self.length)
        self.pcc_metal_buffer_runtime_release = _FakeCFunction(self.release)
        self.pcc_metal_buffer_runtime_write = _FakeCFunction(self.write)
        self.pcc_metal_buffer_runtime_read = _FakeCFunction(self.read)

    def create(self, nbytes, out_buffer):
        ptr = self.next_ptr
        self.next_ptr += 0x1000
        self.storage[ptr] = bytearray(int(nbytes.value))
        ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(ptr)
        return 0

    def length(self, buffer, out_nbytes):
        ctypes.cast(out_nbytes, ctypes.POINTER(ctypes.c_uint64))[0] = ctypes.c_uint64(
            len(self.storage[int(buffer.value)])
        )
        return 0

    def release(self, buffer):
        self.storage.pop(int(buffer.value), None)
        return 0

    def write(self, buffer, offset, src, nbytes):
        ptr = int(buffer.value)
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        self.storage[ptr][offset_i : offset_i + nbytes_i] = ctypes.string_at(src, nbytes_i)
        return 0

    def read(self, buffer, offset, dst, nbytes):
        ptr = int(buffer.value)
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        ctypes.memmove(dst, bytes(self.storage[ptr][offset_i : offset_i + nbytes_i]), nbytes_i)
        return 0


def _module() -> KernelModule:
    func = KernelFunc(
        name="matrix_kernel",
        params=(
            BufferParam("A", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            BufferParam("C", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("A", "C"), {"extent": 4}),
            KernelOp("copy", ("A", "C")),
        ),
        grid=(1,),
        threads=32,
    )
    return KernelModule("matrix_mod", funcs=(func,))


def _plan_allocations_and_runtime(tmp_path):
    fake = _FakeBufferRuntime()
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    plan = plan_metal_launch(_module(), args)
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    allocations = allocate_metal_native_buffers_for_plan(
        library_path,
        plan,
        cdll_factory=lambda path: fake,
    )
    return library_path, plan, allocations, fake


def _cpu_reference(matrix):
    return CpuReferenceResult(
        entry="matrix_kernel",
        outputs={"C": matrix},
        tiles_executed=1,
        k_tiles=1,
    )


def test_native_readback_matches_cpu_oracle(tmp_path):
    library_path, plan, allocations, fake = _plan_allocations_and_runtime(tmp_path)
    expected = ((1.0, 2.0), (3.5, -4.25))
    try:
        write_metal_launch_matrices(
            library_path,
            allocations,
            plan,
            {"C": expected},
            cdll_factory=lambda path: fake,
        )
        result = verify_metal_launch_output_against_cpu_reference(
            library_path,
            allocations,
            plan,
            _cpu_reference(expected),
            output_name="C",
            cdll_factory=lambda path: fake,
        )
    finally:
        allocations.release_all()

    data = result.to_dict()
    assert result.status == STATUS_METAL_CPU_ORACLE_MATCH
    assert result.element_count == 4
    assert result.max_abs_error == 0.0
    assert data["readback"]["matrix"] == [[1.0, 2.0], [3.5, -4.25]]
    assert data["runtime_launch_executed"] is False
    assert data["whole_program_gpu"] is False


def test_native_readback_can_carry_completed_launch_claim(tmp_path):
    library_path, plan, allocations, fake = _plan_allocations_and_runtime(tmp_path)
    expected = ((1.0, 2.0), (3.0, 4.0))
    try:
        write_metal_launch_matrices(
            library_path,
            allocations,
            plan,
            {"C": expected},
            cdll_factory=lambda path: fake,
        )
        result = verify_metal_launch_output_against_cpu_reference(
            library_path,
            allocations,
            plan,
            _cpu_reference(expected),
            output_name="C",
            cdll_factory=lambda path: fake,
            runtime_launch_executed=True,
        )
    finally:
        allocations.release_all()

    data = result.to_dict()
    assert result.status == STATUS_METAL_CPU_ORACLE_MATCH
    assert data["runtime_launch_executed"] is True
    assert data["readback"]["runtime_launch_executed"] is True
    assert data["whole_program_gpu"] is False
    assert data["reason"] == "Native Metal launch output matches CPU oracle output."


def test_native_readback_reports_cpu_oracle_mismatch(tmp_path):
    library_path, plan, allocations, fake = _plan_allocations_and_runtime(tmp_path)
    try:
        write_metal_launch_matrices(
            library_path,
            allocations,
            plan,
            {"C": ((1.0, 2.0), (3.0, 4.0))},
            cdll_factory=lambda path: fake,
        )
        with pytest.raises(MetalCpuOracleCompareError, match=r"C\[1,1\] mismatch"):
            verify_metal_launch_output_against_cpu_reference(
                library_path,
                allocations,
                plan,
                _cpu_reference(((1.0, 2.0), (3.0, 9.0))),
                output_name="C",
                cdll_factory=lambda path: fake,
            )
    finally:
        allocations.release_all()


def test_native_readback_requires_named_output_when_oracle_has_many(tmp_path):
    library_path, plan, allocations, fake = _plan_allocations_and_runtime(tmp_path)
    cpu = CpuReferenceResult(
        entry="matrix_kernel",
        outputs={"C": ((0.0, 0.0), (0.0, 0.0)), "D": ((1.0,),)},
        tiles_executed=1,
        k_tiles=1,
    )
    try:
        with pytest.raises(MetalCpuOracleCompareError, match="choose output_name"):
            verify_metal_launch_output_against_cpu_reference(
                library_path,
                allocations,
                plan,
                cpu,
                cdll_factory=lambda path: fake,
            )
    finally:
        allocations.release_all()
