"""Runtime-source Metal bridge tests.

This path intentionally does not produce or consume a ``.metallib``. It exists
to exercise the host/device command-buffer boundary through Metal's runtime
source compiler when the offline Metal toolchain is unavailable.
"""

from __future__ import annotations

import ctypes
import json
import struct
from dataclasses import replace

import pytest

from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccFenceToken, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_buffer import (
    STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED,
    MetalNativeBufferRuntimeArtifacts,
    build_metal_native_buffer_binding_set,
)
from pcc.kernel_ir.metal_launch import plan_metal_launch
from pcc.kernel_ir.metal_package import build_metal_kernel_package
from pcc.kernel_ir.metal_runtime_abi import (
    STATUS_METAL_SOURCE_RUNTIME_CALL_PLAN_READY,
    build_metal_source_runtime_call_plan,
)
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SOURCE_RUNTIME_BRIDGE_LOAD_VALIDATED,
    STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED,
    STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED,
    MetalSourceRuntimeBridgeArtifacts,
    MetalSourceRuntimeError,
    build_metal_source_runtime_bridge_artifacts,
    emit_metal_source_runtime_bridge_source,
    invoke_metal_source_runtime_bridge,
    metal_source_runtime_bridge_symbol,
    run_metal_source_runtime_prebuilt_package,
    run_metal_source_runtime_package,
    verify_metal_source_runtime_package_manifest,
    write_metal_source_runtime_package_manifest,
)


def _copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": 16}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("copy_mod", funcs=(func,))


def _matrix_copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("matrix_copy_mod", funcs=(func,))


def _copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 16)
    return args


def _matrix_copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 4)
    return args


def _mixed_scalar_module() -> KernelModule:
    func = KernelFunc(
        name="scalar_kernel",
        params=(
            ScalarParam("n", ScalarType.U32),
            ScalarParam("scale", ScalarType.F64),
            ScalarParam("enabled", ScalarType.BOOL),
        ),
        body=(KernelOp("parallel", ("n",), {"extent": 1}),),
        grid=(1,),
        threads=1,
    )
    return KernelModule("scalar_mod", funcs=(func,))


def _mixed_scalar_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_scalar("u32", 7)
    args.add_scalar("f64", 1.5)
    args.add_scalar("bool", True)
    return args


def _copy_plan():
    return plan_metal_launch(_copy_module(), _copy_args())


def _copy_bindings(plan):
    native_ptrs = {
        arg.handle_id: 0xB00000 + ordinal * 0x1000
        for ordinal, arg in enumerate(a for a in plan.args if a.kind == "buffer")
    }
    return build_metal_native_buffer_binding_set(plan, native_ptrs)


class _FakeCFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


class _FakeBufferRuntime:
    def __init__(self):
        self.next_ptr = 0xD00000
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


def _fake_artifact_compiler(output_path, *, source_path=None, timeout=30.0):
    assert source_path is not None
    output_path.write_bytes(b"fake object")
    return output_path


def _fake_artifact_linker(output_path, *, object_path=None, timeout=30.0):
    assert object_path is not None
    assert object_path.is_file()
    output_path.write_bytes(b"fake dylib")
    return output_path


def _fake_artifact_loader(library_path, *, symbol):
    assert library_path.is_file()
    return symbol


def _write_fake_artifact(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_runtime_source_bridge_uses_new_library_with_source_not_metallib():
    source = emit_metal_source_runtime_bridge_source(_copy_plan())

    assert "const char *metal_source" in source
    assert "metal_source_len" in source
    assert "newLibraryWithSource" in source
    assert "newLibraryWithURL" not in source
    assert "metallib_path" not in source
    assert "_copy_last_error" in source
    assert "localizedDescription" in source
    assert "newFunctionWithName:@\"copy_kernel\"" in source
    assert "[encoder setBuffer:pcc_buf_src offset:0 atIndex:0]" in source
    assert "[encoder setBuffer:pcc_buf_dst offset:0 atIndex:1]" in source
    assert "[encoder setBytes:pcc_scalar_n length:sizeof(uint32_t) atIndex:2]" in source
    assert "dispatchThreadgroups" in source
    assert "addCompletedHandler" in source
    assert "waitUntilCompleted" in source


def test_runtime_source_bridge_artifacts_validate_symbol_with_injected_tools(tmp_path):
    def fake_compiler(output_path, *, source_path=None, timeout=30.0):
        assert source_path is not None
        output_path.write_bytes(b"fake source runtime bridge object")
        return output_path

    def fake_linker(output_path, *, object_path=None, timeout=30.0):
        assert object_path is not None
        assert object_path.is_file()
        output_path.write_bytes(b"fake source runtime bridge dylib")
        return output_path

    loaded = []

    def fake_loader(library_path, *, symbol):
        assert library_path.is_file()
        loaded.append(symbol)
        return symbol

    artifacts = build_metal_source_runtime_bridge_artifacts(
        _copy_plan(),
        tmp_path,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        compiler=fake_compiler,
        linker=fake_linker,
        loader=fake_loader,
    )

    assert artifacts.status == STATUS_SOURCE_RUNTIME_BRIDGE_LOAD_VALIDATED
    assert artifacts.runtime_launch_executed is False
    assert artifacts.whole_program_gpu is False
    assert artifacts.validated_symbol == artifacts.symbol
    assert loaded == [artifacts.symbol]
    assert (tmp_path / "copy_kernel_metal_source_runtime_bridge.m").is_file()
    assert (tmp_path / "copy_kernel_metal_source_runtime_bridge.o").read_bytes() == (
        b"fake source runtime bridge object"
    )
    assert (tmp_path / "copy_kernel_metal_source_runtime_bridge.dylib").read_bytes() == (
        b"fake source runtime bridge dylib"
    )


def test_runtime_source_call_plan_is_pure_abi_without_ctypes(tmp_path):
    plan = _copy_plan()
    bindings = _copy_bindings(plan)
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")
    metal_source = "kernel void copy_kernel() {}"

    call_plan = build_metal_source_runtime_call_plan(
        launch_plan=plan,
        metal_source=metal_source,
        bridge_library_path=library_path,
        symbol="pcc_copy_runtime_source_bridge",
        native_buffer_bindings=bindings,
    )
    data = call_plan.to_dict()

    assert call_plan.status == STATUS_METAL_SOURCE_RUNTIME_CALL_PLAN_READY
    assert call_plan.source_bytes == metal_source.encode("utf-8")
    assert data["runtime_launch_executed"] is False
    assert data["whole_program_gpu"] is False
    assert data["fence_callback_required"] is False
    assert data["wait_until_completed"] is True
    assert data["buffer_slots"][0]["native_mtlbuffer_ptr"] == 0xB00000
    assert data["buffer_slots"][1]["native_mtlbuffer_ptr"] == 0xB01000
    assert data["scalar_slots"] == [
        {
            "name": "n",
            "kernel_index": 2,
            "dtype": "u32",
            "value": 16,
            "abi_offset": 0,
            "abi_nbytes": 4,
        }
    ]
    assert call_plan.scalar_payload == struct.pack("<I", 16)
    assert data["scalar_payload_nbytes"] == 4
    assert len(data["source_sha256"]) == 64
    assert len(data["scalar_payload_sha256"]) == 64


def test_runtime_source_call_plan_scalar_payload_is_c_aligned(tmp_path):
    plan = plan_metal_launch(_mixed_scalar_module(), _mixed_scalar_args())
    bindings = build_metal_native_buffer_binding_set(plan, {})
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")

    call_plan = build_metal_source_runtime_call_plan(
        launch_plan=plan,
        metal_source="kernel void scalar_kernel() {}",
        bridge_library_path=library_path,
        symbol="pcc_scalar_runtime_source_bridge",
        native_buffer_bindings=bindings,
    )
    data = call_plan.to_dict()

    assert data["buffer_slots"] == []
    assert data["scalar_payload_nbytes"] == 17
    assert call_plan.scalar_payload == (
        struct.pack("<I", 7)
        + b"\x00\x00\x00\x00"
        + struct.pack("<d", 1.5)
        + struct.pack("<?", True)
    )
    assert data["scalar_slots"] == [
        {
            "name": "n",
            "kernel_index": 0,
            "dtype": "u32",
            "value": 7,
            "abi_offset": 0,
            "abi_nbytes": 4,
        },
        {
            "name": "scale",
            "kernel_index": 1,
            "dtype": "f64",
            "value": 1.5,
            "abi_offset": 8,
            "abi_nbytes": 8,
        },
        {
            "name": "enabled",
            "kernel_index": 2,
            "dtype": "bool",
            "value": True,
            "abi_offset": 16,
            "abi_nbytes": 1,
        },
    ]


def test_runtime_source_invocation_fake_cdll_validates_abi_without_gpu_claim(tmp_path):
    plan = _copy_plan()
    bindings = _copy_bindings(plan)
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")
    metal_source = "kernel void copy_kernel() {}"
    calls = []

    def launch(
        source_bytes,
        source_len,
        buffer_handles,
        scalar_values,
        fence_complete,
        fence_ctx,
        wait_until_completed,
    ):
        scalar_n = ctypes.cast(scalar_values[0], ctypes.POINTER(ctypes.c_uint32))[0]
        calls.append(
            {
                "source": source_bytes[: int(source_len.value)].decode("utf-8"),
                "buffers": [int(buffer_handles[0]), int(buffer_handles[1])],
                "scalar_n": int(scalar_n),
                "fence_complete": getattr(fence_complete, "value", fence_complete),
                "fence_ctx": getattr(fence_ctx, "value", fence_ctx),
                "wait": bool(wait_until_completed),
            }
        )
        return 0

    class FakeLibrary:
        pass

    symbol = "pcc_copy_runtime_source_bridge"
    setattr(FakeLibrary, symbol, _FakeCFunction(launch))
    fence = PccFenceToken()

    result = invoke_metal_source_runtime_bridge(
        plan=plan,
        metal_source=metal_source,
        bridge_library_path=library_path,
        symbol=symbol,
        native_buffer_bindings=bindings,
        fence=fence,
        cdll_factory=lambda path: FakeLibrary(),
    )
    data = result.to_dict()

    assert result.status == STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED
    assert data["bridge_function_called"] is True
    assert data["injected_cdll_factory"] is True
    assert data["runtime_launch_executed"] is False
    assert data["runtime_source_compiled"] is False
    assert data["whole_program_gpu"] is False
    assert fence.completed is True
    assert calls == [
        {
            "source": metal_source,
            "buffers": [0xB00000, 0xB01000],
            "scalar_n": 16,
            "fence_complete": None,
            "fence_ctx": None,
            "wait": True,
        }
    ]


def test_runtime_source_invocation_refuses_empty_source(tmp_path):
    plan = _copy_plan()
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")

    with pytest.raises(MetalSourceRuntimeError, match="non-empty source"):
        invoke_metal_source_runtime_bridge(
            plan=plan,
            metal_source="",
            bridge_library_path=library_path,
            symbol="unused",
            native_buffer_bindings=_copy_bindings(plan),
            fence=PccFenceToken(),
            cdll_factory=lambda path: object(),
        )


def test_runtime_source_invocation_refuses_unmanaged_async_callback_lifetime(tmp_path):
    plan = _copy_plan()
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")

    with pytest.raises(MetalSourceRuntimeError, match="async runtime-source callback"):
        invoke_metal_source_runtime_bridge(
            plan=plan,
            metal_source="kernel void copy_kernel() {}",
            bridge_library_path=library_path,
            symbol="unused",
            native_buffer_bindings=_copy_bindings(plan),
            fence=PccFenceToken(),
            wait_until_completed=False,
            cdll_factory=lambda path: object(),
        )


def test_runtime_source_invocation_refuses_mismatched_native_bindings(tmp_path):
    plan = _copy_plan()
    library_path = tmp_path / "source_runtime_bridge.dylib"
    library_path.write_bytes(b"fake dylib")
    bindings = _copy_bindings(plan)
    bad_bindings = replace(
        bindings,
        bindings=(replace(bindings.bindings[0], name="wrong_src"), bindings.bindings[1]),
    )

    with pytest.raises(MetalSourceRuntimeError, match="do not match launch plan"):
        invoke_metal_source_runtime_bridge(
            plan=plan,
            metal_source="kernel void copy_kernel() {}",
            bridge_library_path=library_path,
            symbol="unused",
            native_buffer_bindings=bad_bindings,
            fence=PccFenceToken(),
            cdll_factory=lambda path: object(),
        )


def test_runtime_source_package_api_validates_fake_abi_without_execution_claim(tmp_path):
    fake_buffer_runtime = _FakeBufferRuntime()
    bridge_calls = []

    def fake_launch(
        source_bytes,
        source_len,
        buffer_handles,
        scalar_values,
        fence_complete,
        fence_ctx,
        wait_until_completed,
    ):
        bridge_calls.append(
            {
                "source": source_bytes[: int(source_len.value)].decode("utf-8"),
                "buffers": [int(buffer_handles[0]), int(buffer_handles[1])],
                "fence_complete": getattr(fence_complete, "value", fence_complete),
                "fence_ctx": getattr(fence_ctx, "value", fence_ctx),
                "wait": bool(wait_until_completed),
            }
        )
        return 0

    class FakeBridgeLibrary:
        def __getattr__(self, _name):
            return _FakeCFunction(fake_launch)

    result = run_metal_source_runtime_package(
        _matrix_copy_module(),
        _matrix_copy_args(),
        tmp_path,
        metal_source="kernel void copy_kernel() {}",
        input_matrices={"src": ((1.0, 2.0), (3.0, 4.0))},
        cpu_reference=None,
        output_name="dst",
        native_buffer_compiler=_fake_artifact_compiler,
        native_buffer_linker=_fake_artifact_linker,
        native_buffer_loader=_fake_artifact_loader,
        source_bridge_compiler=_fake_artifact_compiler,
        source_bridge_linker=_fake_artifact_linker,
        source_bridge_loader=_fake_artifact_loader,
        buffer_cdll_factory=lambda path: fake_buffer_runtime,
        bridge_cdll_factory=lambda path: FakeBridgeLibrary(),
    )
    data = result.to_dict()

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED
    assert result.runtime_launch_executed is False
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert data["package_status"] == "metal_kernel_package_artifacts"
    assert data["source_bridge"]["status"] == STATUS_SOURCE_RUNTIME_BRIDGE_LOAD_VALIDATED
    assert data["matrix_write"]["status"] == "metal_matrix_buffers_ready"
    assert data["invocation"]["status"] == STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED
    assert data["cpu_comparison"] is None
    assert data["allocation_snapshot"]["released"] is True
    assert "no GPU execution" in result.reason
    assert bridge_calls == [
        {
            "source": "kernel void copy_kernel() {}",
            "buffers": [0xD00000, 0xD01000],
            "fence_complete": None,
            "fence_ctx": None,
            "wait": True,
        }
    ]

    manifest_path = write_metal_source_runtime_package_manifest(result)
    manifest = verify_metal_source_runtime_package_manifest(manifest_path)
    assert manifest_path == tmp_path / "metal_source_runtime_package_manifest.json"
    assert manifest["status"] == STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED
    assert manifest["runtime_launch_executed"] is False
    assert manifest["runtime_source_compiled"] is False
    assert manifest["whole_program_gpu"] is False
    assert set(manifest["artifacts"]) == {
        "finalize.metal_source",
        "native_buffer_runtime.library",
        "native_buffer_runtime.object",
        "native_buffer_runtime.source",
        "source_bridge.library",
        "source_bridge.object",
        "source_bridge.source",
    }
    for record in manifest["artifacts"].values():
        assert len(record["sha256"]) == 64
        assert record["nbytes"] > 0

    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["runtime_launch_executed"] = True
    tampered["result"]["runtime_launch_executed"] = True
    manifest_path.write_text(json.dumps(tampered, sort_keys=True), encoding="utf-8")
    with pytest.raises(MetalSourceRuntimeError, match="claims launch"):
        verify_metal_source_runtime_package_manifest(manifest_path)


def test_runtime_source_prebuilt_package_runs_without_build_toolchain(tmp_path):
    fake_buffer_runtime = _FakeBufferRuntime()
    bridge_calls = []
    package = build_metal_kernel_package(
        _matrix_copy_module(),
        _matrix_copy_args(),
        tmp_path / "package",
        compile_bridge=False,
    )
    native_runtime = MetalNativeBufferRuntimeArtifacts(
        status=STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED,
        source_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "buffer.m", b"buffer source")),
        source="/* prebuilt buffer runtime */",
        object_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "buffer.o", b"buffer object")),
        library_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "buffer.dylib", b"buffer dylib")),
        validated_symbols=(
            "pcc_metal_buffer_runtime_create",
            "pcc_metal_buffer_runtime_length",
            "pcc_metal_buffer_runtime_release",
            "pcc_metal_buffer_runtime_write",
            "pcc_metal_buffer_runtime_read",
        ),
    )
    symbol = metal_source_runtime_bridge_symbol(package.launch_plan)
    source_bridge = MetalSourceRuntimeBridgeArtifacts(
        status=STATUS_SOURCE_RUNTIME_BRIDGE_LOAD_VALIDATED,
        symbol=symbol,
        source_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "source_bridge.m", b"bridge source")),
        source="/* prebuilt source bridge */",
        object_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "source_bridge.o", b"bridge object")),
        library_path=str(_write_fake_artifact(tmp_path / "prebuilt" / "source_bridge.dylib", b"bridge dylib")),
        validated_symbol=symbol,
    )

    def fake_launch(
        source_bytes,
        source_len,
        buffer_handles,
        scalar_values,
        fence_complete,
        fence_ctx,
        wait_until_completed,
    ):
        bridge_calls.append(
            {
                "source": source_bytes[: int(source_len.value)].decode("utf-8"),
                "buffers": [int(buffer_handles[0]), int(buffer_handles[1])],
                "fence_complete": getattr(fence_complete, "value", fence_complete),
                "fence_ctx": getattr(fence_ctx, "value", fence_ctx),
                "wait": bool(wait_until_completed),
            }
        )
        return 0

    class FakeBridgeLibrary:
        def __getattr__(self, name):
            if name == symbol:
                return _FakeCFunction(fake_launch)
            raise AttributeError(name)

    result = run_metal_source_runtime_prebuilt_package(
        package,
        native_runtime,
        source_bridge,
        metal_source="kernel void copy_kernel() {}",
        input_matrices={"src": ((1.0, 2.0), (3.0, 4.0))},
        cpu_reference=None,
        output_name="dst",
        buffer_cdll_factory=lambda path: fake_buffer_runtime,
        bridge_cdll_factory=lambda path: FakeBridgeLibrary(),
    )
    data = result.to_dict()

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED
    assert result.runtime_launch_executed is False
    assert result.runtime_source_compiled is False
    assert result.allocations_released is True
    assert data["native_buffer_runtime"]["library_path"] == native_runtime.library_path
    assert data["source_bridge"]["library_path"] == source_bridge.library_path
    assert data["invocation"]["status"] == STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED
    assert bridge_calls == [
        {
            "source": "kernel void copy_kernel() {}",
            "buffers": [0xD00000, 0xD01000],
            "fence_complete": None,
            "fence_ctx": None,
            "wait": True,
        }
    ]
