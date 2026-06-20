"""pcc1/no-libpython ownership of a classic kDLMetal DLPack capsule."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap

import pytest

from pcc.kernel_ir.metal_buffer import (
    STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED,
    STATUS_SKIPPED_WITH_REASON,
    build_metal_native_buffer_runtime_artifacts,
)
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarType,
)
from pcc.kernel_ir.metal_package import build_metal_kernel_package


REPO = Path(__file__).resolve().parents[2]


def _strict_hardware() -> bool:
    return os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1"


def _unavailable(reason: str) -> None:
    if _strict_hardware():
        pytest.fail(reason)
    pytest.skip(reason)


def _current_pcc1() -> Path:
    raw = os.environ.get("PCC_CURRENT_PCC1", "").strip()
    if not raw:
        _unavailable("PCC_CURRENT_PCC1 is required for the pcc1 DLPack gate")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    if not path.is_file() or not os.access(path, os.X_OK):
        _unavailable(f"pcc1 is not executable: {path}")
    return path


def _assert_no_libpython(path: Path) -> None:
    result = subprocess.run(
        ["otool", "-L", str(path)],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    lowered = "\n".join(result.stdout.splitlines()[1:]).lower()
    assert "libpython" not in lowered
    assert "python.framework" not in lowered


def _copy_module() -> KernelModule:
    func = KernelFunc(
        name="dlpack_copy",
        params=(
            BufferParam("src", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("src", "dst"), {"extent": 6}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=32,
    )
    return KernelModule("dlpack_copy_mod", funcs=(func,))


def _copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    return args


def run_pcc1_dlpack_capsule_gate(tmp_path: Path) -> None:
    pcc1 = _current_pcc1()
    _assert_no_libpython(pcc1)
    runtime = build_metal_native_buffer_runtime_artifacts(
        tmp_path / "native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
        timeout=90.0,
    )
    if runtime.status == STATUS_SKIPPED_WITH_REASON:
        _unavailable(runtime.reason)
    assert runtime.status == STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED
    assert runtime.library_path is not None
    package = build_metal_kernel_package(
        _copy_module(),
        _copy_args(),
        tmp_path / "metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        _unavailable(package.finalize.reason)
    assert package.finalize.metallib_produced is True
    assert package.bridge_library_load_validated is True
    assert package.bridge_library_path is not None
    assert package.bridge_library_symbol is not None
    assert package.launch_plan.metallib_path is not None

    source = tmp_path / "pcc1_dlpack_capsule.py"
    executable = tmp_path / "pcc1_dlpack_capsule"
    source.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64, c_void
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i32,
                load_i64,
                load_ptr,
                load_i8,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )
            metal_call = extern(
                "pcc_metal_metallib_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            resource_register = extern(
                "pcc_gc_external_metal_buffer_register",
                (c_ptr, c_uint64),
                c_uint64,
            )
            resource_mark_fence = extern(
                "pcc_gc_external_resource_mark_fence_complete",
                (c_uint64,),
                c_int64,
            )
            resource_poll = extern("pcc_gc_external_resource_poll", (), c_int64)
            resource_active = extern(
                "pcc_gc_external_resource_active_count", (), c_int64
            )
            resource_pending = extern(
                "pcc_gc_external_resource_pending_count", (), c_int64
            )
            resource_released = extern(
                "pcc_gc_external_resource_release_count", (), c_int64
            )
            packet_size = extern(
                "pcc_dlpack_buffer_handle_packet_size", (), c_int64
            )
            capsule_new = extern(
                "pcc_dlpack_metal_capsule_new",
                (c_uint64, c_uint64, c_int32, c_int32, c_int32, c_int64, c_ptr),
                c_ptr,
            )
            capsule_name_code = extern(
                "pcc_dlpack_capsule_name_code", (c_ptr,), c_int64
            )
            capsule_consume = extern(
                "pcc_dlpack_capsule_consume", (c_ptr, c_ptr, c_ptr), c_int64
            )
            managed_release = extern(
                "pcc_dlpack_managed_tensor_release", (c_ptr,), c_int64
            )
            py_decref = extern("py_decref", (c_ptr,), c_void)

            def main() -> None:
                runtime_path = cstr({str(runtime.library_path)!r})
                bridge_path = cstr({str(package.bridge_library_path)!r})
                bridge_symbol = cstr({str(package.bridge_library_symbol)!r})
                metallib_path = cstr({str(package.launch_plan.metallib_path)!r})
                backend_text = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(backend_text) != 0:
                    print(1)
                    return
                backend_code: int = load_i8(backend_text, 0) - 48
                if backend_code < 0 or backend_code > 4 or load_i8(backend_text, 1) != 0:
                    print(2)
                    return
                base_active: int = resource_active()
                base_pending: int = resource_pending()
                base_released: int = resource_released()
                out_buffer = malloc(8)
                out_dst = malloc(8)
                buffers = malloc(16)
                shape = malloc(16)
                payload = malloc(24)
                readback = malloc(24)
                out_managed = malloc(8)
                packet = malloc(packet_size())
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)
                store_i64(shape, 0, 2)
                store_i64(shape, 8, 3)
                store_i32(payload, 0, 1065353216)
                store_i32(payload, 4, 1073741824)
                store_i32(payload, 8, 1077936128)
                store_i32(payload, 12, 1082130432)
                store_i32(payload, 16, 1084227584)
                store_i32(payload, 20, 1086324736)

                rc: int = buffer_create(runtime_path, 24, out_buffer)
                if rc != 0:
                    print(10 + rc)
                    return
                native_handle: int = load_i64(out_buffer, 0)
                rc = buffer_create(runtime_path, 24, out_dst)
                if rc != 0:
                    print(15 + rc)
                    return
                dst_handle: int = load_i64(out_dst, 0)
                rc = buffer_write(runtime_path, native_handle, 0, payload, 24)
                if rc != 0:
                    print(20 + rc)
                    return
                resource_id: int = resource_register(runtime_path, native_handle)
                if resource_id == 0:
                    print(30)
                    return
                capsule = capsule_new(resource_id, native_handle, 2, 32, 1, 2, shape)
                if ptr_is_null(capsule) != 0:
                    print(31)
                    return
                if capsule_name_code(capsule) != 1:
                    print(32)
                    return
                if packet_size() != 120:
                    print(33)
                    return
                rc = capsule_consume(capsule, packet, out_managed)
                if rc != 0:
                    print(40 - rc)
                    return
                managed = load_ptr(out_managed, 0)
                if capsule_name_code(capsule) != 2:
                    print(41)
                    return
                if capsule_consume(capsule, packet, out_managed) != 2:
                    print(42)
                    return
                if load_i64(packet, 0) != native_handle:
                    print(43)
                    return
                if load_i64(packet, 8) != resource_id:
                    print(44)
                    return
                if load_i64(packet, 16) != 24:
                    print(45)
                    return
                if load_i64(packet, 24) != 2 or load_i64(packet, 32) != 3:
                    print(46)
                    return
                if load_i64(packet, 88) != 2:
                    print(47)
                    return
                if load_i32(packet, 96) != 8 or load_i32(packet, 100) != 0:
                    print(48)
                    return
                if load_i32(packet, 104) != 2 or load_i32(packet, 108) != 32:
                    print(49)
                    return
                if load_i32(packet, 112) != 1:
                    print(50)
                    return
                store_i64(buffers, 0, load_i64(packet, 0))
                store_i64(buffers, 8, dst_handle)
                rc = metal_call(
                    bridge_path,
                    bridge_symbol,
                    metallib_path,
                    buffers,
                    2,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc != 0:
                    print(70 + rc)
                    return
                rc = buffer_read(runtime_path, dst_handle, 0, readback, 24)
                if rc != 0:
                    print(75 + rc)
                    return
                if (
                    load_i32(readback, 0) != 1065353216
                    or load_i32(readback, 4) != 1073741824
                    or load_i32(readback, 8) != 1077936128
                    or load_i32(readback, 12) != 1082130432
                    or load_i32(readback, 16) != 1084227584
                    or load_i32(readback, 20) != 1086324736
                ):
                    print(80)
                    return
                if buffer_release(runtime_path, dst_handle) != 0:
                    print(81)
                    return
                if managed_release(managed) != 0:
                    print(51)
                    return
                if resource_active() != base_active + 1:
                    print(52)
                    return
                if resource_pending() != base_pending + 1:
                    print(53)
                    return
                if resource_poll() != 0:
                    print(54)
                    return
                rc = buffer_read(runtime_path, native_handle, 0, readback, 24)
                if rc != 0 or load_i32(readback, 20) != 1086324736:
                    print(55)
                    return
                py_decref(capsule)
                if resource_released() != base_released:
                    print(56)
                    return
                if resource_mark_fence(resource_id) != 0:
                    print(57)
                    return
                if resource_poll() != 1:
                    print(58)
                    return
                if resource_active() != base_active:
                    print(59)
                    return
                if resource_pending() != base_pending:
                    print(60)
                    return
                if resource_released() != base_released + 1:
                    print(61)
                    return

                free(packet)
                free(scalar_offsets)
                free(scalar_payload)
                free(out_managed)
                free(readback)
                free(payload)
                free(shape)
                free(buffers)
                free(out_dst)
                free(out_buffer)
                print(backend_code)
                print(0)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.setdefault("PCC_GC_BACKEND", "0")
    compiled = subprocess.run(
        [
            str(pcc1),
            str(source),
            "-o",
            str(executable),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    _assert_no_libpython(executable)
    ran = subprocess.run(
        [str(executable)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout == f"{env['PCC_GC_BACKEND']}\n0\n"


def test_pcc1_no_libpython_dlpack_capsule_reenters_packed_metal_handle(tmp_path):
    run_pcc1_dlpack_capsule_gate(tmp_path)
