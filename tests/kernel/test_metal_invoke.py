"""Strict Metal bridge invocation wrapper tests.

These tests validate the host-call ABI around ``MetalBridgeInvocationPacket``.
Injected CDLL calls are not GPU execution claims.
"""

from __future__ import annotations

import ctypes
from dataclasses import replace

import pytest

from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccFenceToken, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, MemoryScope, ScalarParam, ScalarType
from pcc.kernel_ir.metal_buffer import build_metal_native_buffer_binding_set
from pcc.kernel_ir.metal_invoke import (
    STATUS_BRIDGE_INVOCATION_ABI_VALIDATED,
    MetalBridgeInvocationError,
    invoke_metal_bridge_packet,
)
from pcc.kernel_ir.metal_package import (
    STATUS_BRIDGE_INVOCATION_PACKET_READY,
    build_metal_bridge_invocation_packet,
    build_metal_kernel_package,
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


def _copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=64, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 16)
    return args


def _fake_bridge_compiler(output_path, *, source_path=None, timeout=30.0):
    assert source_path is not None
    output_path.write_bytes(b"fake bridge object")
    return output_path


def _fake_bridge_linker(output_path, *, object_path=None, timeout=30.0):
    assert object_path is not None
    output_path.write_bytes(b"fake bridge dylib")
    return output_path


def _fake_bridge_loader(library_path, *, symbol):
    assert library_path.is_file()
    return symbol


class _FakeCFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


def _validated_package(tmp_path):
    return build_metal_kernel_package(
        _copy_module(),
        _copy_args(),
        tmp_path,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        bridge_compiler=_fake_bridge_compiler,
        bridge_linker=_fake_bridge_linker,
        bridge_loader=_fake_bridge_loader,
    )


def _ready_packet(tmp_path, *, wait_until_completed=True):
    package = _validated_package(tmp_path)
    metallib = tmp_path / "copy_kernel.metallib"
    metallib.write_bytes(b"fake metallib for ABI wrapper test")
    package = replace(
        package,
        launch_plan=replace(
            package.launch_plan,
            metallib_path=str(metallib),
            metallib_available=True,
        ),
    )
    native_ptrs = {
        arg.handle_id: 0xA00000 + ordinal * 0x1000
        for ordinal, arg in enumerate(
            arg for arg in package.launch_plan.args if arg.kind == "buffer"
        )
    }
    bindings = build_metal_native_buffer_binding_set(package.launch_plan, native_ptrs)
    return build_metal_bridge_invocation_packet(
        package,
        native_buffer_bindings=bindings,
        allow_missing_metallib=False,
        wait_until_completed=wait_until_completed,
    )


def test_bridge_invocation_refuses_not_invocable_packet(tmp_path):
    package = _validated_package(tmp_path)
    packet = build_metal_bridge_invocation_packet(package)

    assert packet.invocable is False
    with pytest.raises(MetalBridgeInvocationError, match="not invocable"):
        invoke_metal_bridge_packet(packet, fence=PccFenceToken())


def test_bridge_invocation_refuses_unmanaged_async_callback_lifetime(tmp_path):
    packet = _ready_packet(tmp_path, wait_until_completed=False)

    assert packet.status == STATUS_BRIDGE_INVOCATION_PACKET_READY
    with pytest.raises(MetalBridgeInvocationError, match="async Metal bridge callback"):
        invoke_metal_bridge_packet(packet, fence=PccFenceToken())


def test_bridge_invocation_fake_cdll_validates_abi_without_gpu_claim(tmp_path):
    packet = _ready_packet(tmp_path, wait_until_completed=True)
    calls = []

    def launch(
        metallib_path,
        buffer_handles,
        scalar_values,
        fence_complete,
        fence_ctx,
        wait_until_completed,
    ):
        scalar_n = ctypes.cast(scalar_values[0], ctypes.POINTER(ctypes.c_uint32))[0]
        calls.append(
            {
                "metallib_path": metallib_path.decode("utf-8"),
                "buffers": [int(buffer_handles[0]), int(buffer_handles[1])],
                "scalar_n": int(scalar_n),
                "wait": bool(wait_until_completed),
            }
        )
        fence_complete(fence_ctx)
        return 0

    class FakeLibrary:
        pass

    setattr(FakeLibrary, packet.symbol, _FakeCFunction(launch))
    fence = PccFenceToken()
    result = invoke_metal_bridge_packet(
        packet,
        fence=fence,
        cdll_factory=lambda path: FakeLibrary(),
    )
    data = result.to_dict()

    assert packet.status == STATUS_BRIDGE_INVOCATION_PACKET_READY
    assert result.status == STATUS_BRIDGE_INVOCATION_ABI_VALIDATED
    assert data["bridge_function_called"] is True
    assert data["injected_cdll_factory"] is True
    assert data["runtime_launch_executed"] is False
    assert data["whole_program_gpu"] is False
    assert fence.completed is True
    assert calls == [
        {
            "metallib_path": packet.metallib_path,
            "buffers": [0xA00000, 0xA01000],
            "scalar_n": 16,
            "wait": True,
        }
    ]
