"""Native MTLBuffer binding/runtime bridge tests for Kernel IR."""

import ctypes

import pytest

from pcc.kernel_ir.metal_buffer import (
    STATUS_NATIVE_BUFFER_ALLOCATIONS_READY,
    STATUS_NATIVE_BUFFER_DATA_ROUNDTRIP_VALIDATED,
    STATUS_NATIVE_BUFFER_RUNTIME_CALL_VALIDATED,
    STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED,
    STATUS_SKIPPED_WITH_REASON,
    MetalNativeBufferRuntimeError,
    allocate_metal_native_buffers_for_plan,
    build_metal_native_buffer_runtime_artifacts,
    emit_metal_native_buffer_runtime_source,
    roundtrip_metal_native_buffer_bytes,
    smoke_metal_native_buffer_runtime,
)
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, MemoryScope, ScalarType
from pcc.kernel_ir.metal_launch import plan_metal_launch


def _copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("src", "dst"), {"extent": 16}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("copy_mod", funcs=(func,))


def _copy_plan():
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    return plan_metal_launch(_copy_module(), args)


def test_native_buffer_runtime_source_allocates_and_releases_without_dispatch():
    source = emit_metal_native_buffer_runtime_source()

    assert "pcc_metal_buffer_runtime_create" in source
    assert "newBufferWithLength" in source
    assert "__bridge_retained void *" in source
    assert "pcc_metal_buffer_runtime_length" in source
    assert "pcc_metal_buffer_runtime_write" in source
    assert "pcc_metal_buffer_runtime_read" in source
    assert "memcpy" in source
    assert "pcc_metal_buffer_runtime_release" in source
    assert "__bridge_transfer id" in source
    assert "commandBuffer" not in source
    assert "dispatchThreadgroups" not in source
    assert "commit]" not in source


def test_native_buffer_runtime_artifacts_validate_symbols_with_injected_tools(tmp_path):
    def fake_compiler(output_path, *, source_path=None, timeout=30.0):
        assert source_path is not None
        output_path.write_bytes(b"fake native buffer runtime object")
        return output_path

    def fake_linker(output_path, *, object_path=None, timeout=30.0):
        assert object_path is not None
        assert object_path.is_file()
        output_path.write_bytes(b"fake native buffer runtime dylib")
        return output_path

    loaded_symbols = []

    def fake_loader(library_path, *, symbol):
        assert library_path.is_file()
        loaded_symbols.append(symbol)
        return symbol

    artifacts = build_metal_native_buffer_runtime_artifacts(
        tmp_path,
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
        compiler=fake_compiler,
        linker=fake_linker,
        loader=fake_loader,
    )
    data = artifacts.to_dict()

    assert artifacts.status == STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED
    assert artifacts.runtime_launch_executed is False
    assert artifacts.whole_program_gpu is False
    assert loaded_symbols == [
        "pcc_metal_buffer_runtime_create",
        "pcc_metal_buffer_runtime_length",
        "pcc_metal_buffer_runtime_release",
        "pcc_metal_buffer_runtime_write",
        "pcc_metal_buffer_runtime_read",
    ]
    assert data["validated_symbols"] == loaded_symbols
    assert (tmp_path / "pcc_metal_buffer_runtime.m").is_file()
    assert (tmp_path / "pcc_metal_buffer_runtime.o").read_bytes() == b"fake native buffer runtime object"
    assert (tmp_path / "pcc_metal_buffer_runtime.dylib").read_bytes() == b"fake native buffer runtime dylib"


class _FakeCFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


def test_native_buffer_runtime_smoke_creates_lengths_and_releases_with_fake_cdll(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    calls = []

    def create(nbytes, out_buffer):
        calls.append(("create", int(nbytes.value)))
        ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(0xABC000)
        return 0

    def length(buffer, out_nbytes):
        calls.append(("length", int(buffer.value)))
        ctypes.cast(out_nbytes, ctypes.POINTER(ctypes.c_uint64))[0] = ctypes.c_uint64(128)
        return 0

    def release(buffer):
        calls.append(("release", int(buffer.value)))
        return 0

    def should_not_transfer(*args):
        raise AssertionError("write/read should not run during create smoke")

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(create)
        pcc_metal_buffer_runtime_length = _FakeCFunction(length)
        pcc_metal_buffer_runtime_release = _FakeCFunction(release)
        pcc_metal_buffer_runtime_write = _FakeCFunction(should_not_transfer)
        pcc_metal_buffer_runtime_read = _FakeCFunction(should_not_transfer)

    result = smoke_metal_native_buffer_runtime(
        library_path,
        nbytes=128,
        cdll_factory=lambda path: FakeLibrary(),
    )
    data = result.to_dict()

    assert result.status == STATUS_NATIVE_BUFFER_RUNTIME_CALL_VALIDATED
    assert data["nbytes_requested"] == 128
    assert data["nbytes_reported"] == 128
    assert data["native_mtlbuffer_ptr"] == 0xABC000
    assert data["released"] is True
    assert data["runtime_launch_executed"] is False
    assert calls == [
        ("create", 128),
        ("length", 0xABC000),
        ("release", 0xABC000),
    ]


def test_native_buffer_runtime_smoke_reports_no_device_without_release(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    calls = []

    def create(nbytes, out_buffer):
        calls.append(("create", int(nbytes.value)))
        return 3

    def should_not_run(*args):
        raise AssertionError("length/release should not run when device is absent")

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(create)
        pcc_metal_buffer_runtime_length = _FakeCFunction(should_not_run)
        pcc_metal_buffer_runtime_release = _FakeCFunction(should_not_run)
        pcc_metal_buffer_runtime_write = _FakeCFunction(should_not_run)
        pcc_metal_buffer_runtime_read = _FakeCFunction(should_not_run)

    result = smoke_metal_native_buffer_runtime(
        library_path,
        cdll_factory=lambda path: FakeLibrary(),
    )

    assert result.status == STATUS_SKIPPED_WITH_REASON
    assert result.released is False
    assert result.runtime_launch_executed is False
    assert calls == [("create", 64)]


def test_allocate_native_buffers_for_plan_builds_binding_set_and_releases(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    next_ptr = 0xC00000
    lengths = {}
    calls = []

    def create(nbytes, out_buffer):
        nonlocal next_ptr
        ptr = next_ptr
        next_ptr += 0x1000
        lengths[ptr] = int(nbytes.value)
        calls.append(("create", int(nbytes.value), ptr))
        ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(ptr)
        return 0

    def length(buffer, out_nbytes):
        ptr = int(buffer.value)
        calls.append(("length", ptr))
        ctypes.cast(out_nbytes, ctypes.POINTER(ctypes.c_uint64))[0] = ctypes.c_uint64(lengths[ptr])
        return 0

    def release(buffer):
        calls.append(("release", int(buffer.value)))
        return 0

    def should_not_transfer(*args):
        raise AssertionError("write/read should not run during allocation")

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(create)
        pcc_metal_buffer_runtime_length = _FakeCFunction(length)
        pcc_metal_buffer_runtime_release = _FakeCFunction(release)
        pcc_metal_buffer_runtime_write = _FakeCFunction(should_not_transfer)
        pcc_metal_buffer_runtime_read = _FakeCFunction(should_not_transfer)

    allocation_set = allocate_metal_native_buffers_for_plan(
        library_path,
        _copy_plan(),
        cdll_factory=lambda path: FakeLibrary(),
    )
    data = allocation_set.to_dict()

    assert allocation_set.status == STATUS_NATIVE_BUFFER_ALLOCATIONS_READY
    assert allocation_set.runtime_launch_executed is False
    assert allocation_set.binding_set is not None
    assert allocation_set.binding_set.native_buffer_handles_ready is True
    assert data["binding_set"]["native_buffer_handles_ready"] is True
    assert [allocation["requested_nbytes"] for allocation in data["allocations"]] == [64, 64]
    assert [binding["native_mtlbuffer_bound"] for binding in data["binding_set"]["bindings"]] == [True, True]
    assert [binding["source"] for binding in data["binding_set"]["bindings"]] == [
        "pcc native Metal buffer runtime allocation",
        "pcc native Metal buffer runtime allocation",
    ]

    allocation_set.release_all()
    assert allocation_set.released is True
    assert [call[0] for call in calls] == [
        "create",
        "length",
        "create",
        "length",
        "release",
        "release",
    ]


def test_allocate_native_buffers_releases_created_buffer_when_length_fails(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    calls = []

    def create(nbytes, out_buffer):
        calls.append(("create", int(nbytes.value), 0xD00000))
        ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(0xD00000)
        return 0

    def length(buffer, out_nbytes):
        calls.append(("length", int(buffer.value)))
        return 9

    def release(buffer):
        calls.append(("release", int(buffer.value)))
        return 0

    def should_not_transfer(*args):
        raise AssertionError("write/read should not run during failed allocation")

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(create)
        pcc_metal_buffer_runtime_length = _FakeCFunction(length)
        pcc_metal_buffer_runtime_release = _FakeCFunction(release)
        pcc_metal_buffer_runtime_write = _FakeCFunction(should_not_transfer)
        pcc_metal_buffer_runtime_read = _FakeCFunction(should_not_transfer)

    with pytest.raises(MetalNativeBufferRuntimeError, match="length failed"):
        allocate_metal_native_buffers_for_plan(
            library_path,
            _copy_plan(),
            cdll_factory=lambda path: FakeLibrary(),
        )

    assert calls == [
        ("create", 64, 0xD00000),
        ("length", 0xD00000),
        ("release", 0xD00000),
    ]


def test_native_buffer_roundtrip_validates_host_byte_transfer_with_fake_cdll(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")
    ptr = 0xE00000
    storage = bytearray(b"\x00" * 32)
    calls = []

    def should_not_lifecycle(*args):
        raise AssertionError("create/length/release should not run during transfer")

    def write(buffer, offset, src, nbytes):
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        calls.append(("write", int(buffer.value), offset_i, nbytes_i))
        src_bytes = ctypes.string_at(src, nbytes_i)
        storage[offset_i : offset_i + nbytes_i] = src_bytes
        return 0

    def read(buffer, offset, dst, nbytes):
        offset_i = int(offset.value)
        nbytes_i = int(nbytes.value)
        calls.append(("read", int(buffer.value), offset_i, nbytes_i))
        ctypes.memmove(dst, bytes(storage[offset_i : offset_i + nbytes_i]), nbytes_i)
        return 0

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_length = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_release = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_write = _FakeCFunction(write)
        pcc_metal_buffer_runtime_read = _FakeCFunction(read)

    result = roundtrip_metal_native_buffer_bytes(
        library_path,
        ptr,
        b"pcc-metal",
        offset=4,
        cdll_factory=lambda path: FakeLibrary(),
    )
    data = result.to_dict()

    assert result.status == STATUS_NATIVE_BUFFER_DATA_ROUNDTRIP_VALIDATED
    assert result.data == b"pcc-metal"
    assert data["direction"] == "host_mtlbuffer_host"
    assert data["data_hex"] == b"pcc-metal".hex()
    assert data["runtime_launch_executed"] is False
    assert storage[4:13] == b"pcc-metal"
    assert calls == [
        ("write", ptr, 4, 9),
        ("read", ptr, 4, 9),
    ]


def test_native_buffer_roundtrip_reports_write_failure(tmp_path):
    library_path = tmp_path / "buffer_runtime.dylib"
    library_path.write_bytes(b"fake dylib")

    def should_not_lifecycle(*args):
        raise AssertionError("create/length/release should not run during transfer")

    def write(buffer, offset, src, nbytes):
        return 3

    def should_not_read(*args):
        raise AssertionError("read should not run after failed write")

    class FakeLibrary:
        pcc_metal_buffer_runtime_create = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_length = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_release = _FakeCFunction(should_not_lifecycle)
        pcc_metal_buffer_runtime_write = _FakeCFunction(write)
        pcc_metal_buffer_runtime_read = _FakeCFunction(should_not_read)

    with pytest.raises(MetalNativeBufferRuntimeError, match="host write failed"):
        roundtrip_metal_native_buffer_bytes(
            library_path,
            0xE10000,
            b"overflow",
            cdll_factory=lambda path: FakeLibrary(),
        )
