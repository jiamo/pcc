#!/usr/bin/env python3
"""Pinned out-of-process TileLang/TVM Metal codegen provider.

This process accepts only pcc's canonical frozen plain-TIR JSON.  It supports
the bounded copy and tiled-GEMM owner slice, runs the explicit TileLang Metal
pipeline, and returns the provider-produced Metal source plus a dependency
manifest.  It intentionally does not import pcc and never selects a fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


REQUEST_SCHEMA = "pcc-tvm-tilelang-provider-request-v1"
RESPONSE_SCHEMA = "pcc-tvm-tilelang-provider-response-v1"
BACKEND = "tvm-tilelang"
TARGET = "metal"
PIPELINE = "tilelang-metal-v1"
PLAIN_TIR_MARKER = "plain_tir_freeze"

# This is the ordered, audited provider pipeline identity.  TileLang groups
# some implementation passes behind helpers; the names here describe the
# externally pinned semantic stages and are part of the request allowlist.
ORDERED_PASSES = (
    "BindTarget",
    "MaterializeKernelLaunch",
    "AddWrapperForSingleBufStore",
    "LegalizeNegativeIndex",
    "InjectAssumes",
    "Simplify",
    "LayoutReducer",
    "IfStmtBinding",
    "StripMetalSoftwarePipeline",
    "PipelinePlanning",
    "InjectSoftwarePipeline",
    "MetalBypassReadonlyShared",
    "MetalFragmentToSimdgroup",
    "LayoutInference",
    "LowerTileOp",
    "DecoupleTypeCast",
    "LegalizeVectorizedLoop",
    "LegalizeSafeMemoryAccess",
    "LowerAccessPtr",
    "PlanAndUpdateBufferAllocationLocation",
    "HoistGlobalBufferAllocations",
    "LowerOpaqueBlock",
    "NarrowDataType32",
    "FlattenBuffer",
    "ConfigIndexBitwidth",
    "VectorizeLoop",
    "StorageRewrite",
    "LoopUnswitching",
    "UnrollLoop",
    "RenormalizeSplitPattern",
    "RemoveNoOp",
    "HoistIfThenElse",
    "VerifyMemory",
    "AnnotateEntryFunc",
    "InferFragment",
    "LowerThreadAllreduce",
    "AnnotateDeviceRegions",
    "SplitHostDevice",
    "AnnotateReadOnlyParams",
    "MergeSharedMemoryAllocations",
    "ThreadSyncShared",
    "MergeIfStmt",
    "MakePackedAPI",
    "LowerDeviceKernelLaunch",
    "LowerIntrin",
    "HoistBroadcastValues",
    "target.build.tilelang_metal",
)


class ProviderError(ValueError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderError(f"{label} must be an array")
    return value


def _require_static_shape(record: dict[str, Any], label: str) -> tuple[int, ...]:
    shape = _require_list(record.get("shape"), f"{label}.shape")
    if not shape or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in shape):
        raise ProviderError(f"{label}.shape must contain positive integers")
    return tuple(shape)


def _records_by_name(records: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(records):
        record = _require_dict(raw, f"{label}[{index}]")
        name = record.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ProviderError(f"{label}[{index}] has an invalid or duplicate name")
        result[name] = record
    return result


def _validate_common_request(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_top = {
        "schema",
        "backend",
        "target",
        "pipeline",
        "ordered_passes",
        "canonical_frozen_ir_sha256",
        "frozen_module",
        "pin",
    }
    if set(request) != expected_top:
        raise ProviderError(f"request fields must be exactly {sorted(expected_top)}")
    if request.get("schema") != REQUEST_SCHEMA:
        raise ProviderError("unsupported provider request schema")
    if request.get("backend") != BACKEND:
        raise ProviderError("provider backend mismatch; no fallback")
    if request.get("target") != TARGET:
        raise ProviderError("provider supports only target='metal'; no fallback")
    if request.get("pipeline") != PIPELINE:
        raise ProviderError("provider pass pipeline is not allowlisted; no fallback")
    if tuple(request.get("ordered_passes") or ()) != ORDERED_PASSES:
        raise ProviderError("provider ordered pass list mismatch; no fallback")
    frozen = _require_dict(request.get("frozen_module"), "frozen_module")
    if frozen.get("marker") != PLAIN_TIR_MARKER or frozen.get("plain_tir") is not True:
        raise ProviderError("provider requires canonical pcc plain-TIR freeze input")
    if frozen.get("target") != TARGET:
        raise ProviderError("frozen module target must be metal; no fallback")
    actual_digest = _sha256_bytes(_canonical_json(frozen))
    if request.get("canonical_frozen_ir_sha256") != actual_digest:
        raise ProviderError("canonical frozen-IR hash mismatch")
    funcs = _require_list(frozen.get("funcs"), "frozen_module.funcs")
    if len(funcs) != 1:
        raise ProviderError("provider v1 accepts exactly one kernel function")
    return frozen, _require_dict(funcs[0], "frozen_module.funcs[0]")


def _validate_pin(root: Path, pin: dict[str, Any]) -> dict[str, str]:
    required = {
        "provider_identity",
        "tilelang_revision",
        "tilelang_version",
        "tvm_revision",
        "tvm_version",
        "source_hashes",
        "library_hashes",
    }
    if set(pin) != required:
        raise ProviderError(f"pin fields must be exactly {sorted(required)}")
    observed: dict[str, str] = {}
    for kind, base in (("source_hashes", root), ("library_hashes", root / "build" / "lib")):
        records = _require_dict(pin.get(kind), f"pin.{kind}")
        for relative, expected in sorted(records.items()):
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ProviderError(f"pin.{kind} must map paths to SHA-256 strings")
            relative_path = Path(relative)
            resolved_base = base.resolve()
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ProviderError(f"pin.{kind} path escapes its pinned root")
            path = (resolved_base / relative_path).resolve()
            if not path.is_relative_to(resolved_base):
                raise ProviderError(f"pin.{kind} path escapes its pinned root")
            if not path.is_file():
                raise ProviderError(f"pinned provider dependency is missing: {path}")
            digest = _sha256_file(path)
            if digest != expected:
                raise ProviderError(f"pinned provider dependency hash mismatch: {path}")
            observed[str(path)] = digest
    return observed


def _validate_copy(func: dict[str, Any]) -> dict[str, Any] | None:
    params = _records_by_name(_require_list(func.get("params"), "func.params"), "func.params")
    locals_ = _require_list(func.get("locals"), "func.locals")
    ops = _require_list(func.get("ops"), "func.ops")
    if locals_:
        return None
    copy_ops = [op for op in ops if isinstance(op, dict) and op.get("tir_op") == "tir.copy_loop"]
    other = [
        op
        for op in ops
        if not isinstance(op, dict)
        or op.get("tir_op") not in {"tir.copy_loop", "tir.parallel_for"}
    ]
    if len(copy_ops) != 1 or other:
        return None
    args = copy_ops[0].get("args")
    if not isinstance(args, list) or len(args) != 2:
        raise ProviderError("copy op must name source and destination")
    src = params.get(args[0])
    dst = params.get(args[1])
    if src is None or dst is None:
        raise ProviderError("copy operands must be global parameters")
    if src.get("kind") != "buffer" or dst.get("kind") != "buffer":
        raise ProviderError("copy operands must be buffers")
    if src.get("dtype") != "f32" or dst.get("dtype") != "f32":
        raise ProviderError("provider v1 copy supports only f32 buffers")
    if src.get("scope") != "global" or dst.get("scope") != "global":
        raise ProviderError("copy buffers must use global scope")
    src_shape = _require_static_shape(src, "copy source")
    if _require_static_shape(dst, "copy destination") != src_shape:
        raise ProviderError("copy source and destination shapes must match")
    scalar_params = [p for p in params.values() if p.get("kind") == "scalar"]
    if len(scalar_params) > 1:
        raise ProviderError("provider v1 copy accepts at most one scalar extent")
    if scalar_params and scalar_params[0].get("dtype") != "u32":
        raise ProviderError("provider v1 copy scalar extent must be u32")
    if len(src_shape) not in (1, 2):
        raise ProviderError("provider v1 copy supports rank-1 or rank-2 buffers")
    return {
        "kind": "copy",
        "shape": src_shape,
        "threads": func.get("threads"),
        "has_extent": bool(scalar_params),
    }


def _validate_gemm(func: dict[str, Any]) -> dict[str, Any] | None:
    ops = _require_list(func.get("ops"), "func.ops")
    gemm_ops = [op for op in ops if isinstance(op, dict) and op.get("tir_op") == "tir.gemm_expand"]
    if not gemm_ops:
        return None
    if len(gemm_ops) != 1:
        raise ProviderError("provider v1 accepts exactly one GEMM op")
    params = _records_by_name(_require_list(func.get("params"), "func.params"), "func.params")
    locals_ = _records_by_name(_require_list(func.get("locals"), "func.locals"), "func.locals")
    args = gemm_ops[0].get("args")
    if not isinstance(args, list) or len(args) != 3:
        raise ProviderError("GEMM op must name A_shared, B_shared, C_local")
    a_local, b_local, c_local = (locals_.get(name) for name in args)
    if a_local is None or b_local is None or c_local is None:
        raise ProviderError("GEMM operands must be device-local buffers")
    global_buffers = [p for p in params.values() if p.get("kind") == "buffer"]
    if len(global_buffers) != 3 or any(p.get("scope") != "global" for p in global_buffers):
        raise ProviderError("provider v1 GEMM requires exactly three global buffers")
    by_name = {p["name"]: p for p in global_buffers}
    if set(by_name) != {"A", "B", "C"}:
        raise ProviderError("provider v1 GEMM requires global buffers A, B, C")
    if (by_name["A"].get("dtype"), by_name["B"].get("dtype"), by_name["C"].get("dtype")) != ("f16", "f16", "f32"):
        raise ProviderError("provider v1 GEMM requires f16 A/B and f32 C")
    a_shape = _require_static_shape(by_name["A"], "A")
    b_shape = _require_static_shape(by_name["B"], "B")
    c_shape = _require_static_shape(by_name["C"], "C")
    if len(a_shape) != 2 or len(b_shape) != 2 or len(c_shape) != 2:
        raise ProviderError("provider v1 GEMM requires rank-2 tensors")
    m, k = a_shape
    bk, n = b_shape
    if bk != k or c_shape != (m, n):
        raise ProviderError("GEMM tensor shapes must be A(M,K), B(K,N), C(M,N)")
    block_m, block_k = _require_static_shape(a_local, "A_shared")
    local_bk, block_n = _require_static_shape(b_local, "B_shared")
    if local_bk != block_k or _require_static_shape(c_local, "C_local") != (block_m, block_n):
        raise ProviderError("GEMM tile shapes are inconsistent")
    if a_local.get("scope") != "shared" or b_local.get("scope") != "shared" or c_local.get("scope") != "fragment":
        raise ProviderError("GEMM locals must use shared/shared/fragment scopes")
    allowed_ops = {"tir.fill_loop", "tir.copy_loop", "tir.gemm_expand"}
    if any(not isinstance(op, dict) or op.get("tir_op") not in allowed_ops for op in ops):
        raise ProviderError("provider v1 GEMM contains an unsupported operation")
    if any(bool(gemm_ops[0].get("attrs", {}).get(key)) for key in ("transpose_A", "transpose_B")):
        raise ProviderError("provider v1 GEMM does not support transpose semantics")
    return {
        "kind": "gemm",
        "m": m,
        "n": n,
        "k": k,
        "block_m": block_m,
        "block_n": block_n,
        "block_k": block_k,
        "threads": func.get("threads"),
    }


def _validate_semantics(func: dict[str, Any]) -> dict[str, Any]:
    threads = func.get("threads")
    if isinstance(threads, bool) or not isinstance(threads, int) or threads <= 0:
        raise ProviderError("kernel thread count must be a positive integer")
    if not isinstance(func.get("name"), str) or not func["name"]:
        raise ProviderError("kernel function must have a name")
    copy = _validate_copy(func)
    if copy is not None:
        return copy
    gemm = _validate_gemm(func)
    if gemm is not None:
        return gemm
    raise ProviderError("provider v1 supports only bounded copy and tiled GEMM; no fallback")


def _copy_1d(shape: tuple[int, ...], threads: int, has_extent: bool):
    import tilelang.language as T

    n = shape[0]
    if has_extent:
        @T.prim_func
        def copy_kernel(src: T.Tensor((n,), "float32"), dst: T.Tensor((n,), "float32"), extent: T.uint32):
            with T.Kernel(1, threads=threads) as _bx:
                for i in T.Parallel(n):
                    dst[i] = src[i]
    else:
        @T.prim_func
        def copy_kernel(src: T.Tensor((n,), "float32"), dst: T.Tensor((n,), "float32")):
            with T.Kernel(1, threads=threads) as _bx:
                for i in T.Parallel(n):
                    dst[i] = src[i]
    return copy_kernel


def _copy_2d(shape: tuple[int, ...], threads: int, has_extent: bool):
    import tilelang.language as T

    rows, cols = shape
    if has_extent:
        @T.prim_func
        def copy_kernel(src: T.Tensor((rows, cols), "float32"), dst: T.Tensor((rows, cols), "float32"), extent: T.uint32):
            with T.Kernel(1, threads=threads) as _bx:
                for i, j in T.Parallel(rows, cols):
                    dst[i, j] = src[i, j]
    else:
        @T.prim_func
        def copy_kernel(src: T.Tensor((rows, cols), "float32"), dst: T.Tensor((rows, cols), "float32")):
            with T.Kernel(1, threads=threads) as _bx:
                for i, j in T.Parallel(rows, cols):
                    dst[i, j] = src[i, j]
    return copy_kernel


def _gemm(spec: dict[str, Any]):
    import tilelang.language as T

    m = spec["m"]
    n = spec["n"]
    k = spec["k"]
    block_m = spec["block_m"]
    block_n = spec["block_n"]
    block_k = spec["block_k"]
    threads = spec["threads"]

    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((m, k), "float16"),
        B: T.Tensor((k, n), "float16"),
        C: T.Tensor((m, n), "float32"),
    ):
        with T.Kernel(T.ceildiv(n, block_n), T.ceildiv(m, block_m), threads=threads) as (bx, by):
            A_shared = T.alloc_shared((block_m, block_k), "float16")
            B_shared = T.alloc_shared((block_k, block_n), "float16")
            C_local = T.alloc_fragment((block_m, block_n), "float32")
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(k, block_k), num_stages=0):
                T.copy(A[by * block_m, ko * block_k], A_shared)
                T.copy(B[ko * block_k, bx * block_n], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_m, bx * block_n])
    return gemm_kernel


def _compile(spec: dict[str, Any]) -> tuple[str, str]:
    import tilelang
    import tvm

    if not tvm.get_global_func("target.build.tilelang_metal", allow_missing=True):
        raise ProviderError("pinned TileLang Metal codegen is unavailable; no fallback")
    # TileLang's eager builder consults the active target while constructing
    # T.Parallel/T.Kernel nodes and some lowering helpers consult it again.
    with tvm.target.Target(TARGET):
        if spec["kind"] == "copy":
            factory = _copy_1d if len(spec["shape"]) == 1 else _copy_2d
            prim_func = factory(spec["shape"], spec["threads"], spec["has_extent"])
        elif spec["kind"] == "gemm":
            prim_func = _gemm(spec)
        else:  # pragma: no cover - validation owns this boundary
            raise ProviderError("unsupported provider semantic kind")
        artifact = tilelang.lower(
            prim_func,
            target=TARGET,
            enable_host_codegen=False,
            enable_device_compile=False,
        )
    source = artifact.kernel_source
    if not isinstance(source, str) or "kernel void" not in source:
        raise ProviderError("TileLang provider did not produce Metal kernel source")
    return source, str(getattr(prim_func, "attrs", {}).get("global_symbol", ""))


def _load_provider(root: Path, site_packages: Path) -> tuple[Any, Any, Any]:
    if "PYTHONPATH" in os.environ or "PYTHONSTARTUP" in os.environ:
        raise ProviderError("ambient Python import configuration is forbidden")
    if not root.is_dir() or not site_packages.is_dir():
        raise ProviderError("pinned provider root or site-packages directory is unavailable")
    tvm_python = root / "3rdparty" / "tvm" / "python"
    if not tvm_python.is_dir():
        raise ProviderError("pinned vendored TVM Python tree is unavailable")
    sys.path[:0] = [str(root), str(tvm_python), str(site_packages)]
    os.environ["TVM_LIBRARY_PATH"] = str(root / "build" / "lib")
    os.environ["DYLD_LIBRARY_PATH"] = str(root / "build" / "lib")
    os.environ.pop("TILELANG_PASS_DIFF", None)
    import tilelang
    import tvm
    import tvm_ffi

    return tilelang, tvm, tvm_ffi


def _module_path(module: Any) -> str:
    return str(Path(module.__file__).resolve())


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--site-packages", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    response_path = Path(args.response).resolve()
    root = Path(args.root).resolve()
    site_packages = Path(args.site_packages).resolve()
    request = _require_dict(json.loads(request_path.read_text(encoding="utf-8")), "request")
    frozen, func = _validate_common_request(request)
    pin = _require_dict(request["pin"], "pin")
    dependency_hashes = _validate_pin(root, pin)
    tilelang, tvm, tvm_ffi = _load_provider(root, site_packages)
    if tilelang.__version__ != pin["tilelang_version"]:
        raise ProviderError("pinned TileLang version mismatch")
    if getattr(tvm, "__version__", None) != pin["tvm_version"]:
        raise ProviderError("pinned TVM version mismatch")
    if _module_path(tilelang) != str((root / "tilelang" / "__init__.py").resolve()):
        raise ProviderError("ambient TileLang package was imported")
    if _module_path(tvm) != str((root / "3rdparty" / "tvm" / "python" / "tvm" / "__init__.py").resolve()):
        raise ProviderError("ambient TVM package was imported")

    spec = _validate_semantics(func)
    source, provider_entry = _compile(spec)
    source_digest = _sha256_bytes(source.encode("utf-8"))
    response = {
        "schema": RESPONSE_SCHEMA,
        "status": "compiled",
        "backend": BACKEND,
        "target": TARGET,
        "pipeline": PIPELINE,
        "ordered_passes": list(ORDERED_PASSES),
        "canonical_frozen_ir_sha256": request["canonical_frozen_ir_sha256"],
        "semantic_kind": spec["kind"],
        "logical_entry": func["name"],
        "provider_entry": provider_entry,
        "metal_source": source,
        "metal_source_sha256": source_digest,
        "provider_identity": pin["provider_identity"],
        "tilelang_revision": pin["tilelang_revision"],
        "tilelang_version": tilelang.__version__,
        "tvm_revision": pin["tvm_revision"],
        "tvm_version": tvm.__version__,
        "provider_process_links_libpython": True,
        "dependencies": {
            "python_executable": str(Path(sys.executable).resolve()),
            "tilelang_module": _module_path(tilelang),
            "tvm_module": _module_path(tvm),
            "tvm_ffi_module": _module_path(tvm_ffi),
            "content_sha256": dependency_hashes,
        },
        "diagnostics": [],
        "fallback_used": False,
    }
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_bytes(_canonical_json(response) + b"\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except Exception as exc:
        print(f"pcc tvm-tilelang provider error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
