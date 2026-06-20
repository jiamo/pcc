"""Pinned out-of-process TVM/TileLang Metal owner provider client.

The provider is deliberately separate from ordinary ``import tilelang``.  pcc
hashes canonical frozen IR, invokes one pinned checkout under an isolated
Python process, validates the response, and keeps all dependencies visible in
the returned artifact record.  No path in this module probes another backend.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pcc.kernel_ir.tirx_adapter import PlainTirModule


REQUEST_SCHEMA = "pcc-tvm-tilelang-provider-request-v1"
RESPONSE_SCHEMA = "pcc-tvm-tilelang-provider-response-v1"
TVM_TILELANG_PIPELINE = "tilelang-metal-v1"
TVM_TILELANG_PROVIDER_IDENTITY = "pcc-tvm-tilelang-metal-owner-v1"

# Must match the provider's audited ordered list exactly.  Keeping the list on
# both sides turns an accidental provider pipeline change into a hard protocol
# error rather than an ambient registration change.
TVM_TILELANG_ORDERED_PASSES = (
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


class TvmTilelangProviderError(ValueError):
    """The requested provider was unavailable or violated its protocol."""


@dataclass(frozen=True)
class TvmTilelangProviderConfig:
    root: str
    python: str
    site_packages: str
    provider_script: str
    pin: Mapping[str, Any]


@dataclass(frozen=True)
class TvmTilelangCompileResult:
    backend: str
    target: str
    pipeline: str
    ordered_passes: tuple[str, ...]
    provider_identity: str
    canonical_frozen_ir_sha256: str
    semantic_kind: str
    logical_entry: str
    provider_entry: str
    provider_metal_source: str
    provider_metal_source_sha256: str
    metal_source: str
    metal_source_sha256: str
    tilelang_revision: str
    tilelang_version: str
    tvm_revision: str
    tvm_version: str
    provider_process_links_libpython: bool
    dependencies: Mapping[str, Any]
    diagnostics: tuple[str, ...]
    request_path: str
    response_path: str
    provider_source_path: str
    source_path: str

    def artifact_hashes(self) -> dict[str, str]:
        hashes = {
            "canonical_frozen_ir": self.canonical_frozen_ir_sha256,
            "provider_metal_source": self.provider_metal_source_sha256,
            "pcc_abi_adapted_metal_source": self.metal_source_sha256,
        }
        content = self.dependencies.get("content_sha256")
        if isinstance(content, Mapping):
            for path, digest in content.items():
                if isinstance(path, str) and isinstance(digest, str):
                    hashes["provider_dependency." + Path(path).name] = digest
        return hashes


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pin_path() -> Path:
    return Path(__file__).with_name("tvm_tilelang_provider_pin.json")


def load_tvm_tilelang_provider_config(
    *,
    environ: Mapping[str, str] | None = None,
) -> TvmTilelangProviderConfig:
    env = os.environ if environ is None else environ
    raw = json.loads(_pin_path().read_text(encoding="utf-8"))
    if raw.get("schema") != "pcc-tvm-tilelang-provider-pin-v1":
        raise TvmTilelangProviderError("unsupported TVM/TileLang provider pin schema")
    pin_keys = (
        "provider_identity",
        "tilelang_revision",
        "tilelang_version",
        "tvm_revision",
        "tvm_version",
        "source_hashes",
        "library_hashes",
    )
    pin = {key: raw[key] for key in pin_keys}
    root = env.get("PCC_TVM_TILELANG_ROOT", raw["default_root"])
    python = env.get("PCC_TVM_TILELANG_PYTHON", raw["default_python"])
    site_packages = env.get(
        "PCC_TVM_TILELANG_SITE_PACKAGES",
        raw["default_site_packages"],
    )
    return TvmTilelangProviderConfig(
        root=str(Path(root).expanduser().resolve()),
        python=str(Path(python).expanduser().resolve()),
        site_packages=str(Path(site_packages).expanduser().resolve()),
        provider_script=str(
            (_repo_root() / "scripts" / "tvm_tilelang_owner_provider.py").resolve()
        ),
        pin=pin,
    )


def _validate_config(config: TvmTilelangProviderConfig) -> None:
    paths = {
        "provider root": Path(config.root),
        "provider Python": Path(config.python),
        "provider site-packages": Path(config.site_packages),
        "provider entrypoint": Path(config.provider_script),
    }
    for label, path in paths.items():
        exists = path.is_dir() if label in {"provider root", "provider site-packages"} else path.is_file()
        if not exists:
            raise TvmTilelangProviderError(
                f"pinned {label} is unavailable at {path}; no fallback"
            )
    if config.pin.get("provider_identity") != TVM_TILELANG_PROVIDER_IDENTITY:
        raise TvmTilelangProviderError("TVM/TileLang provider identity pin mismatch")


def _provider_env(config: TvmTilelangProviderConfig) -> dict[str, str]:
    root = Path(config.root)
    env = {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TVM_LIBRARY_PATH": str(root / "build" / "lib"),
        "DYLD_LIBRARY_PATH": str(root / "build" / "lib"),
    }
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        env["TMPDIR"] = tmpdir
    return env


def _run_provider(
    command: list[str],
    *,
    config: TvmTilelangProviderConfig,
    timeout: float,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_provider_env(config),
        cwd=config.root,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=2.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise TvmTilelangProviderError(
            f"pinned TVM/TileLang provider timed out after {timeout:.1f}s; no fallback"
        ) from exc
    finally:
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TvmTilelangProviderError(f"provider response {label} must be an object")
    return value


_KERNEL_ENTRY_RE = re.compile(r"\bkernel\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_BUFFER_BINDING_RE = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\[\[\s*buffer\((?P<index>[0-9]+)\)\s*\]\]"
)


def _adapt_provider_metal_source(
    source: str,
    *,
    plain: PlainTirModule,
) -> tuple[str, str]:
    """Re-import the bounded provider source into pcc's launch ABI.

    TileLang may order output buffers before inputs and appends ``_kernel`` to
    device entries.  pcc owns packed-argument indices and entry identity, so
    this adapter permits only those two mechanical changes.  The provider
    remains the codegen owner; raw and adapted source are both retained.
    """
    entries = _KERNEL_ENTRY_RE.findall(source)
    if len(entries) != 1:
        raise TvmTilelangProviderError(
            "provider source must contain exactly one Metal kernel entry"
        )
    if len(plain.funcs) != 1:
        raise TvmTilelangProviderError("provider ABI adapter requires one plain-TIR func")
    func = plain.funcs[0]
    logical_entry = func.get("name")
    params = func.get("params")
    if not isinstance(logical_entry, str) or not logical_entry:
        raise TvmTilelangProviderError("plain-TIR function has no logical entry")
    if not isinstance(params, list):
        raise TvmTilelangProviderError("plain-TIR function has no parameter list")
    param_indices: dict[str, int] = {}
    required_buffers: set[str] = set()
    for index, raw in enumerate(params):
        if not isinstance(raw, Mapping):
            raise TvmTilelangProviderError("plain-TIR parameter is not a record")
        name = raw.get("name")
        if not isinstance(name, str) or not name or name in param_indices:
            raise TvmTilelangProviderError("plain-TIR parameter name is invalid")
        param_indices[name] = index
        if raw.get("kind") == "buffer":
            required_buffers.add(name)

    seen_bindings: set[str] = set()

    def replace_binding(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in param_indices:
            raise TvmTilelangProviderError(
                f"provider source contains unknown buffer binding {name!r}"
            )
        if name in seen_bindings:
            raise TvmTilelangProviderError(
                f"provider source repeats buffer binding {name!r}"
            )
        seen_bindings.add(name)
        return f"{name} [[ buffer({param_indices[name]}) ]]"

    adapted = _BUFFER_BINDING_RE.sub(replace_binding, source)
    missing = sorted(required_buffers - seen_bindings)
    if missing:
        raise TvmTilelangProviderError(
            f"provider source omitted required buffer binding(s) {missing}"
        )
    provider_entry = entries[0]
    adapted = _KERNEL_ENTRY_RE.sub(
        f"kernel void {logical_entry}(",
        adapted,
        count=1,
    )
    if len(_KERNEL_ENTRY_RE.findall(adapted)) != 1:
        raise TvmTilelangProviderError("adapted provider source lost its kernel entry")
    return adapted, provider_entry


def _validate_response(
    response: Mapping[str, Any],
    *,
    expected_digest: str,
    config: TvmTilelangProviderConfig,
) -> None:
    expected = {
        "schema": RESPONSE_SCHEMA,
        "status": "compiled",
        "backend": "tvm-tilelang",
        "target": "metal",
        "pipeline": TVM_TILELANG_PIPELINE,
        "canonical_frozen_ir_sha256": expected_digest,
        "provider_identity": TVM_TILELANG_PROVIDER_IDENTITY,
        "fallback_used": False,
        "provider_process_links_libpython": True,
        "tilelang_revision": config.pin["tilelang_revision"],
        "tilelang_version": config.pin["tilelang_version"],
        "tvm_revision": config.pin["tvm_revision"],
        "tvm_version": config.pin["tvm_version"],
    }
    for key, value in expected.items():
        if response.get(key) != value:
            raise TvmTilelangProviderError(
                f"provider response field {key!r} did not match the pinned request"
            )
    if tuple(response.get("ordered_passes") or ()) != TVM_TILELANG_ORDERED_PASSES:
        raise TvmTilelangProviderError("provider response pass pipeline mismatch")
    source = response.get("metal_source")
    if not isinstance(source, str) or "kernel void" not in source:
        raise TvmTilelangProviderError("provider response contains no Metal kernel source")
    if response.get("metal_source_sha256") != _sha256_bytes(source.encode("utf-8")):
        raise TvmTilelangProviderError("provider Metal source hash mismatch")
    dependencies = _require_mapping(response.get("dependencies"), "dependencies")
    expected_python = str(Path(config.python).resolve())
    actual_python = dependencies.get("python_executable")
    # A virtualenv executable may resolve through its interpreter symlink.  The
    # pin records the configured launcher and the response records the resolved
    # binary; both remain visible instead of pretending they are identical.
    if not isinstance(actual_python, str) or not Path(actual_python).is_file():
        raise TvmTilelangProviderError(
            f"provider did not disclose a valid Python dependency for {expected_python}"
        )
    _require_mapping(dependencies.get("content_sha256"), "dependency hashes")


def compile_with_tvm_tilelang_provider(
    plain: PlainTirModule,
    artifact_dir: str | Path,
    *,
    pipeline: str = TVM_TILELANG_PIPELINE,
    config: TvmTilelangProviderConfig | None = None,
    timeout: float = 60.0,
) -> TvmTilelangCompileResult:
    if pipeline != TVM_TILELANG_PIPELINE:
        raise TvmTilelangProviderError(
            f"unsupported TVM/TileLang pass pipeline {pipeline!r}; no fallback"
        )
    config = config or load_tvm_tilelang_provider_config()
    _validate_config(config)
    frozen = plain.to_dict()
    canonical_digest = _sha256_bytes(_canonical_json(frozen))
    request = {
        "schema": REQUEST_SCHEMA,
        "backend": "tvm-tilelang",
        "target": "metal",
        "pipeline": pipeline,
        "ordered_passes": list(TVM_TILELANG_ORDERED_PASSES),
        "canonical_frozen_ir_sha256": canonical_digest,
        "frozen_module": frozen,
        "pin": dict(config.pin),
    }
    request_bytes = _canonical_json(request) + b"\n"
    request_id = _sha256_bytes(request_bytes)
    out_dir = Path(artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    request_path = out_dir / f"request-{request_id[:16]}.json"
    response_path = out_dir / (
        f"response-{request_id[:16]}-{uuid.uuid4().hex[:12]}.json"
    )
    request_path.write_bytes(request_bytes)
    command = [
        config.python,
        "-I",
        "-S",
        config.provider_script,
        "--root",
        config.root,
        "--site-packages",
        config.site_packages,
        "--request",
        str(request_path),
        "--response",
        str(response_path),
    ]
    return_code, stdout, stderr = _run_provider(
        command,
        config=config,
        timeout=timeout,
    )
    if return_code != 0:
        detail = stderr.strip() or stdout.strip() or "provider returned no diagnostic"
        raise TvmTilelangProviderError(
            f"pinned TVM/TileLang provider failed with exit {return_code}: "
            f"{detail}; no fallback"
        )
    if not response_path.is_file():
        detail = stderr.strip() or stdout.strip() or "provider wrote no response"
        raise TvmTilelangProviderError(
            f"pinned TVM/TileLang provider failed: {detail}; no fallback"
        )
    response = _require_mapping(
        json.loads(response_path.read_text(encoding="utf-8")),
        "root",
    )
    _validate_response(response, expected_digest=canonical_digest, config=config)
    provider_source = str(response["metal_source"])
    source, provider_entry = _adapt_provider_metal_source(
        provider_source,
        plain=plain,
    )
    source_digest = _sha256_bytes(source.encode("utf-8"))
    provider_source_path = out_dir / (
        f"{plain.module}-{response['metal_source_sha256'][:16]}.provider.metal"
    )
    provider_source_path.write_text(provider_source, encoding="utf-8")
    source_path = out_dir / f"{plain.module}-{source_digest[:16]}.pcc-abi.metal"
    source_path.write_text(source, encoding="utf-8")
    diagnostics = response.get("diagnostics")
    if not isinstance(diagnostics, list) or any(not isinstance(x, str) for x in diagnostics):
        raise TvmTilelangProviderError("provider diagnostics must be a string array")
    return TvmTilelangCompileResult(
        backend="tvm-tilelang",
        target="metal",
        pipeline=pipeline,
        ordered_passes=TVM_TILELANG_ORDERED_PASSES,
        provider_identity=str(response["provider_identity"]),
        canonical_frozen_ir_sha256=canonical_digest,
        semantic_kind=str(response["semantic_kind"]),
        logical_entry=str(response["logical_entry"]),
        provider_entry=provider_entry,
        provider_metal_source=provider_source,
        provider_metal_source_sha256=str(response["metal_source_sha256"]),
        metal_source=source,
        metal_source_sha256=source_digest,
        tilelang_revision=str(response["tilelang_revision"]),
        tilelang_version=str(response["tilelang_version"]),
        tvm_revision=str(response["tvm_revision"]),
        tvm_version=str(response["tvm_version"]),
        provider_process_links_libpython=True,
        dependencies=dict(response["dependencies"]),
        diagnostics=tuple(diagnostics),
        request_path=str(request_path),
        response_path=str(response_path),
        provider_source_path=str(provider_source_path),
        source_path=str(source_path),
    )


__all__ = [
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "TVM_TILELANG_PIPELINE",
    "TVM_TILELANG_PROVIDER_IDENTITY",
    "TVM_TILELANG_ORDERED_PASSES",
    "TvmTilelangProviderError",
    "TvmTilelangProviderConfig",
    "TvmTilelangCompileResult",
    "load_tvm_tilelang_provider_config",
    "compile_with_tvm_tilelang_provider",
]
