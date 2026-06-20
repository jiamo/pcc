"""Level-6 five-GC Metal lifetime gate.

Level 6 is not "run the host Python Metal harness five times with different
environment labels". It requires the same GPU workload and native-resource
lifetime boundary to pass under PCC_GC_BACKEND=0..4 from the pcc runtime path.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.gpu_hardware import test_metal_pcc1_launch_real as pcc1_metal

from pcc.kernel_ir.gpu_claims import (
    GpuClaimError,
    GpuClaimLevel,
    STATUS_GPU_5GC_LIFETIME_EXECUTED,
    STATUS_PCC1_METAL_LAUNCHER_EXECUTED,
    classify_five_gc_gpu_lifetime_result,
    require_five_gc_parity_or_skip,
)
from pcc.kernel_ir.gpu_owner_backend import (
    GPU_BACKEND_PCC_METAL,
    GPU_BACKEND_TVM_TILELANG,
    pcc_metal_owner_identity_fields,
    tvm_tilelang_owner_identity_fields,
    validate_gpu_owner_identity,
)
from pcc.kernel_ir.tvm_tilelang_owner import (
    TVM_TILELANG_PIPELINE,
    TVM_TILELANG_PROVIDER_IDENTITY,
    load_tvm_tilelang_provider_config,
)
from pcc.kernel_ir.metal_invoke import STATUS_BRIDGE_INVOKED
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_INVOKED,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
)


REPO = Path(__file__).resolve().parents[2]


def _level4_backend_record(
    backend: int,
    *,
    workload_id: str = "tilelang_scalar_gemm_runtime_source",
    env_label_only: bool = False,
) -> dict[str, object]:
    return {
        **pcc_metal_owner_identity_fields(launcher_links_libpython=False),
        "status": STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
        "gc_backend": backend,
        "pcc_gc_backend_env": backend,
        "workload_id": workload_id,
        "env_label_only": env_label_only,
        "runtime_launch_executed": True,
        "runtime_source_compiled": True,
        "whole_program_gpu": False,
        "finalize": {"metallib_produced": False},
        "invocation": {
            "status": STATUS_SOURCE_RUNTIME_INVOKED,
            "fence_completed": True,
        },
        "cpu_comparison": {"status": "metal_cpu_oracle_match"},
    }


def _level5_lifetime_backend_record(
    backend: int,
    *,
    workload_id: str = "tilelang_scalar_gemm_runtime_source",
) -> dict[str, object]:
    return {
        **_level4_backend_record(backend, workload_id=workload_id),
        "status": STATUS_PCC1_METAL_LAUNCHER_EXECUTED,
        "pcc1_native_executed": True,
        "pcc1_no_libpython": True,
        "same_launcher_path": True,
        "pcc1_returncode": 0,
        "native_release_before_fence": False,
        "native_release_after_fence": True,
    }


def _level5_metallib_lifetime_backend_record(
    backend: int,
    *,
    workload_id: str = "simdgroup_gemm_8x8_metallib",
) -> dict[str, object]:
    return {
        "status": STATUS_PCC1_METAL_LAUNCHER_EXECUTED,
        "gc_backend": backend,
        "pcc_gc_backend_env": backend,
        "workload_id": workload_id,
        "runtime_launch_executed": True,
        "runtime_source_compiled": False,
        "whole_program_gpu": False,
        "finalize": {"metallib_produced": True},
        "invocation": {
            "status": STATUS_BRIDGE_INVOKED,
            "fence_completed": True,
        },
        "cpu_comparison": {"status": "metal_cpu_oracle_match"},
        "pcc1_native_executed": True,
        "pcc1_no_libpython": True,
        "same_launcher_path": True,
        "pcc1_returncode": 0,
        "native_release_before_fence": False,
        "native_release_after_fence": True,
    }


def _find_current_no_libpython_pcc1() -> Path:
    pcc1 = pcc1_metal._find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail(
            "PCC_RUN_GPU_5GC_LIFETIME=1 requested a real Level-6 run, but no "
            "fresh current pcc1 binary was found; set PCC_CURRENT_PCC1 or rebuild pcc1"
        )
    assert not pcc1_metal._links_libpython(pcc1), f"pcc1 links libpython: {pcc1}"
    return pcc1


def _build_pcc1_three_buffer_lifetime_probe(
    tmp_path: Path,
    *,
    package,
    native_runtime,
    source_bridge,
    metal_source: str,
    a,
    b,
    expected,
    a_nbytes: int,
    b_nbytes: int,
    c_nbytes: int,
    first_error_code: int,
    src_stem: str,
    failure_label: str,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=first_error_code,
    )

    src = tmp_path / f"{src_stem}.py"
    exe = tmp_path / src_stem
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc({a_nbytes})
                b_payload = malloc({b_nbytes})
                readback = malloc({c_nbytes})
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, {a_nbytes}, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, {b_nbytes}, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, {c_nbytes}, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, {a_nbytes})
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, {b_nbytes})
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, {c_nbytes})
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        f"pcc1 compile failed for five-GC {failure_label} lifetime probe "
        f"(exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _pcc1_store_f16_matrix_maybe_byte_lines(
    ptr_name: str,
    matrix,
    *,
    indent: str,
) -> str:
    flat = [value for row in matrix for value in row]
    if len(flat) % 2 == 0:
        return pcc1_metal._pcc1_store_f16_matrix_lines(
            ptr_name,
            matrix,
            indent=indent,
        )
    return pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        ptr_name,
        matrix,
        indent=indent,
    )


def _build_pcc1_three_buffer_metallib_lifetime_probe(
    tmp_path: Path,
    *,
    package,
    native_runtime,
    a,
    b,
    expected,
    a_nbytes: int,
    b_nbytes: int,
    c_nbytes: int,
    c_dtype: str = "f32",
    c_zero_nbytes: int | None = None,
    first_error_code: int,
    src_stem: str,
    failure_label: str,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    assert package.finalize.metallib_produced is True
    assert package.runtime_launch_executed is False
    if package.bridge_library_path is None:
        pytest.fail(f"{failure_label}: package did not produce a bridge dylib")
    if package.bridge_library_symbol is None:
        pytest.fail(f"{failure_label}: package did not record a bridge symbol")
    if package.launch_plan.metallib_path is None:
        pytest.fail(f"{failure_label}: package did not record a metallib path")

    a_store_lines = _pcc1_store_f16_matrix_maybe_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = _pcc1_store_f16_matrix_maybe_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    if c_dtype == "f32":
        c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
            "readback",
            expected,
            indent="                ",
            first_error_code=first_error_code,
        )
    elif c_dtype == "f16":
        c_assert_lines = pcc1_metal._pcc1_assert_f16_matrix_byte_lines(
            "readback",
            expected,
            indent="                ",
            first_error_code=first_error_code,
        )
    else:
        raise AssertionError(f"unsupported pcc1 C matrix dtype {c_dtype!r}")
    if c_zero_nbytes is not None:
        c_payload_decl = f"                c_payload = malloc({c_zero_nbytes})"
        c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
            "c_payload",
            c_zero_nbytes,
            indent="                ",
        )
        c_write_block = "\n".join(
            (
                f"                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, {c_zero_nbytes})",
                "                if rc != 0:",
                "                    print(rc)",
                "                    return",
            )
        )
        c_free_line = "                free(c_payload)"
    else:
        c_payload_decl = ""
        c_zero_lines = ""
        c_write_block = ""
        c_free_line = ""

    src = tmp_path / f"{src_stem}.py"
    exe = tmp_path / src_stem
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_metallib_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                bridge_runtime = cstr({json.dumps(str(package.bridge_library_path))})
                symbol = cstr({json.dumps(str(package.bridge_library_symbol))})
                metallib_path = cstr({json.dumps(str(package.launch_plan.metallib_path))})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc({a_nbytes})
                b_payload = malloc({b_nbytes})
{c_payload_decl}
                readback = malloc({c_nbytes})
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, {a_nbytes}, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, {b_nbytes}, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, {c_nbytes}, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, {a_nbytes})
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, {b_nbytes})
                if rc != 0:
                    print(rc)
                    return
{c_write_block}

                rc = pcc_metal_call(
                    bridge_runtime,
                    symbol,
                    metallib_path,
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, {c_nbytes})
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
{c_free_line}
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        f"pcc1 compile failed for five-GC {failure_label} metallib lifetime probe "
        f"(exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_gemm_metallib_lifetime_probe(tmp_path: Path) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_gemm_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=128,
        b_nbytes=128,
        c_nbytes=256,
        first_error_code=1040,
        src_stem="pcc1_5gc_simdgroup_gemm_metallib",
        failure_label="simdgroup GEMM",
    )


def _build_pcc1_simdgroup_two_n_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_two_n_gemm_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=128,
        b_nbytes=256,
        c_nbytes=512,
        first_error_code=11000,
        src_stem="pcc1_5gc_simdgroup_two_n_gemm_metallib",
        failure_label="two-N simdgroup GEMM",
    )


def _build_pcc1_simdgroup_two_m_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_two_m_gemm_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=256,
        b_nbytes=128,
        c_nbytes=512,
        first_error_code=11200,
        src_stem="pcc1_5gc_simdgroup_two_m_gemm_metallib",
        failure_label="two-M simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_gemm_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=256,
        b_nbytes=256,
        c_nbytes=1024,
        first_error_code=11400,
        src_stem="pcc1_5gc_simdgroup_four_2d_gemm_metallib",
        failure_label="four-2D simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=270,
        b_nbytes=270,
        c_nbytes=900,
        first_error_code=11800,
        src_stem="pcc1_5gc_simdgroup_four_2d_edge_tail_gemm_metallib",
        failure_label="four-2D edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_transpose_ab_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_transpose_ab_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=256,
        b_nbytes=256,
        c_nbytes=1024,
        first_error_code=11600,
        src_stem="pcc1_5gc_simdgroup_four_2d_transpose_ab_gemm_metallib",
        failure_label="four-2D transpose_AB simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_transpose_ab_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_transpose_ab_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=270,
        b_nbytes=270,
        c_nbytes=900,
        first_error_code=12000,
        src_stem="pcc1_5gc_simdgroup_four_2d_transpose_ab_edge_tail_gemm_metallib",
        failure_label="four-2D transpose_AB edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_splitk_atomic_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_splitk_atomic_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=512,
        b_nbytes=512,
        c_nbytes=1024,
        c_zero_nbytes=1024,
        first_error_code=12200,
        src_stem="pcc1_5gc_simdgroup_four_2d_splitk_atomic_gemm_metallib",
        failure_label="four-2D split-K atomic simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=510,
        b_nbytes=510,
        c_nbytes=900,
        c_zero_nbytes=900,
        first_error_code=12400,
        src_stem="pcc1_5gc_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_metallib",
        failure_label="four-2D split-K atomic edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=510,
        b_nbytes=510,
        c_nbytes=900,
        c_zero_nbytes=900,
        first_error_code=12600,
        src_stem="pcc1_5gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_metallib",
        failure_label="four-2D transpose_AB split-K atomic edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=510,
        b_nbytes=1054,
        c_nbytes=1860,
        c_zero_nbytes=1860,
        first_error_code=12800,
        src_stem="pcc1_5gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_metallib",
        failure_label="eight-N transpose_AB split-K atomic edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=1054,
        b_nbytes=1054,
        c_nbytes=3844,
        c_zero_nbytes=3844,
        first_error_code=13000,
        src_stem="pcc1_5gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_metallib",
        failure_label="sixteen transpose_AB split-K atomic edge/tail simdgroup GEMM",
    )


def _build_pcc1_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=1054,
        b_nbytes=2142,
        c_nbytes=7812,
        c_zero_nbytes=7812,
        first_error_code=13200,
        src_stem="pcc1_5gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_metallib",
        failure_label="thirty-two transpose_AB split-K atomic edge/tail simdgroup GEMM",
    )


def _build_pcc1_tilelang_gemm_metallib_lifetime_probe(tmp_path: Path) -> Path:
    package, native_runtime = pcc1_metal._build_real_metallib_tilelang_gemm_artifacts(
        tmp_path,
    )
    module = pcc1_metal._tilelang_gemm_module()
    a = ((1.0, 2.0), (3.0, 4.0))
    b = ((5.0, 6.0), (7.0, 8.0))
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=8,
        b_nbytes=8,
        c_nbytes=16,
        first_error_code=960,
        src_stem="pcc1_5gc_tilelang_gemm_metallib",
        failure_label="TileLang GEMM",
    )


def _build_pcc1_tilelang_local_metal_benchmark_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_local_metal_benchmark_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=3 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=20900,
        src_stem="pcc1_5gc_local_tilelang_metal_benchmark_metallib",
        failure_label="local TileLang Metal benchmark metallib",
    )


def _build_pcc1_tilelang_local_matmul_nonroller_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_local_matmul_nonroller_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 2,
        c_dtype="f16",
        first_error_code=21000,
        src_stem="pcc1_5gc_local_tilelang_matmul_nonroller_metallib",
        failure_label="local TileLang matmul non-roller metallib",
    )


def _build_pcc1_tilelang_local_matmul_static_roller_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_local_matmul_static_roller_artifacts(
            tmp_path,
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 2,
        c_dtype="f16",
        first_error_code=21100,
        src_stem="pcc1_5gc_local_tilelang_matmul_static_roller_metallib",
        failure_label="local TileLang matmul static roller-config metallib",
    )


def _build_real_metallib_tilelang_transpose_a_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_transpose_a_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_transpose_a_args(),
        tmp_path / "real_pcc1_tilelang_transpose_a_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang transpose_A GEMM metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail("TileLang transpose_A metallib package did not retain source")
    if "A_shared[((kk * 8u) + local_m)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_A metallib source lost transposed A use")
    if "B_shared[((kk * 8u) + local_n)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_A metallib source lost row-major B use")
    if "A[(a_row * 5u) + a_col]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_A metallib source lost A(K,M) load")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang transpose_A GEMM metallib bridge was not load-validated: "
            f"{package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang transpose_A GEMM metallib bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang transpose_A GEMM launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path / "real_pcc1_tilelang_transpose_a_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_transpose_a_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_transpose_a_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_transpose_a_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=3 * 5 * 2,
        b_nbytes=3 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=9400,
        src_stem="pcc1_5gc_tilelang_transpose_a_metallib",
        failure_label="TileLang transpose_A GEMM",
    )


def _build_real_metallib_tilelang_transpose_b_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_transpose_b_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_transpose_b_args(),
        tmp_path / "real_pcc1_tilelang_transpose_b_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang transpose_B GEMM metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail("TileLang transpose_B metallib package did not retain source")
    if "A_shared[((local_m * 8u) + kk)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_B metallib source lost row-major A use")
    if "B_shared[((local_n * 8u) + kk)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_B metallib source lost transposed B use")
    if "B[(b_row * 3u) + b_col]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_B metallib source lost B(N,K) load")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang transpose_B GEMM metallib bridge was not load-validated: "
            f"{package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang transpose_B GEMM metallib bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang transpose_B GEMM launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path / "real_pcc1_tilelang_transpose_b_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_transpose_b_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_transpose_b_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_transpose_b_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=9500,
        src_stem="pcc1_5gc_tilelang_transpose_b_metallib",
        failure_label="TileLang transpose_B GEMM",
    )


def _build_real_metallib_tilelang_transpose_ab_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_transpose_ab_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_transpose_ab_args(),
        tmp_path / "real_pcc1_tilelang_transpose_ab_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang transpose_AB GEMM metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail("TileLang transpose_AB metallib package did not retain source")
    if "A_shared[((kk * 8u) + local_m)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_AB metallib source lost transposed A indexing")
    if "B_shared[((local_n * 8u) + kk)]" not in package.finalize.metal_source:
        pytest.fail("TileLang transpose_AB metallib source lost transposed B indexing")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang transpose_AB GEMM metallib bridge was not load-validated: "
            f"{package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang transpose_AB GEMM metallib bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang transpose_AB GEMM launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path / "real_pcc1_tilelang_transpose_ab_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_transpose_ab_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_transpose_ab_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_transpose_ab_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=3 * 5 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=9600,
        src_stem="pcc1_5gc_tilelang_transpose_ab_metallib",
        failure_label="TileLang transpose_AB GEMM",
    )


def _build_real_metallib_tilelang_splitk_atomic_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_splitk_atomic_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_splitk_atomic_args(),
        tmp_path / "real_pcc1_tilelang_splitk_atomic_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang split-K atomic GEMM metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail("TileLang split-K atomic metallib package did not retain source")
    if "device atomic_float* C [[buffer(2)]]" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K metallib source lost atomic output pointer")
    if "uint split_k_index = tgid.z;" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K metallib source lost z-axis split index")
    if "atomic_fetch_add_explicit" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K metallib source lost atomic accumulation")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang split-K atomic GEMM metallib bridge was not load-validated: "
            f"{package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang split-K atomic metallib bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang split-K atomic launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path / "real_pcc1_tilelang_splitk_atomic_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_splitk_atomic_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_splitk_atomic_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_splitk_atomic_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        c_zero_nbytes=5 * 7 * 4,
        first_error_code=9800,
        src_stem="pcc1_5gc_tilelang_splitk_atomic_metallib",
        failure_label="TileLang split-K atomic GEMM",
    )


def _build_real_metallib_tilelang_splitk_atomic_vectorized_c_artifacts(
    tmp_path: Path,
):
    module = pcc1_metal._tilelang_splitk_atomic_vectorized_c_module()
    body = module.funcs[0].body
    atomic = next(
        op
        for op in body
        if op.op == "atomic_add" and op.args == ("C", "C_local")
    )
    if atomic.attrs.get("parallel_vars") != ["i"]:
        pytest.fail("TileLang split-K atomic vectorized C lost T.Parallel metadata")
    if atomic.attrs.get("parallel_extents") != [8]:
        pytest.fail("TileLang split-K atomic vectorized C lost parallel extent")
    if atomic.attrs.get("vectorized_var") != "j":
        pytest.fail("TileLang split-K atomic vectorized C lost T.vectorized var")
    if atomic.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang split-K atomic vectorized C lost vectorized extent")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_splitk_atomic_vectorized_c_args(),
        tmp_path
        / "real_pcc1_tilelang_splitk_atomic_vectorized_c_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C metallib package did not "
            f"produce metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C metallib package did not "
            "retain source"
        )
    if "device atomic_float* C [[buffer(2)]]" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C source lost atomic output pointer"
        )
    if "uint split_k_index = tgid.z;" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C source lost z-axis split index"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C source lost zero-start K-loop"
        )
    if "atomic_fetch_add_explicit" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C source lost atomic accumulation"
        )
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C bridge missing library path"
        )
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang split-K atomic T.vectorized C launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_splitk_atomic_vectorized_c_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_splitk_atomic_vectorized_c_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_splitk_atomic_vectorized_c_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_splitk_atomic_vectorized_c_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        c_zero_nbytes=5 * 7 * 4,
        first_error_code=10555,
        src_stem="pcc1_5gc_tilelang_splitk_atomic_vectorized_c_metallib",
        failure_label="TileLang split-K atomic T.vectorized C GEMM",
    )


def _build_real_metallib_tilelang_splitk_atomic_ceildiv_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_splitk_atomic_ceildiv_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_splitk_atomic_ceildiv_args(),
        tmp_path / "real_pcc1_tilelang_splitk_atomic_ceildiv_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang split-K atomic ceildiv GEMM metallib package did not "
            f"produce metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang split-K atomic ceildiv metallib package did not retain source"
        )
    if "device atomic_float* C [[buffer(2)]]" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K ceildiv source lost atomic output pointer")
    if "uint split_k0 = split_k_index * 5u;" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K ceildiv source lost split_k0 computation")
    if "uint split_k_end = min(split_k0 + 5u, 17u);" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K ceildiv source lost split_k_end guard")
    if "atomic_fetch_add_explicit" not in package.finalize.metal_source:
        pytest.fail("TileLang split-K ceildiv source lost atomic accumulation")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang split-K atomic ceildiv GEMM metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang split-K atomic ceildiv bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang split-K atomic ceildiv launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_splitk_atomic_ceildiv_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_splitk_atomic_ceildiv_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_splitk_atomic_ceildiv_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_splitk_atomic_ceildiv_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 17 * 2,
        b_nbytes=17 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        c_zero_nbytes=5 * 7 * 4,
        first_error_code=9900,
        src_stem="pcc1_5gc_tilelang_splitk_atomic_ceildiv_metallib",
        failure_label="TileLang split-K atomic ceildiv GEMM",
    )


def _build_pcc1_tilelang_splitk_floor_plus_one_ceildiv_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_splitk_floor_plus_one_ceildiv_artifacts(
            tmp_path
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 17 * 2,
        b_nbytes=17 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        c_zero_nbytes=5 * 7 * 4,
        first_error_code=10600,
        src_stem="pcc1_5gc_tilelang_splitk_floor_plus_one_ceildiv_metallib",
        failure_label="TileLang split-K floor-plus-one ceildiv GEMM",
    )


def _build_pcc1_tilelang_output_staging_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_output_staging_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=3 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10700,
        src_stem="pcc1_5gc_tilelang_output_staged_gemm_metallib",
        failure_label="TileLang output-staged GEMM",
    )


def _build_pcc1_tilelang_output_staging_f16_transpose_b_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_output_staging_f16_transpose_b_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 2,
        c_dtype="f16",
        first_error_code=20700,
        src_stem="pcc1_5gc_tilelang_output_staged_f16_transpose_b_gemm_metallib",
        failure_label="TileLang output-staged f16 transpose_B GEMM",
    )


def _build_pcc1_tilelang_gemmwarp_policy_alias_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        pcc1_metal._build_real_metallib_tilelang_output_staging_f16_transpose_b_policy_alias_artifacts(
            tmp_path
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=7 * 3 * 2,
        c_nbytes=5 * 7 * 2,
        c_dtype="f16",
        first_error_code=20800,
        src_stem="pcc1_5gc_tilelang_gemmwarp_policy_alias_metallib",
        failure_label="TileLang GemmWarpPolicy alias metallib",
    )


def _build_real_metallib_tilelang_swizzled_padded_annotate_layout_artifacts(
    tmp_path: Path,
):
    module = pcc1_metal._tilelang_swizzled_padded_annotate_layout_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_swizzled_padded_annotate_layout_args(),
        tmp_path
        / "real_pcc1_tilelang_swizzled_padded_annotate_layout_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang swizzled padded annotate_layout metallib package did not "
            f"produce metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang swizzled padded annotate_layout metallib package did not "
            "retain source"
        )
    if (
        "uint a_shared_idx = ((a_local_m * 8u) + a_local_k);"
        not in package.finalize.metal_source
    ):
        pytest.fail(
            "TileLang swizzled padded annotate_layout source lost A shared index"
        )
    if (
        "uint b_shared_idx = ((b_local_k * 8u) + b_local_n);"
        not in package.finalize.metal_source
    ):
        pytest.fail(
            "TileLang swizzled padded annotate_layout source lost B shared index"
        )
    if "A_shared[a_shared_idx]" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang swizzled padded annotate_layout source lost A shared write"
        )
    if "B_shared[b_shared_idx]" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang swizzled padded annotate_layout source lost B shared write"
        )
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang swizzled padded annotate_layout metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail(
            "TileLang swizzled padded annotate_layout bridge missing library path"
        )
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang swizzled padded annotate_layout launch plan missing "
            "metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_swizzled_padded_annotate_layout_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_swizzled_padded_annotate_layout_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_swizzled_padded_annotate_layout_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_swizzled_padded_annotate_layout_artifacts(
            tmp_path
        )
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 3 * 2,
        b_nbytes=3 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10000,
        src_stem="pcc1_5gc_tilelang_swizzled_padded_annotate_layout_metallib",
        failure_label="TileLang swizzled padded annotate_layout GEMM",
    )


def _build_real_metallib_tilelang_enabled_swizzle_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_enabled_swizzle_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_enabled_swizzle_args(),
        tmp_path / "real_pcc1_tilelang_enabled_swizzle_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang enabled use_swizzle metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail("TileLang enabled use_swizzle metallib package did not retain source")
    for needle in (
        "uint swizzle_grid_x = 3u;",
        "uint swizzle_grid_y = 3u;",
        "uint swizzle_panel_size = 2u * swizzle_grid_x;",
        "uint tile_col0 = tile_gid_x * 8u;",
        "uint tile_row0 = tile_gid_y * 8u;",
    ):
        if needle not in package.finalize.metal_source:
            pytest.fail(f"TileLang enabled use_swizzle source lost {needle!r}")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang enabled use_swizzle metallib bridge was not load-validated: "
            f"{package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang enabled use_swizzle bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang enabled use_swizzle launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path / "real_pcc1_tilelang_enabled_swizzle_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_enabled_swizzle_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_enabled_swizzle_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_enabled_swizzle_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=17 * 16 * 2,
        b_nbytes=16 * 19 * 2,
        c_nbytes=17 * 19 * 4,
        first_error_code=10200,
        src_stem="pcc1_5gc_tilelang_enabled_swizzle_metallib",
        failure_label="TileLang enabled use_swizzle GEMM",
    )


def _build_real_metallib_tilelang_nonzero_step_pipelined_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_nonzero_step_pipelined_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_nonzero_step_pipelined_args(),
        tmp_path / "real_pcc1_tilelang_nonzero_step_pipelined_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined metallib package did not "
            f"produce metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined metallib package did not "
            "retain source"
        )
    if "for (uint ko = 1u; ko < 5u; ko += 2u)" not in package.finalize.metal_source:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined source lost nonzero stepped loop"
        )
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined bridge missing library path"
        )
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang nonzero stepped T.Pipelined launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_nonzero_step_pipelined_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_nonzero_step_pipelined_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_nonzero_step_pipelined_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_nonzero_step_pipelined_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 40 * 2,
        b_nbytes=40 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10400,
        src_stem="pcc1_5gc_tilelang_nonzero_step_pipelined_metallib",
        failure_label="TileLang nonzero stepped T.Pipelined GEMM",
    )


def _build_real_metallib_tilelang_vectorized_a_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_vectorized_a_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("parallel_vars") != ["i"]:
        pytest.fail("TileLang vectorized A import lost A T.Parallel metadata")
    if a_copy.attrs.get("vectorized_var") != "kk":
        pytest.fail("TileLang vectorized A import lost A T.vectorized var")
    if a_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized A import lost A vectorized extent")
    if b_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang vectorized A import lost B serial K-loop metadata")
    if "parallel_vars" in b_copy.attrs:
        pytest.fail("TileLang vectorized A import added unexpected B T.Parallel metadata")
    if "vectorized_var" in b_copy.attrs:
        pytest.fail("TileLang vectorized A import added unexpected B T.vectorized metadata")
    if c_copy.attrs:
        pytest.fail("TileLang vectorized A import added unexpected C copy metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_vectorized_a_copy_args(),
        tmp_path / "real_pcc1_tilelang_vectorized_a_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.vectorized A copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.vectorized A copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost vector load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.vectorized A copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.vectorized A copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.vectorized A copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_vectorized_a_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_vectorized_a_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_vectorized_a_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_vectorized_a_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10525,
        src_stem="pcc1_5gc_tilelang_vectorized_a_copy_metallib",
        failure_label="TileLang T.vectorized A copy GEMM",
    )


def _build_real_metallib_tilelang_vectorized_b_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_vectorized_b_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang vectorized B import lost A serial K-loop metadata")
    if "parallel_vars" in a_copy.attrs:
        pytest.fail("TileLang vectorized B import added unexpected A T.Parallel metadata")
    if "vectorized_var" in a_copy.attrs:
        pytest.fail("TileLang vectorized B import added unexpected A T.vectorized metadata")
    if b_copy.attrs.get("parallel_vars") != ["kk"]:
        pytest.fail("TileLang vectorized B import lost B T.Parallel metadata")
    if b_copy.attrs.get("vectorized_var") != "j":
        pytest.fail("TileLang vectorized B import lost B T.vectorized var")
    if b_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized B import lost B vectorized extent")
    if c_copy.attrs:
        pytest.fail("TileLang vectorized B import added unexpected C copy metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_vectorized_b_copy_args(),
        tmp_path / "real_pcc1_tilelang_vectorized_b_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.vectorized B copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.vectorized B copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost vector load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized B copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.vectorized B copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.vectorized B copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.vectorized B copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_vectorized_b_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_vectorized_b_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_vectorized_b_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_vectorized_b_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10535,
        src_stem="pcc1_5gc_tilelang_vectorized_b_copy_metallib",
        failure_label="TileLang T.vectorized B copy GEMM",
    )


def _build_real_metallib_tilelang_vectorized_c_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_vectorized_c_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang vectorized C import lost A serial K-loop metadata")
    if "parallel_vars" in a_copy.attrs:
        pytest.fail("TileLang vectorized C import added unexpected A T.Parallel metadata")
    if "vectorized_var" in a_copy.attrs:
        pytest.fail("TileLang vectorized C import added unexpected A T.vectorized metadata")
    if b_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang vectorized C import lost B serial K-loop metadata")
    if "parallel_vars" in b_copy.attrs:
        pytest.fail("TileLang vectorized C import added unexpected B T.Parallel metadata")
    if "vectorized_var" in b_copy.attrs:
        pytest.fail("TileLang vectorized C import added unexpected B T.vectorized metadata")
    if c_copy.attrs.get("parallel_vars") != ["i"]:
        pytest.fail("TileLang vectorized C import lost C T.Parallel metadata")
    if c_copy.attrs.get("vectorized_var") != "j":
        pytest.fail("TileLang vectorized C import lost C T.vectorized var")
    if c_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized C import lost C vectorized extent")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_vectorized_c_copy_args(),
        tmp_path / "real_pcc1_tilelang_vectorized_c_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.vectorized C copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.vectorized C copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost vector load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized C copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.vectorized C copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.vectorized C copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.vectorized C copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_vectorized_c_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_vectorized_c_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_vectorized_c_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_vectorized_c_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10545,
        src_stem="pcc1_5gc_tilelang_vectorized_c_copy_metallib",
        failure_label="TileLang T.vectorized C copy GEMM",
    )


def _build_real_metallib_tilelang_vectorized_abc_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_vectorized_abc_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("parallel_vars") != ["i"]:
        pytest.fail("TileLang vectorized ABC import lost A T.Parallel metadata")
    if a_copy.attrs.get("vectorized_var") != "kk":
        pytest.fail("TileLang vectorized ABC import lost A T.vectorized var")
    if a_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized ABC import lost A vectorized extent")
    if b_copy.attrs.get("parallel_vars") != ["kk"]:
        pytest.fail("TileLang vectorized ABC import lost B T.Parallel metadata")
    if b_copy.attrs.get("vectorized_var") != "j":
        pytest.fail("TileLang vectorized ABC import lost B T.vectorized var")
    if b_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized ABC import lost B vectorized extent")
    if c_copy.attrs.get("parallel_vars") != ["i"]:
        pytest.fail("TileLang vectorized ABC import lost C T.Parallel metadata")
    if c_copy.attrs.get("vectorized_var") != "j":
        pytest.fail("TileLang vectorized ABC import lost C T.vectorized var")
    if c_copy.attrs.get("vectorized_extent") != 8:
        pytest.fail("TileLang vectorized ABC import lost C vectorized extent")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_vectorized_abc_copy_args(),
        tmp_path / "real_pcc1_tilelang_vectorized_abc_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.vectorized A/B/C copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.vectorized A/B/C copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost vector load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.vectorized A/B/C copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.vectorized A/B/C copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.vectorized A/B/C copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang T.vectorized A/B/C copy launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_vectorized_abc_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_vectorized_abc_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_vectorized_abc_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_vectorized_abc_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10550,
        src_stem="pcc1_5gc_tilelang_vectorized_abc_copy_metallib",
        failure_label="TileLang T.vectorized A/B/C copy GEMM",
    )


def _build_real_metallib_tilelang_vectorized_annotations_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_vectorized_annotations_module()
    a_copy = next(
        op for op in module.funcs[0].body
        if op.op == "copy" and op.args[1] == "A_shared"
    )
    if a_copy.attrs.get("vectorized_annotations") != {"pragma_unroll": True}:
        pytest.fail(
            "TileLang vectorized annotations import lost pragma_unroll metadata"
        )
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_vectorized_annotations_args(),
        tmp_path / "real_pcc1_tilelang_vectorized_annotations_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang vectorized annotations metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang vectorized annotations metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang vectorized annotations source lost K-loop")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang vectorized annotations metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang vectorized annotations bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang vectorized annotations launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_vectorized_annotations_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_vectorized_annotations_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_vectorized_annotations_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_vectorized_annotations_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10600,
        src_stem="pcc1_5gc_tilelang_vectorized_annotations_metallib",
        failure_label="TileLang vectorized annotations GEMM",
    )


def _build_real_metallib_tilelang_parallel_a_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_parallel_a_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("parallel_vars") != ["i", "kk"]:
        pytest.fail("TileLang parallel A import lost A T.Parallel metadata")
    if b_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang parallel A import lost B serial K-loop metadata")
    if "parallel_vars" in b_copy.attrs:
        pytest.fail("TileLang parallel A import added unexpected B T.Parallel metadata")
    if c_copy.attrs:
        pytest.fail("TileLang parallel A import added unexpected C copy metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_parallel_a_copy_args(),
        tmp_path / "real_pcc1_tilelang_parallel_a_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.Parallel A copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.Parallel A copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost parallel A load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.Parallel A copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.Parallel A copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.Parallel A copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_parallel_a_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_parallel_a_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_parallel_a_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_parallel_a_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10625,
        src_stem="pcc1_5gc_tilelang_parallel_a_copy_metallib",
        failure_label="TileLang T.Parallel A copy GEMM",
    )


def _build_real_metallib_tilelang_parallel_b_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_parallel_b_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang parallel B import lost A serial K-loop metadata")
    if "parallel_vars" in a_copy.attrs:
        pytest.fail("TileLang parallel B import added unexpected A T.Parallel metadata")
    if b_copy.attrs.get("parallel_vars") != ["kk", "j"]:
        pytest.fail("TileLang parallel B import lost B T.Parallel metadata")
    if c_copy.attrs:
        pytest.fail("TileLang parallel B import added unexpected C copy metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_parallel_b_copy_args(),
        tmp_path / "real_pcc1_tilelang_parallel_b_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.Parallel B copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.Parallel B copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost parallel B load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel B copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.Parallel B copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.Parallel B copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.Parallel B copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_parallel_b_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_parallel_b_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_parallel_b_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_parallel_b_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10635,
        src_stem="pcc1_5gc_tilelang_parallel_b_copy_metallib",
        failure_label="TileLang T.Parallel B copy GEMM",
    )


def _build_real_metallib_tilelang_parallel_c_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_parallel_c_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang parallel C import lost A serial K-loop metadata")
    if "parallel_vars" in a_copy.attrs:
        pytest.fail("TileLang parallel C import added unexpected A T.Parallel metadata")
    if b_copy.attrs.get("serial_extent") != 2:
        pytest.fail("TileLang parallel C import lost B serial K-loop metadata")
    if "parallel_vars" in b_copy.attrs:
        pytest.fail("TileLang parallel C import added unexpected B T.Parallel metadata")
    if c_copy.attrs.get("parallel_vars") != ["i", "j"]:
        pytest.fail("TileLang parallel C import lost C T.Parallel metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_parallel_c_copy_args(),
        tmp_path / "real_pcc1_tilelang_parallel_c_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.Parallel C copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.Parallel C copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost tiled load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel C copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.Parallel C copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.Parallel C copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.Parallel C copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_parallel_c_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_parallel_c_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_parallel_c_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_parallel_c_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10645,
        src_stem="pcc1_5gc_tilelang_parallel_c_copy_metallib",
        failure_label="TileLang T.Parallel C copy GEMM",
    )


def _build_real_metallib_tilelang_parallel_ab_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_parallel_ab_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("parallel_vars") != ["i", "kk"]:
        pytest.fail("TileLang parallel A/B import lost A T.Parallel metadata")
    if b_copy.attrs.get("parallel_vars") != ["kk", "j"]:
        pytest.fail("TileLang parallel A/B import lost B T.Parallel metadata")
    if c_copy.attrs:
        pytest.fail("TileLang parallel A/B import added unexpected C copy metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_parallel_ab_copy_args(),
        tmp_path / "real_pcc1_tilelang_parallel_ab_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.Parallel A/B copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.Parallel A/B copy metallib package did not retain source"
        )
    if "for (uint ko = 0u; ko < 2u; ++ko)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost zero-start K-loop")
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost parallel load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.Parallel A/B copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.Parallel A/B copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail("TileLang T.Parallel A/B copy launch plan missing metallib path")

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_parallel_ab_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_parallel_ab_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_parallel_ab_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_parallel_ab_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10650,
        src_stem="pcc1_5gc_tilelang_parallel_ab_copy_metallib",
        failure_label="TileLang T.Parallel A/B copy GEMM",
    )


def _build_real_metallib_tilelang_parallel_abc_copy_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_parallel_abc_copy_module()
    body = module.funcs[0].body
    a_copy = next(
        op for op in body if op.op == "copy" and op.args == ("A", "A_shared")
    )
    b_copy = next(
        op for op in body if op.op == "copy" and op.args == ("B", "B_shared")
    )
    c_copy = next(
        op for op in body if op.op == "copy" and op.args == ("C_local", "C")
    )
    if a_copy.attrs.get("parallel_vars") != ["i", "kk"]:
        pytest.fail("TileLang parallel ABC import lost A T.Parallel metadata")
    if b_copy.attrs.get("parallel_vars") != ["kk", "j"]:
        pytest.fail("TileLang parallel ABC import lost B T.Parallel metadata")
    if c_copy.attrs.get("parallel_vars") != ["i", "j"]:
        pytest.fail("TileLang parallel ABC import lost C T.Parallel metadata")

    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_parallel_abc_copy_args(),
        tmp_path / "real_pcc1_tilelang_parallel_abc_copy_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang T.Parallel A/B/C copy metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang T.Parallel A/B/C copy metallib package did not retain source"
        )
    if "threadgroup half A_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B/C copy source lost A_shared tile")
    if "threadgroup half B_shared[64];" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B/C copy source lost B_shared tile")
    if "for (uint load = tid; load < 64u; load += 32u)" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B/C copy source lost parallel load loop")
    if "threadgroup_barrier(mem_flags::mem_threadgroup);" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B/C copy source lost tile barrier")
    if "C[(row * 7u) + col] = (float)acc;" not in package.finalize.metal_source:
        pytest.fail("TileLang T.Parallel A/B/C copy source lost guarded C writeback")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang T.Parallel A/B/C copy metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang T.Parallel A/B/C copy bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang T.Parallel A/B/C copy launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_parallel_abc_copy_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_parallel_abc_copy_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_parallel_abc_copy_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_parallel_abc_copy_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10700,
        src_stem="pcc1_5gc_tilelang_parallel_abc_copy_metallib",
        failure_label="TileLang T.Parallel A/B/C copy GEMM",
    )


def _build_real_metallib_tilelang_nonzero_step_serial_artifacts(tmp_path: Path):
    module = pcc1_metal._tilelang_nonzero_step_serial_module()
    package = pcc1_metal.build_metal_kernel_package(
        module,
        pcc1_metal._tilelang_nonzero_step_pipelined_args(),
        tmp_path / "real_pcc1_tilelang_nonzero_step_serial_metallib_package",
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        timeout=90.0,
    )
    if package.finalize.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(package.finalize.reason)
    if not package.finalize.metallib_produced:
        pytest.fail(
            "TileLang nonzero stepped T.serial metallib package did not produce "
            f"metallib: {package.to_dict()}"
        )
    if package.finalize.metal_source is None:
        pytest.fail(
            "TileLang nonzero stepped T.serial metallib package did not retain source"
        )
    if "for (uint ko = 1u; ko < 5u; ko += 2u)" not in package.finalize.metal_source:
        pytest.fail("TileLang nonzero stepped T.serial source lost loop")
    if not package.bridge_library_load_validated:
        pytest.fail(
            "TileLang nonzero stepped T.serial metallib bridge was not "
            f"load-validated: {package.to_dict()}"
        )
    if package.bridge_library_path is None:
        pytest.fail("TileLang nonzero stepped T.serial bridge missing library path")
    if package.launch_plan.metallib_path is None:
        pytest.fail(
            "TileLang nonzero stepped T.serial launch plan missing metallib path"
        )

    native_runtime = pcc1_metal.build_metal_native_buffer_runtime_artifacts(
        tmp_path
        / "real_pcc1_tilelang_nonzero_step_serial_metallib_native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
    )
    if native_runtime.status == STATUS_SKIPPED_WITH_REASON:
        pytest.skip(native_runtime.reason)
    if native_runtime.status != pcc1_metal.STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED:
        pytest.fail(
            f"native buffer runtime was not load-validated: {native_runtime.to_dict()}"
        )
    if native_runtime.library_path is None:
        pytest.fail("native buffer runtime did not produce a library path")

    a, b = pcc1_metal._tilelang_nonzero_step_pipelined_inputs()
    expected = pcc1_metal.execute_scalar_tiled_gemm_reference(
        module,
        {"A": a, "B": b},
    ).outputs["C"]
    return package, native_runtime, a, b, expected


def _build_pcc1_tilelang_nonzero_step_serial_metallib_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, a, b, expected = (
        _build_real_metallib_tilelang_nonzero_step_serial_artifacts(tmp_path)
    )
    return _build_pcc1_three_buffer_metallib_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 40 * 2,
        b_nbytes=40 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=10800,
        src_stem="pcc1_5gc_tilelang_nonzero_step_serial_metallib",
        failure_label="TileLang nonzero stepped T.serial GEMM",
    )


def _build_pcc1_tilelang_gemm_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()

    package, native_runtime, source_bridge, metal_source = (
        pcc1_metal._build_real_runtime_source_tilelang_gemm_artifacts(tmp_path)
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    src = tmp_path / "pcc1_5gc_tilelang_gemm.py"
    exe = tmp_path / "pcc1_5gc_tilelang_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(8)
                b_payload = malloc(8)
                readback = malloc(16)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 8, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 8, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 16, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

                store_i8(a_payload, 0, 0)
                store_i8(a_payload, 1, 60)
                store_i8(a_payload, 2, 0)
                store_i8(a_payload, 3, 64)
                store_i8(a_payload, 4, 0)
                store_i8(a_payload, 5, 66)
                store_i8(a_payload, 6, 0)
                store_i8(a_payload, 7, 68)

                store_i8(b_payload, 0, 0)
                store_i8(b_payload, 1, 69)
                store_i8(b_payload, 2, 0)
                store_i8(b_payload, 3, 70)
                store_i8(b_payload, 4, 0)
                store_i8(b_payload, 5, 71)
                store_i8(b_payload, 6, 0)
                store_i8(b_payload, 7, 72)

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 8)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 8)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 16)
                if rc != 0:
                    print(rc)
                    return
                if load_i32(readback, 0) != 1100480512:
                    print(920)
                    return
                if load_i32(readback, 4) != 1102053376:
                    print(921)
                    return
                if load_i32(readback, 8) != 1110179840:
                    print(922)
                    return
                if load_i32(readback, 12) != 1112014848:
                    print(923)
                    return

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        f"pcc1 compile failed for five-GC Metal lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_gemm_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_gemm_artifacts(tmp_path)
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=940,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(128)
                b_payload = malloc(128)
                readback = malloc(256)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 128, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 128, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 256, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 128)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 128)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 256)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        f"pcc1 compile failed for five-GC simdgroup lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_two_n_gemm_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_two_n_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1320,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_two_n_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_two_n_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(128)
                b_payload = malloc(256)
                readback = malloc(512)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 128, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 256, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 512, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 128)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 256)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 512)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC two-N simdgroup lifetime probe "
        f"(exit {build.returncode})\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_two_m_gemm_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_two_m_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1460,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_two_m_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_two_m_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(256)
                b_payload = malloc(128)
                readback = malloc(512)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 256, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 128, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 512, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 256)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 128)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 512)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC two-M simdgroup lifetime probe "
        f"(exit {build.returncode})\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_gemm_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1600,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(256)
                b_payload = malloc(256)
                readback = malloc(1024)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 256, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 256, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1024, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 256)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 256)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 1024)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D simdgroup lifetime probe "
        f"(exit {build.returncode})\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_transpose_ab_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_transpose_ab_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1900,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_transpose_ab_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_transpose_ab_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(256)
                b_payload = malloc(256)
                readback = malloc(1024)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 256, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 256, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1024, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 256)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 256)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 1024)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D transpose simdgroup lifetime "
        f"probe (exit {build.returncode})\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=2200,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_edge_tail_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_edge_tail_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(270)
                b_payload = malloc(270)
                readback = malloc(900)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 270, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 270, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 900, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 270)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 270)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 900)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D edge-tail simdgroup lifetime "
        f"probe (exit {build.returncode})\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_transpose_ab_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_transpose_ab_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=2500,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_transpose_ab_edge_tail_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_transpose_ab_edge_tail_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(270)
                b_payload = malloc(270)
                readback = malloc(900)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 270, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 270, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 900, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 270)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 270)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 900)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D transpose edge-tail simdgroup "
        f"lifetime probe (exit {build.returncode})\nstdout:\n{build.stdout}\n"
        f"stderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_splitk_atomic_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_splitk_atomic_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        1024,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=2800,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_splitk_atomic_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_splitk_atomic_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(512)
                b_payload = malloc(512)
                c_payload = malloc(1024)
                readback = malloc(1024)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 512, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 512, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1024, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 512)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 512)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 1024)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 1024)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D split-K atomic simdgroup "
        f"lifetime probe (exit {build.returncode})\nstdout:\n{build.stdout}\n"
        f"stderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        900,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=3400,
    )

    src = tmp_path / "pcc1_5gc_simdgroup_four_2d_splitk_atomic_edge_tail_gemm.py"
    exe = tmp_path / "pcc1_5gc_simdgroup_four_2d_splitk_atomic_edge_tail_gemm"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(510)
                b_payload = malloc(510)
                c_payload = malloc(900)
                readback = malloc(900)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 510, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 510, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 900, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 510)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 510)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 900)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 900)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D split-K atomic edge-tail "
        f"simdgroup lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        900,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=4000,
    )

    src = (
        tmp_path
        / "pcc1_5gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm.py"
    )
    exe = (
        tmp_path
        / "pcc1_5gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm"
    )
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(510)
                b_payload = malloc(510)
                c_payload = malloc(900)
                readback = malloc(900)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 510, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 510, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 900, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 510)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 510)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 900)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 900)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC four-2D transpose split-K atomic "
        f"edge-tail simdgroup lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        1860,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=4500,
    )

    src = (
        tmp_path
        / "pcc1_5gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm.py"
    )
    exe = (
        tmp_path
        / "pcc1_5gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm"
    )
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(510)
                b_payload = malloc(1054)
                c_payload = malloc(1860)
                readback = malloc(1860)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 510, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1054, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1860, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 510)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 1054)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 1860)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 1860)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC eight-simdgroup transpose split-K "
        f"atomic edge-tail lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        3844,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=5200,
    )

    src = (
        tmp_path
        / "pcc1_5gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm.py"
    )
    exe = (
        tmp_path
        / "pcc1_5gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm"
    )
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(1054)
                b_payload = malloc(1054)
                c_payload = malloc(3844)
                readback = malloc(3844)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 1054, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1054, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 3844, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 1054)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 1054)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 3844)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 3844)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC sixteen-simdgroup transpose split-K "
        f"atomic edge-tail lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        7812,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=6200,
    )

    src = (
        tmp_path
        / "pcc1_5gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm.py"
    )
    exe = (
        tmp_path
        / "pcc1_5gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm"
    )
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(1054)
                b_payload = malloc(2142)
                c_payload = malloc(7812)
                readback = malloc(7812)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 1054, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 2142, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 7812, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 1054)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 2142)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 7812)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 7812)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC thirty-two-simdgroup transpose split-K "
        f"atomic edge-tail lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_vectorized_nonzero_serial_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_vectorized_nonzero_serial_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=960,
    )

    src = tmp_path / "pcc1_5gc_tilelang_vectorized_nonzero_serial.py"
    exe = tmp_path / "pcc1_5gc_tilelang_vectorized_nonzero_serial"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(240)
                b_payload = malloc(336)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 240, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 336, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 240)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 336)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang vectorized nonzero-serial "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_vectorized_annotations_lifetime_probe(
    tmp_path: Path,
) -> Path:
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_vectorized_annotations_artifacts(
            tmp_path
        )
    )
    return _build_pcc1_three_buffer_lifetime_probe(
        tmp_path,
        package=package,
        native_runtime=native_runtime,
        source_bridge=source_bridge,
        metal_source=metal_source,
        a=a,
        b=b,
        expected=expected,
        a_nbytes=5 * 16 * 2,
        b_nbytes=16 * 7 * 2,
        c_nbytes=5 * 7 * 4,
        first_error_code=8200,
        src_stem="pcc1_5gc_tilelang_vectorized_annotations",
        failure_label="TileLang vectorized annotations runtime-source",
    )


def _build_pcc1_tilelang_splitk_atomic_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_splitk_atomic_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        140,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=980,
    )

    src = tmp_path / "pcc1_5gc_tilelang_splitk_atomic.py"
    exe = tmp_path / "pcc1_5gc_tilelang_splitk_atomic"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(160)
                b_payload = malloc(224)
                c_payload = malloc(140)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 160, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 224, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 160)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 224)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 140)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang split-K atomic lifetime probe "
        f"(exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_splitk_atomic_ceildiv_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_splitk_atomic_ceildiv_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_zero_lines = pcc1_metal._pcc1_zero_i32_payload_lines(
        "c_payload",
        140,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1020,
    )

    src = tmp_path / "pcc1_5gc_tilelang_splitk_atomic_ceildiv.py"
    exe = tmp_path / "pcc1_5gc_tilelang_splitk_atomic_ceildiv"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(170)
                b_payload = malloc(238)
                c_payload = malloc(140)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 170, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 238, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}
{c_zero_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 170)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 238)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, c_ptr, 0, c_payload, 140)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(c_payload)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang split-K atomic ceildiv-tail "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_transpose_ab_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_transpose_ab_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1060,
    )

    src = tmp_path / "pcc1_5gc_tilelang_transpose_ab.py"
    exe = tmp_path / "pcc1_5gc_tilelang_transpose_ab"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(30)
                b_payload = malloc(42)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 30, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 42, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 30)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 42)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang transpose_A+transpose_B "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_swizzled_padded_annotate_layout_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_swizzled_padded_annotate_layout_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_byte_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1100,
    )

    src = tmp_path / "pcc1_5gc_tilelang_swizzled_padded_annotate_layout.py"
    exe = tmp_path / "pcc1_5gc_tilelang_swizzled_padded_annotate_layout"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i8,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(30)
                b_payload = malloc(42)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 30, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 42, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 30)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 42)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang swizzled padded annotate_layout "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_enabled_swizzle_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_enabled_swizzle_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1140,
    )

    src = tmp_path / "pcc1_5gc_tilelang_enabled_swizzle.py"
    exe = tmp_path / "pcc1_5gc_tilelang_enabled_swizzle"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(544)
                b_payload = malloc(608)
                readback = malloc(1292)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 544, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 608, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 1292, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 544)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 608)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 1292)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang enabled use_swizzle "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_nonzero_start_pipelined_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_nonzero_start_pipelined_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=1500,
    )

    src = tmp_path / "pcc1_5gc_tilelang_nonzero_start_pipelined.py"
    exe = tmp_path / "pcc1_5gc_tilelang_nonzero_start_pipelined"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(240)
                b_payload = malloc(336)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 240, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 336, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 240)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 336)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang nonzero-start Pipelined "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_step_serial_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_step_serial_artifacts(tmp_path)
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=7000,
    )

    src = tmp_path / "pcc1_5gc_tilelang_step_serial.py"
    exe = tmp_path / "pcc1_5gc_tilelang_step_serial"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(320)
                b_payload = malloc(448)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 320, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 448, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 320)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 448)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang stepped T.serial "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_step_pipelined_lifetime_probe(tmp_path: Path) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_step_pipelined_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=7100,
    )

    src = tmp_path / "pcc1_5gc_tilelang_step_pipelined.py"
    exe = tmp_path / "pcc1_5gc_tilelang_step_pipelined"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(320)
                b_payload = malloc(448)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 320, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 448, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 320)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 448)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang stepped T.Pipelined "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_nonzero_step_pipelined_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_nonzero_step_pipelined_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=7200,
    )

    src = tmp_path / "pcc1_5gc_tilelang_nonzero_step_pipelined.py"
    exe = tmp_path / "pcc1_5gc_tilelang_nonzero_step_pipelined"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(400)
                b_payload = malloc(560)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 400, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 560, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 400)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 560)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang nonzero stepped T.Pipelined "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _build_pcc1_tilelang_nonzero_step_serial_lifetime_probe(
    tmp_path: Path,
) -> Path:
    pcc1 = _find_current_no_libpython_pcc1()
    package, native_runtime, source_bridge, metal_source, a, b, expected = (
        pcc1_metal._build_real_runtime_source_tilelang_nonzero_step_serial_artifacts(
            tmp_path
        )
    )
    assert package.finalize.metallib_produced is False
    assert package.runtime_launch_executed is False

    a_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "a_payload",
        a,
        indent="                ",
    )
    b_store_lines = pcc1_metal._pcc1_store_f16_matrix_lines(
        "b_payload",
        b,
        indent="                ",
    )
    c_assert_lines = pcc1_metal._pcc1_assert_f32_matrix_lines(
        "readback",
        expected,
        indent="                ",
        first_error_code=7300,
    )

    src = tmp_path / "pcc1_5gc_tilelang_nonzero_step_serial.py"
    exe = tmp_path / "pcc1_5gc_tilelang_nonzero_step_serial"
    src.write_text(
        textwrap.dedent(
            f"""
            from pcc.extern import extern, c_int32, c_int64, c_ptr, c_uint64
            from pcc.unsafe import (
                cstr,
                free,
                getenv,
                load_i8,
                load_i32,
                load_i64,
                malloc,
                ptr_is_null,
                store_i32,
                store_i64,
            )

            pcc_metal_call = extern(
                "pcc_metal_source_runtime_call_prebuilt",
                (c_ptr, c_ptr, c_ptr, c_uint64, c_ptr, c_uint64, c_ptr, c_ptr, c_uint64, c_int32),
                c_int64,
            )
            pcc_buffer_create = extern(
                "pcc_metal_buffer_runtime_create_prebuilt",
                (c_ptr, c_uint64, c_ptr),
                c_int64,
            )
            pcc_buffer_write = extern(
                "pcc_metal_buffer_runtime_write_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_read = extern(
                "pcc_metal_buffer_runtime_read_prebuilt",
                (c_ptr, c_uint64, c_uint64, c_ptr, c_uint64),
                c_int64,
            )
            pcc_buffer_release = extern(
                "pcc_metal_buffer_runtime_release_prebuilt",
                (c_ptr, c_uint64),
                c_int64,
            )

            def main() -> None:
                gc_backend = getenv(cstr("PCC_GC_BACKEND"))
                if ptr_is_null(gc_backend):
                    print(930)
                    return
                backend_code: int = load_i8(gc_backend, 0)
                if backend_code < 48 or backend_code > 52:
                    print(931)
                    return
                if load_i8(gc_backend, 1) != 0:
                    print(932)
                    return

                buffer_runtime = cstr({json.dumps(str(native_runtime.library_path))})
                source_runtime = cstr({json.dumps(str(source_bridge.library_path))})
                symbol = cstr({json.dumps(source_bridge.symbol)})
                metal_source = cstr({json.dumps(metal_source)})
                out_a = malloc(8)
                out_b = malloc(8)
                out_c = malloc(8)
                buffers = malloc(24)
                a_payload = malloc(400)
                b_payload = malloc(560)
                readback = malloc(140)
                scalar_payload = malloc(1)
                scalar_offsets = malloc(8)

                rc = pcc_buffer_create(buffer_runtime, 400, out_a)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 560, out_b)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_create(buffer_runtime, 140, out_c)
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                a_ptr: int = load_i64(out_a, 0)
                b_ptr: int = load_i64(out_b, 0)
                c_ptr: int = load_i64(out_c, 0)
                store_i64(buffers, 0, a_ptr)
                store_i64(buffers, 8, b_ptr)
                store_i64(buffers, 16, c_ptr)

{a_store_lines}
{b_store_lines}

                rc = pcc_buffer_write(buffer_runtime, a_ptr, 0, a_payload, 400)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_write(buffer_runtime, b_ptr, 0, b_payload, 560)
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_metal_call(
                    source_runtime,
                    symbol,
                    metal_source,
                    {len(metal_source.encode("utf-8"))},
                    buffers,
                    3,
                    scalar_payload,
                    scalar_offsets,
                    0,
                    1,
                )
                if rc == 3:
                    print(903)
                    return
                if rc != 0:
                    print(rc)
                    return

                rc = pcc_buffer_read(buffer_runtime, c_ptr, 0, readback, 140)
                if rc != 0:
                    print(rc)
                    return
{c_assert_lines}

                rc = pcc_buffer_release(buffer_runtime, c_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, b_ptr)
                if rc != 0:
                    print(rc)
                    return
                rc = pcc_buffer_release(buffer_runtime, a_ptr)
                if rc != 0:
                    print(rc)
                    return
                print(backend_code)
                print(0)

                free(scalar_offsets)
                free(scalar_payload)
                free(readback)
                free(b_payload)
                free(a_payload)
                free(buffers)
                free(out_c)
                free(out_b)
                free(out_a)

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            str(pcc1),
            str(src),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=env,
    )
    assert build.returncode == 0, (
        "pcc1 compile failed for five-GC TileLang nonzero stepped T.serial "
        f"lifetime probe (exit {build.returncode})\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    assert exe.exists()
    assert not pcc1_metal._links_libpython(exe), f"pcc1 output links libpython: {exe}"
    return exe


def _run_lifetime_probe_backend(
    exe: Path,
    backend: int,
    *,
    workload_id: str = "tilelang_scalar_gemm_runtime_source",
) -> dict[str, object]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = str(backend)
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=90,
        env=env,
    )
    assert run.returncode == 0, (
        f"pcc1 five-GC Metal lifetime probe failed under PCC_GC_BACKEND={backend} "
        f"(exit {run.returncode})\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    stdout = run.stdout.strip()
    if stdout == "903":
        pytest.skip("MTLCreateSystemDefaultDevice returned nil")
    assert stdout.splitlines() == [str(48 + backend), "0"]
    return _level5_lifetime_backend_record(backend, workload_id=workload_id)


def _run_metallib_lifetime_probe_backend(
    exe: Path,
    backend: int,
    *,
    workload_id: str = "simdgroup_gemm_8x8_metallib",
) -> dict[str, object]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = str(backend)
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=90,
        env=env,
    )
    assert run.returncode == 0, (
        f"pcc1 five-GC Metal metallib lifetime probe failed under "
        f"PCC_GC_BACKEND={backend} (exit {run.returncode})\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    stdout = run.stdout.strip()
    if stdout == "903":
        pytest.skip("MTLCreateSystemDefaultDevice returned nil")
    assert stdout.splitlines() == [str(48 + backend), "0"]
    return _level5_metallib_lifetime_backend_record(
        backend,
        workload_id=workload_id,
    )


def _five_backend_result(
    *,
    workload_id: str = "tilelang_scalar_gemm_runtime_source",
    env_label_only: bool = False,
) -> dict[str, object]:
    return {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": workload_id,
        "whole_program_gpu": False,
        "backend_results": [
            _level4_backend_record(
                backend,
                workload_id=workload_id,
                env_label_only=env_label_only,
            )
            for backend in range(5)
        ],
    }


def test_level6_gate_reports_mode_labeled_skip_when_not_enabled(monkeypatch):
    monkeypatch.delenv("PCC_RUN_GPU_5GC_LIFETIME", raising=False)
    reason = (
        "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
        "real pcc-runtime Metal lifetime parity is not implemented in this slice"
    )
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_scalar_gemm",
        {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": reason,
            "whole_program_gpu": False,
        },
    )

    checked = require_five_gc_parity_or_skip(evidence)
    assert checked.level is GpuClaimLevel.GPU_LEVEL_0_METADATA
    assert checked.proven is False
    assert checked.reason.startswith("SKIPPED_WITH_REASON:")


def test_level6_classifier_rejects_env_label_only_level4_matrix():
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_scalar_gemm",
        _five_backend_result(env_label_only=True),
    )

    assert evidence.level is GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT
    assert evidence.gc_backend_parity == (0,)
    assert "env-label-only" in evidence.reason
    with pytest.raises(GpuClaimError, match="GPU_LEVEL_6_5GC_PARITY"):
        require_five_gc_parity_or_skip(evidence)


def test_level6_classifier_requires_all_five_backends():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_scalar_gemm_runtime_source",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_lifetime_backend_record(backend)
            for backend in range(4)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result("tilelang_scalar_gemm", result)

    assert evidence.level is GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE
    assert evidence.proven is False
    assert evidence.gc_backend_parity == (0, 1, 2, 3)
    with pytest.raises(GpuClaimError, match="GPU_LEVEL_6_5GC_PARITY"):
        require_five_gc_parity_or_skip(evidence)


def test_level6_classifier_requires_fence_deferred_native_release():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_scalar_gemm_runtime_source",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_lifetime_backend_record(backend)
            for backend in range(5)
        ],
    }
    result["backend_results"][3]["native_release_after_fence"] = False
    evidence = classify_five_gc_gpu_lifetime_result("tilelang_scalar_gemm", result)

    assert evidence.level is GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE
    assert "fence-deferred native release" in evidence.reason
    with pytest.raises(GpuClaimError, match="GPU_LEVEL_6_5GC_PARITY"):
        require_five_gc_parity_or_skip(evidence)


def test_level6_classifier_accepts_complete_pcc1_five_gc_lifetime_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_scalar_gemm_runtime_source",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_lifetime_backend_record(backend)
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result("tilelang_scalar_gemm", result)
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_8x8_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(backend)
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result("simdgroup_gemm_8x8", result)
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_two_n_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_two_n_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_two_n_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_n",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_two_m_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_two_m_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_two_m_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_m",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_edge_tail_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_transpose_ab_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_transpose_ab_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_transpose_ab_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_transpose_ab_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_transpose_ab_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_transpose_ab_edge_tail_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_splitk_atomic_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_splitk_atomic_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_splitk_atomic_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_splitk_atomic_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_splitk_atomic_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="simdgroup_gemm_four_2d_splitk_atomic_edge_tail_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id=(
                    "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib"
                ),
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id=(
                    "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail_metallib"
                ),
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id=(
                    "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail_metallib"
                ),
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id=(
                    "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail_metallib"
                ),
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_scalar_gemm_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_scalar_gemm_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_scalar_gemm_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_scalar_gemm_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_scalar_gemm_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang GEMM metallib lifetime parity is "
                    "not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_gemm_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_scalar_gemm_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_metal_benchmark_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_local_metal_benchmark_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_local_metal_benchmark_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_local_metal_benchmark_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_local_tilelang_metal_benchmark_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_local_metal_benchmark_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            workload_id,
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime local TileLang Metal benchmark metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_local_metal_benchmark_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        workload_id,
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_matmul_nonroller_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_local_matmul_nonroller_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_local_matmul_nonroller_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_local_matmul_nonroller_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_local_tilelang_matmul_nonroller_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_local_matmul_nonroller_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            workload_id,
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime local TileLang matmul non-roller metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_local_matmul_nonroller_metallib_lifetime_probe(
        tmp_path
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        workload_id,
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_transpose_a_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_transpose_a_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_transpose_a_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_a_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_transpose_a_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_transpose_a_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_transpose_a_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang transpose_A GEMM metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_transpose_a_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_a_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_transpose_b_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_transpose_b_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_transpose_b_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_b_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_transpose_b_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_transpose_b_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_transpose_b_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang transpose_B GEMM metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_transpose_b_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_b_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_transpose_ab_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_transpose_ab_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_transpose_ab_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_ab_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_transpose_ab_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_transpose_ab_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_transpose_ab_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang transpose_AB GEMM metallib lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_transpose_ab_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_ab_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_atomic_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_splitk_atomic_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_splitk_atomic_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_atomic_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_atomic_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_atomic_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K atomic GEMM metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_splitk_atomic_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_atomic_vectorized_c_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_splitk_atomic_vectorized_c_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_splitk_atomic_vectorized_c_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_vectorized_c_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_atomic_vectorized_c_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_atomic_vectorized_c_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_atomic_vectorized_c_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K atomic T.vectorized C "
                    "GEMM metallib lifetime parity is not implemented in this "
                    "default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_splitk_atomic_vectorized_c_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_vectorized_c_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_atomic_ceildiv_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_splitk_atomic_ceildiv_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_splitk_atomic_ceildiv_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_ceildiv_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_atomic_ceildiv_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_atomic_ceildiv_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_atomic_ceildiv_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K atomic ceildiv GEMM "
                    "metallib lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_splitk_atomic_ceildiv_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_ceildiv_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_floor_plus_one_ceildiv_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_splitk_floor_plus_one_ceildiv_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_splitk_floor_plus_one_ceildiv_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_floor_plus_one_ceildiv_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_floor_plus_one_ceildiv_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_floor_plus_one_ceildiv_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_floor_plus_one_ceildiv_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K floor-plus-one ceildiv GEMM "
                    "metallib lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_tilelang_splitk_floor_plus_one_ceildiv_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_floor_plus_one_ceildiv_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_output_staged_gemm_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_output_staged_gemm_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_output_staged_gemm_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_output_staged_gemm_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_output_staged_gemm_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_output_staged_gemm_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_output_staged_gemm_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang output-staged GEMM metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_output_staging_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_output_staged_gemm_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_output_staged_f16_transpose_b_gemm_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_output_staged_f16_transpose_b_gemm_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_output_staged_f16_transpose_b_gemm_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_output_staged_f16_transpose_b_gemm_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_output_staged_f16_transpose_b_gemm_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_output_staged_f16_transpose_b_gemm_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            workload_id,
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang output-staged f16 transpose_B GEMM "
                    "metallib lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_output_staging_f16_transpose_b_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        workload_id,
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_gemmwarp_policy_alias_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_gemmwarp_policy_alias_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_gemmwarp_policy_alias_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_gemmwarp_policy_alias_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_gemmwarp_policy_alias_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_gemmwarp_policy_alias_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            workload_id,
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang GemmWarpPolicy alias metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_gemmwarp_policy_alias_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        workload_id,
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_matmul_static_roller_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_local_matmul_static_roller_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_local_matmul_static_roller_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_local_matmul_static_roller_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_local_tilelang_matmul_static_roller_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_local_matmul_static_roller_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            workload_id,
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime local TileLang matmul static roller-config "
                    "metallib lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_local_matmul_static_roller_metallib_lifetime_probe(
        tmp_path
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        workload_id,
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_swizzled_padded_annotate_layout_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_swizzled_padded_annotate_layout_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_swizzled_padded_annotate_layout_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_swizzled_padded_annotate_layout_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_swizzled_padded_annotate_layout_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_swizzled_padded_annotate_layout_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_swizzled_padded_annotate_layout_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang swizzled padded annotate_layout "
                    "GEMM metallib lifetime parity is not implemented in this "
                    "default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_tilelang_swizzled_padded_annotate_layout_metallib_lifetime_probe(
            tmp_path
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_swizzled_padded_annotate_layout_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_enabled_swizzle_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_enabled_swizzle_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_enabled_swizzle_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_enabled_swizzle_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_enabled_swizzle_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_enabled_swizzle_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_enabled_swizzle_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang enabled use_swizzle GEMM metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_enabled_swizzle_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_enabled_swizzle_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_nonzero_step_pipelined_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_nonzero_step_pipelined_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_nonzero_step_pipelined_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_pipelined_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_nonzero_step_pipelined_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_nonzero_step_pipelined_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_nonzero_step_pipelined_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang nonzero stepped T.Pipelined GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_nonzero_step_pipelined_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_pipelined_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_vectorized_annotations_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_vectorized_annotations_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_vectorized_annotations_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_annotations_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_vectorized_abc_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_vectorized_abc_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_vectorized_abc_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_abc_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_vectorized_a_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_vectorized_a_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_vectorized_a_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_a_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_a_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_a_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_a_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.vectorized A copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_a_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_a_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_vectorized_b_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_vectorized_b_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_vectorized_b_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_b_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_b_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_b_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_b_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.vectorized B copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_b_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_b_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_vectorized_c_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_vectorized_c_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_vectorized_c_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_c_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_c_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_c_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_c_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.vectorized C copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_c_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_c_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_abc_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_abc_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_abc_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.vectorized A/B/C copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_abc_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_abc_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_annotations_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_annotations_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_annotations_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang vectorized annotations GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_annotations_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_annotations_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_a_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_parallel_a_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_parallel_a_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_a_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_parallel_a_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_parallel_a_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_parallel_a_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.Parallel A copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_parallel_a_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_a_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_b_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_parallel_b_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_parallel_b_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_b_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_parallel_b_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_parallel_b_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_parallel_b_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.Parallel B copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_parallel_b_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_b_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_c_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_parallel_c_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_parallel_c_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_c_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_parallel_c_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_parallel_c_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_parallel_c_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.Parallel C copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_parallel_c_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_c_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_ab_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_parallel_ab_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_parallel_ab_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_ab_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_parallel_ab_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_parallel_ab_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_parallel_ab_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.Parallel A/B copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_parallel_ab_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_ab_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_abc_copy_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_parallel_abc_copy_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_parallel_abc_copy_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_abc_copy_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_parallel_abc_copy_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_parallel_abc_copy_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_parallel_abc_copy_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang T.Parallel A/B/C copy GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_parallel_abc_copy_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_parallel_abc_copy_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_nonzero_step_serial_metallib_matrix():
    result = {
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_nonzero_step_serial_metallib",
        "whole_program_gpu": False,
        "backend_results": [
            _level5_metallib_lifetime_backend_record(
                backend,
                workload_id="tilelang_nonzero_step_serial_metallib",
            )
            for backend in range(5)
        ],
    }
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_serial_metallib",
        result,
    )
    checked = require_five_gc_parity_or_skip(evidence)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_nonzero_step_serial_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_nonzero_step_serial_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_nonzero_step_serial_metallib",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang nonzero stepped T.serial GEMM "
                    "metallib lifetime parity is not implemented in this default "
                    "gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_nonzero_step_serial_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_serial_metallib",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_8x8_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_8x8",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime simdgroup metallib lifetime parity is not "
                    "implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_gemm_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_8x8",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_two_n_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_two_n_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_two_n",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime two-N simdgroup metallib lifetime parity "
                    "is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_two_n_gemm_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_n",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_two_m_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_two_m_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_two_m",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime two-M simdgroup metallib lifetime parity "
                    "is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_two_m_gemm_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_m",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D simdgroup metallib lifetime parity "
                    "is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_gemm_metallib_lifetime_probe(tmp_path)
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_edge_tail_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D edge/tail simdgroup metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_edge_tail_gemm_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_transpose_ab_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose_AB simdgroup metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_transpose_ab_gemm_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_transpose_ab_edge_tail_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose_AB edge/tail simdgroup "
                    "metallib lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_four_2d_transpose_ab_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_splitk_atomic_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_splitk_atomic_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_splitk_atomic",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D split-K atomic simdgroup metallib "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_splitk_atomic_gemm_metallib_lifetime_probe(
        tmp_path,
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_splitk_atomic_edge_tail_metallib"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D split-K atomic edge/tail "
                    "simdgroup metallib lifetime parity is not implemented "
                    "in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose_AB split-K atomic "
                    "edge/tail simdgroup metallib lifetime parity is not "
                    "implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail_metallib"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime eight-N transpose_AB split-K atomic "
                    "edge/tail simdgroup metallib lifetime parity is not "
                    "implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail_metallib"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime sixteen transpose_AB split-K atomic "
                    "edge/tail simdgroup metallib lifetime parity is not "
                    "implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail_metallib"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime thirty-two transpose_AB split-K atomic "
                    "edge/tail simdgroup metallib lifetime parity is not "
                    "implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_metallib_lifetime_probe(
            tmp_path,
        )
    )
    backend_results = [
        _run_metallib_lifetime_probe_backend(
            exe,
            backend,
            workload_id=workload_id,
        )
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is False
    assert checked.metallib_produced is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_step_serial_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_step_serial_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_step_serial",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang stepped T.serial Metal lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_step_serial_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_step_serial",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_nonzero_step_serial_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_nonzero_step_serial_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_nonzero_step_serial",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang nonzero stepped T.serial Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_nonzero_step_serial_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_serial",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_step_pipelined_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_step_pipelined_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_step_pipelined",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang stepped T.Pipelined Metal lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_step_pipelined_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_step_pipelined",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_nonzero_step_pipelined_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_nonzero_step_pipelined_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_nonzero_step_pipelined",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang nonzero stepped T.Pipelined Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_nonzero_step_pipelined_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_step_pipelined",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_lifetime_real_or_skipped(tmp_path):
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_scalar_gemm",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime Metal lifetime parity is not implemented in this slice"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend)
        for backend in range(5)
    ]
    owner_result = {
        **pcc_metal_owner_identity_fields(launcher_links_libpython=False),
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": "tilelang_scalar_gemm_runtime_source",
        "whole_program_gpu": False,
        "backend_results": backend_results,
    }
    validate_gpu_owner_identity(
        owner_result,
        requested_gpu_backend=GPU_BACKEND_PCC_METAL,
    )
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_scalar_gemm",
        owner_result,
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_copy_owner_lifetime_real_or_skipped(tmp_path):
    requested_owner = os.environ.get(
        "PCC_TEST_LEVEL5_COPY_GPU_OWNER",
        GPU_BACKEND_PCC_METAL,
    )
    workload_id = (
        "tvm_tilelang_copy_runtime_source"
        if requested_owner == GPU_BACKEND_TVM_TILELANG
        else "copy_runtime_source"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "copy",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-metal copy lifetime parity was not requested"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    # Build and validate the Level-5 launcher once, then execute that exact
    # no-libpython binary under all five runtime collector selections.
    pcc1_metal.test_level5_pcc1_compiled_program_runs_real_runtime_source_copy(
        tmp_path
    )
    exe = tmp_path / "pcc1_real_metal_runtime_source_copy"
    assert exe.is_file()
    backend_results = []
    for backend in range(5):
        env = os.environ.copy()
        env.pop("LC_ALL", None)
        env["PCC_GC_BACKEND"] = str(backend)
        run = subprocess.run(
            [str(exe)],
            text=True,
            capture_output=True,
            timeout=90,
            env=env,
        )
        assert run.returncode == 0, (
            f"{requested_owner} copy failed under PCC_GC_BACKEND={backend} "
            f"(exit {run.returncode})\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
        stdout = run.stdout.strip()
        if stdout == "903":
            pytest.skip("MTLCreateSystemDefaultDevice returned nil")
        assert stdout.splitlines() == [str(48 + backend), "0"]
        backend_results.append(
            _level5_lifetime_backend_record(backend, workload_id=workload_id)
        )

    if requested_owner == GPU_BACKEND_PCC_METAL:
        owner_identity = pcc_metal_owner_identity_fields(
            launcher_links_libpython=False
        )
        provider_dependencies = None
    elif requested_owner == GPU_BACKEND_TVM_TILELANG:
        owner_identity = tvm_tilelang_owner_identity_fields(
            launcher_links_libpython=False,
            provider_identity=TVM_TILELANG_PROVIDER_IDENTITY,
            pipeline=TVM_TILELANG_PIPELINE,
        )
        config = load_tvm_tilelang_provider_config()
        provider_dependencies = {
            "root": config.root,
            "python": config.python,
            "site_packages": config.site_packages,
            "pin": dict(config.pin),
        }
    else:
        pytest.fail(f"unsupported explicit Level-6 GPU owner {requested_owner!r}")
    owner_result = {
        **owner_identity,
        "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
        "workload_id": workload_id,
        "whole_program_gpu": False,
        "backend_results": backend_results,
        "provider_dependencies": provider_dependencies,
    }
    validate_gpu_owner_identity(
        owner_result,
        requested_gpu_backend=requested_owner,
    )
    evidence = classify_five_gc_gpu_lifetime_result("copy", owner_result)
    checked = require_five_gc_parity_or_skip(evidence, strict=True)
    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tvm_tilelang_copy_owner_lifetime_real_or_skipped(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "PCC_TEST_LEVEL5_COPY_GPU_OWNER",
        GPU_BACKEND_TVM_TILELANG,
    )
    test_gpu_level6_five_gc_copy_owner_lifetime_real_or_skipped(tmp_path)


def test_gpu_level6_five_gc_simdgroup_lifetime_real_or_skipped(tmp_path):
    workload_id = "simdgroup_gemm_8x8_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_8x8",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime simdgroup Metal lifetime parity is not implemented "
                    "in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_8x8",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_two_n_lifetime_real_or_skipped(tmp_path):
    workload_id = "simdgroup_gemm_two_n_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_two_n",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime two-N simdgroup Metal lifetime parity is "
                    "not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_two_n_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_n",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_two_m_lifetime_real_or_skipped(tmp_path):
    workload_id = "simdgroup_gemm_two_m_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_two_m",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime two-M simdgroup Metal lifetime parity is "
                    "not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_two_m_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_two_m",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_lifetime_real_or_skipped(tmp_path):
    workload_id = "simdgroup_gemm_four_2d_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D simdgroup Metal lifetime parity is "
                    "not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_transpose_ab_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose simdgroup Metal lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_transpose_ab_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_edge_tail_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D edge-tail simdgroup Metal lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_edge_tail_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_transpose_ab_edge_tail_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose edge-tail simdgroup "
                    "Metal lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_transpose_ab_edge_tail_gemm_lifetime_probe(
        tmp_path
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_splitk_atomic_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_splitk_atomic_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_splitk_atomic",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D split-K atomic simdgroup Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_splitk_atomic_gemm_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_splitk_atomic_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "simdgroup_gemm_four_2d_splitk_atomic_edge_tail_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D split-K atomic edge-tail "
                    "simdgroup Metal lifetime parity is not implemented in this "
                    "default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_simdgroup_four_2d_splitk_atomic_edge_tail_gemm_lifetime_probe(
        tmp_path
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail_runtime_source"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime four-2D transpose split-K atomic edge-tail "
                    "simdgroup Metal lifetime parity is not implemented in this "
                    "default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
            tmp_path
        )
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_four_2d_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail_runtime_source"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime eight-simdgroup transpose split-K atomic "
                    "edge-tail simdgroup Metal lifetime parity is not implemented "
                    "in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
            tmp_path
        )
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_eight_n_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail_runtime_source"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime sixteen-simdgroup transpose split-K atomic "
                    "edge-tail simdgroup Metal lifetime parity is not implemented "
                    "in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_sixteen_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
            tmp_path
        )
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_sixteen_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = (
        "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail_runtime_source"
    )
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime thirty-two-simdgroup transpose split-K atomic "
                    "edge-tail simdgroup Metal lifetime parity is not implemented "
                    "in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = (
        _build_pcc1_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_gemm_lifetime_probe(
            tmp_path
        )
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "simdgroup_gemm_thirty_two_transpose_ab_splitk_atomic_edge_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_nonzero_serial_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_nonzero_serial_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_nonzero_serial",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang vectorized nonzero-serial Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_nonzero_serial_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_nonzero_serial",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_vectorized_annotations_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_vectorized_annotations_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_vectorized_annotations",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang vectorized annotations Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_vectorized_annotations_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_vectorized_annotations",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_atomic_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_atomic_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_atomic",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K atomic Metal lifetime parity "
                    "is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_splitk_atomic_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_splitk_atomic_ceildiv_tail_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_splitk_atomic_ceildiv_tail_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_splitk_atomic_ceildiv_tail",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang split-K atomic ceildiv-tail Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_splitk_atomic_ceildiv_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_splitk_atomic_ceildiv_tail",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_transpose_ab_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_transpose_ab_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_transpose_ab",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang transpose_A+transpose_B Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_transpose_ab_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_transpose_ab",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_swizzled_padded_annotate_layout_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_swizzled_padded_annotate_layout_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_swizzled_padded_annotate_layout",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang swizzled padded annotate_layout "
                    "Metal lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_swizzled_padded_annotate_layout_lifetime_probe(
        tmp_path
    )
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_swizzled_padded_annotate_layout",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_enabled_swizzle_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_enabled_swizzle_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_enabled_swizzle",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang enabled use_swizzle Metal lifetime "
                    "parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_enabled_swizzle_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_enabled_swizzle",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True


def test_gpu_level6_five_gc_tilelang_nonzero_start_pipelined_lifetime_real_or_skipped(
    tmp_path,
):
    workload_id = "tilelang_nonzero_start_pipelined_runtime_source"
    if os.environ.get("PCC_RUN_GPU_5GC_LIFETIME") != "1":
        evidence = classify_five_gc_gpu_lifetime_result(
            "tilelang_nonzero_start_pipelined",
            {
                "status": STATUS_SKIPPED_WITH_REASON,
                "reason": (
                    "SKIPPED_WITH_REASON: PCC_RUN_GPU_5GC_LIFETIME=1 is not set; "
                    "real pcc-runtime TileLang nonzero-start Pipelined Metal "
                    "lifetime parity is not implemented in this default gate"
                ),
                "whole_program_gpu": False,
            },
        )
        checked = require_five_gc_parity_or_skip(evidence)
        assert checked.reason.startswith("SKIPPED_WITH_REASON:")
        return

    exe = _build_pcc1_tilelang_nonzero_start_pipelined_lifetime_probe(tmp_path)
    backend_results = [
        _run_lifetime_probe_backend(exe, backend, workload_id=workload_id)
        for backend in range(5)
    ]
    evidence = classify_five_gc_gpu_lifetime_result(
        "tilelang_nonzero_start_pipelined",
        {
            "status": STATUS_GPU_5GC_LIFETIME_EXECUTED,
            "workload_id": workload_id,
            "whole_program_gpu": False,
            "backend_results": backend_results,
        },
    )
    checked = require_five_gc_parity_or_skip(evidence, strict=True)

    assert checked.level is GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    assert checked.gc_backend_parity == (0, 1, 2, 3, 4)
    assert checked.pcc1_native_executed is True
    assert checked.runtime_launch_executed is True
    assert checked.fence_completed is True
