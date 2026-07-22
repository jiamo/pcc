"""Explicit execution-owner drivers for pcc Kernel IR GPU launches.

The driver boundary keeps GPU selection fail-closed.  A caller requests one
owner, and the result records that same owner; this module never probes another
backend as a fallback.  The first implementation wraps pcc's existing Metal
finalize/package/runtime path without changing its device semantics.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pcc.kernel_ir.hmm_fence import PccPackedArgs
from pcc.kernel_ir.ir import KernelModule
from pcc.kernel_ir.metal_finalize import (
    emit_metal_simdgroup_gemm_source,
    emit_metal_source,
)
from pcc.kernel_ir.metal_launch import MetalLaunchPlan, plan_metal_launch
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.schedule import (
    KernelSchedule,
    KernelScheduleError,
    apply_kernel_schedule,
)
from pcc.kernel_ir.tirx_adapter import (
    PLAIN_TIR_FREEZE_MARKER,
    PlainTirModule,
    lower_to_plain_tir,
)
from pcc.kernel_ir.tvm_tilelang_owner import (
    TVM_TILELANG_PIPELINE,
    TVM_TILELANG_PROVIDER_IDENTITY,
    TvmTilelangCompileResult,
    TvmTilelangProviderConfig,
    TvmTilelangProviderError,
    compile_with_tvm_tilelang_provider,
)

GPU_BACKEND_PCC_METAL = "pcc-metal"
GPU_BACKEND_TVM_TILELANG = "tvm-tilelang"
PCC_METAL_SCALAR_PIPELINE = "pcc-metal-scalar-v1"
PCC_METAL_SIMDGROUP_GEMM_PIPELINE = "pcc-metal-simdgroup-gemm-v1"
PCC_METAL_DRIVER_IDENTITY = "pcc-metal-owner-driver-v1"


class GpuBackendError(ValueError):
    """A GPU owner request or driver transition violated the contract."""


class GpuBackendUnavailable(GpuBackendError):
    """The explicitly requested owner is not implemented or available."""


@dataclass(frozen=True)
class GpuBackendCapabilities:
    backend: str
    target: str
    supported: bool
    compile_modes: tuple[str, ...]
    packed_args_abi: str = "PccPackedArgs-v1"
    fence_abi: str = "PccFenceToken-v1"
    accepts_pyobjects: bool = False


@dataclass(frozen=True)
class PccMetalValidation:
    backend: str
    target: str
    frozen_module: PlainTirModule
    canonical_frozen_ir_sha256: str
    schedule_plan_sha256: str | None = None
    schedule_trace: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class PccMetalCompileArtifacts:
    backend: str
    target: str
    pipeline: str
    provider_identity: str
    frozen_module: PlainTirModule
    canonical_frozen_ir_sha256: str
    metal_source: str
    metal_source_sha256: str
    artifact_id: str
    source_path: str
    schedule_plan_sha256: str | None = None
    schedule_trace: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class TvmTilelangValidation:
    backend: str
    target: str
    frozen_module: PlainTirModule
    canonical_frozen_ir_sha256: str
    schedule_plan_sha256: str | None = None
    schedule_trace: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class TvmTilelangCompileArtifacts:
    backend: str
    target: str
    pipeline: str
    frozen_module: PlainTirModule
    provider_result: TvmTilelangCompileResult
    artifact_id: str
    schedule_plan_sha256: str | None = None
    schedule_trace: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class TvmTilelangPackage:
    backend: str
    target: str
    compiled: TvmTilelangCompileArtifacts
    packed_args: PccPackedArgs
    launch_plan: MetalLaunchPlan
    artifact_dir: str


@dataclass(frozen=True)
class PccMetalPackage:
    backend: str
    target: str
    compiled: PccMetalCompileArtifacts
    packed_args: PccPackedArgs
    launch_plan: MetalLaunchPlan
    artifact_dir: str


@dataclass(frozen=True)
class GpuOwnerManifest:
    requested_gpu_backend: str
    actual_gpu_backend: str
    semantic_ir_owner: str
    codegen_owner: str
    runtime_owner: str
    target: str
    provider_identity: str
    canonical_frozen_ir_sha256: str
    pass_pipeline_identity: str
    artifact_hashes: Mapping[str, str]
    fallback_used: bool
    launcher_links_libpython: bool
    provider_process_links_libpython: bool
    claim_level: str
    gate_result: str
    whole_program_gpu: bool = False
    schedule_plan_sha256: str | None = None
    schedule_trace: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.requested_gpu_backend != self.actual_gpu_backend:
            raise GpuBackendError(
                "requested GPU backend does not match actual execution owner"
            )
        if self.fallback_used:
            raise GpuBackendError("GPU owner manifests must never record fallback")
        if self.actual_gpu_backend not in {
            GPU_BACKEND_PCC_METAL,
            GPU_BACKEND_TVM_TILELANG,
        }:
            raise GpuBackendError(
                f"unknown GPU execution owner {self.actual_gpu_backend!r}"
            )
        if self.whole_program_gpu:
            raise GpuBackendError("Kernel IR execution cannot claim whole-program GPU")
        if self.schedule_plan_sha256 is not None and (
            len(self.schedule_plan_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.schedule_plan_sha256)
        ):
            raise GpuBackendError("GPU owner manifest has an invalid schedule digest")
        if bool(self.schedule_plan_sha256) != bool(self.schedule_trace):
            raise GpuBackendError(
                "GPU owner manifest must record schedule digest and trace together"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_gpu_backend": self.requested_gpu_backend,
            "actual_gpu_backend": self.actual_gpu_backend,
            "semantic_ir_owner": self.semantic_ir_owner,
            "codegen_owner": self.codegen_owner,
            "runtime_owner": self.runtime_owner,
            "target": self.target,
            "provider_identity": self.provider_identity,
            "canonical_frozen_ir_sha256": self.canonical_frozen_ir_sha256,
            "pass_pipeline_identity": self.pass_pipeline_identity,
            "schedule_plan_sha256": self.schedule_plan_sha256,
            "schedule_trace": [dict(record) for record in self.schedule_trace],
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "fallback_used": self.fallback_used,
            "launcher_links_libpython": self.launcher_links_libpython,
            "provider_process_links_libpython": (self.provider_process_links_libpython),
            "claim_level": self.claim_level,
            "gate_result": self.gate_result,
            "whole_program_gpu": self.whole_program_gpu,
        }


@dataclass(frozen=True)
class GpuOwnerExecutionResult:
    manifest: GpuOwnerManifest
    raw_result: Any = field(repr=False)
    synchronized: bool = False
    resources_destroyed: bool = False

    def to_dict(self) -> dict[str, Any]:
        raw = _result_data(self.raw_result)
        return {
            "owner_manifest": self.manifest.to_dict(),
            "result": raw,
            "synchronized": self.synchronized,
            "resources_destroyed": self.resources_destroyed,
        }


class GpuBackendDriver(Protocol):
    backend_id: str

    def capabilities(self, target: str) -> GpuBackendCapabilities: ...

    def validate(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        *,
        schedule: KernelSchedule | None = None,
    ) -> Any: ...

    def compile(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        pipeline: str,
        artifact_dir: str | Path,
        *,
        schedule: KernelSchedule | None = None,
    ) -> Any: ...

    def package(
        self,
        artifacts: Any,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        *,
        entry: str | None = None,
    ) -> Any: ...

    def launch(self, package: Any, **kwargs: Any) -> Any: ...

    def synchronize(self, result: Any) -> bool: ...

    def destroy(self, result: Any) -> bool: ...

    def execute(
        self,
        module: KernelModule | PlainTirModule,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        **kwargs: Any,
    ) -> GpuOwnerExecutionResult: ...


def _canonical_frozen_ir(plain: PlainTirModule) -> bytes:
    return json.dumps(
        plain.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _freeze_with_schedule(
    module: KernelModule | PlainTirModule,
    *,
    target: str,
    schedule: KernelSchedule | None,
    backend: str,
) -> tuple[PlainTirModule, str | None, tuple[Mapping[str, Any], ...]]:
    if isinstance(module, PlainTirModule):
        if schedule is not None:
            raise GpuBackendError(
                f"{backend} cannot apply a KernelSchedule after plain-TIR freeze; "
                "no fallback"
            )
        return module, None, ()
    if schedule is None:
        return lower_to_plain_tir(module, target="metal"), None, ()
    try:
        applied = apply_kernel_schedule(module, schedule, target=target)
    except KernelScheduleError as exc:
        raise GpuBackendError(
            f"{backend} schedule rejected: {exc}; no fallback"
        ) from exc
    return (
        lower_to_plain_tir(applied.module, target="metal"),
        applied.schedule_sha256,
        applied.trace,
    )


def _result_data(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        data = result.to_dict()
    elif isinstance(result, Mapping):
        data = dict(result)
    else:
        raise GpuBackendError("GPU runtime result is not manifest-shaped")
    if not isinstance(data, dict):
        raise GpuBackendError("GPU runtime result did not produce a mapping")
    return data


def pcc_metal_owner_identity_fields(
    *,
    launcher_links_libpython: bool,
    pipeline: str = PCC_METAL_SCALAR_PIPELINE,
) -> dict[str, Any]:
    """Return the explicit pcc-metal identity record for launcher evidence."""
    return {
        "requested_gpu_backend": GPU_BACKEND_PCC_METAL,
        "actual_gpu_backend": GPU_BACKEND_PCC_METAL,
        "semantic_ir_owner": "pcc-kernel-ir-tirx",
        "codegen_owner": "pcc-metal-finalizer",
        "runtime_owner": "pcc-metal-runtime-source",
        "provider_identity": PCC_METAL_DRIVER_IDENTITY,
        "pass_pipeline_identity": pipeline,
        "fallback_used": False,
        "launcher_links_libpython": launcher_links_libpython,
        "provider_process_links_libpython": False,
    }


def tvm_tilelang_owner_identity_fields(
    *,
    launcher_links_libpython: bool,
    provider_identity: str,
    pipeline: str = TVM_TILELANG_PIPELINE,
) -> dict[str, Any]:
    """Return the explicit provider identity record for launcher evidence."""
    return {
        "requested_gpu_backend": GPU_BACKEND_TVM_TILELANG,
        "actual_gpu_backend": GPU_BACKEND_TVM_TILELANG,
        "semantic_ir_owner": "pcc-kernel-ir-tirx",
        "codegen_owner": "pinned-tvm-tilelang-provider",
        "runtime_owner": "pcc-metal-runtime-source",
        "provider_identity": provider_identity,
        "pass_pipeline_identity": pipeline,
        "fallback_used": False,
        "launcher_links_libpython": launcher_links_libpython,
        "provider_process_links_libpython": True,
    }


def validate_gpu_owner_identity(
    result: Any,
    *,
    requested_gpu_backend: str,
) -> dict[str, Any]:
    """Validate requested=actual and no-fallback identity on any gate result."""
    data = _result_data(result)
    requested = str(requested_gpu_backend).strip().lower()
    recorded_requested = data.get("requested_gpu_backend")
    actual = data.get("actual_gpu_backend")
    if recorded_requested != requested:
        raise GpuBackendError(
            f"gate result requested owner {recorded_requested!r}, expected {requested!r}"
        )
    if actual != requested:
        raise GpuBackendError(
            f"gate result actual owner {actual!r}, expected {requested!r}"
        )
    if data.get("fallback_used") is not False:
        raise GpuBackendError("gate result did not prove fallback_used=false")
    if requested == GPU_BACKEND_PCC_METAL:
        expected = pcc_metal_owner_identity_fields(
            launcher_links_libpython=bool(data.get("launcher_links_libpython")),
            pipeline=str(data.get("pass_pipeline_identity") or ""),
        )
        for key in (
            "semantic_ir_owner",
            "codegen_owner",
            "runtime_owner",
            "provider_identity",
            "provider_process_links_libpython",
        ):
            if data.get(key) != expected[key]:
                raise GpuBackendError(
                    f"gate result has invalid pcc-metal identity field {key!r}"
                )
    elif requested == GPU_BACKEND_TVM_TILELANG:
        provider_identity = data.get("provider_identity")
        if provider_identity != TVM_TILELANG_PROVIDER_IDENTITY:
            raise GpuBackendError(
                "gate result did not use the pinned TVM/TileLang provider identity"
            )
        expected = tvm_tilelang_owner_identity_fields(
            launcher_links_libpython=bool(data.get("launcher_links_libpython")),
            provider_identity=provider_identity,
            pipeline=str(data.get("pass_pipeline_identity") or ""),
        )
        for key in (
            "semantic_ir_owner",
            "codegen_owner",
            "runtime_owner",
            "provider_process_links_libpython",
        ):
            if data.get(key) != expected[key]:
                raise GpuBackendError(
                    f"gate result has invalid tvm-tilelang identity field {key!r}"
                )
    return data


class PccMetalGpuBackendDriver:
    """First common execution-owner driver for pcc's Metal path."""

    backend_id = GPU_BACKEND_PCC_METAL

    def __init__(
        self,
        *,
        runtime_runner: Callable[..., Any] = run_metal_source_runtime_package,
        source_emitters: Mapping[str, Callable[[PlainTirModule], str]] | None = None,
    ) -> None:
        self._runtime_runner = runtime_runner
        self._source_emitters = dict(
            source_emitters
            or {
                PCC_METAL_SCALAR_PIPELINE: emit_metal_source,
                PCC_METAL_SIMDGROUP_GEMM_PIPELINE: (emit_metal_simdgroup_gemm_source),
            }
        )

    def capabilities(self, target: str) -> GpuBackendCapabilities:
        normalized = str(target).lower()
        supported = normalized == "metal" or normalized.startswith("metal:")
        return GpuBackendCapabilities(
            backend=self.backend_id,
            target=normalized,
            supported=supported,
            compile_modes=tuple(sorted(self._source_emitters)),
        )

    def validate(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        *,
        schedule: KernelSchedule | None = None,
    ) -> PccMetalValidation:
        capabilities = self.capabilities(target)
        if not capabilities.supported:
            raise GpuBackendError(
                f"{self.backend_id} does not support target {target!r}; no fallback"
            )
        plain, schedule_digest, schedule_trace = _freeze_with_schedule(
            module,
            target=capabilities.target,
            schedule=schedule,
            backend=self.backend_id,
        )
        if isinstance(module, PlainTirModule):
            if plain.target != "metal" or plain.marker != PLAIN_TIR_FREEZE_MARKER:
                raise GpuBackendError(
                    "pcc-metal requires canonical Metal plain-TIR freeze input"
                )
        digest = _sha256_bytes(_canonical_frozen_ir(plain))
        return PccMetalValidation(
            backend=self.backend_id,
            target=capabilities.target,
            frozen_module=plain,
            canonical_frozen_ir_sha256=digest,
            schedule_plan_sha256=schedule_digest,
            schedule_trace=schedule_trace,
        )

    def compile(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        pipeline: str,
        artifact_dir: str | Path,
        *,
        schedule: KernelSchedule | None = None,
    ) -> PccMetalCompileArtifacts:
        validation = self.validate(module, target, schedule=schedule)
        emitter = self._source_emitters.get(pipeline)
        if emitter is None:
            raise GpuBackendError(
                f"unsupported pcc-metal pass pipeline {pipeline!r}; no fallback"
            )
        source = emitter(validation.frozen_module)
        if not isinstance(source, str) or not source.strip():
            raise GpuBackendError("pcc-metal source emitter produced no Metal source")
        source_digest = _sha256_bytes(source.encode("utf-8"))
        artifact_identity = (
            PCC_METAL_DRIVER_IDENTITY
            + "\0"
            + validation.canonical_frozen_ir_sha256
            + "\0"
            + pipeline
            + "\0"
            + source_digest
        )
        if validation.schedule_plan_sha256 is not None:
            artifact_identity += "\0" + validation.schedule_plan_sha256
        artifact_id = _sha256_bytes(artifact_identity.encode("utf-8"))
        out_dir = Path(artifact_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        source_path = out_dir / (
            validation.frozen_module.module + "-" + artifact_id[:16] + ".metal"
        )
        source_path.write_text(source, encoding="utf-8")
        return PccMetalCompileArtifacts(
            backend=self.backend_id,
            target=validation.target,
            pipeline=pipeline,
            provider_identity=PCC_METAL_DRIVER_IDENTITY,
            frozen_module=validation.frozen_module,
            canonical_frozen_ir_sha256=validation.canonical_frozen_ir_sha256,
            metal_source=source,
            metal_source_sha256=source_digest,
            artifact_id=artifact_id,
            source_path=str(source_path),
            schedule_plan_sha256=validation.schedule_plan_sha256,
            schedule_trace=validation.schedule_trace,
        )

    def package(
        self,
        artifacts: PccMetalCompileArtifacts,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        *,
        entry: str | None = None,
    ) -> PccMetalPackage:
        if artifacts.backend != self.backend_id:
            raise GpuBackendError("cannot package artifacts from another GPU owner")
        launch_plan = plan_metal_launch(
            artifacts.frozen_module,
            packed_args,
            entry=entry,
        )
        out_dir = Path(artifact_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return PccMetalPackage(
            backend=self.backend_id,
            target=artifacts.target,
            compiled=artifacts,
            packed_args=packed_args,
            launch_plan=launch_plan,
            artifact_dir=str(out_dir),
        )

    def launch(
        self,
        package: PccMetalPackage,
        *,
        input_matrices: Mapping[str, object],
        cpu_reference: Any,
        output_name: str | None = None,
        wait_until_completed: bool = True,
        timeout: float = 30.0,
    ) -> Any:
        if package.backend != self.backend_id:
            raise GpuBackendError("cannot launch a package from another GPU owner")
        if not wait_until_completed:
            raise GpuBackendError(
                "pcc-metal owner v1 requires synchronized oracle verification"
            )
        return self._runtime_runner(
            package.compiled.frozen_module,
            package.packed_args,
            Path(package.artifact_dir) / "runtime",
            metal_source=package.compiled.metal_source,
            input_matrices=input_matrices,
            cpu_reference=cpu_reference,
            output_name=output_name,
            wait_until_completed=True,
            timeout=timeout,
        )

    def synchronize(self, result: Any) -> bool:
        data = _result_data(result)
        if data.get("status") == STATUS_SKIPPED_WITH_REASON:
            return False
        invocation = data.get("invocation")
        if not isinstance(invocation, dict) or not invocation.get("fence_completed"):
            raise GpuBackendError("pcc-metal launch did not complete its fence")
        if not data.get("runtime_launch_executed"):
            raise GpuBackendError("pcc-metal result did not execute a device launch")
        return True

    def destroy(self, result: Any) -> bool:
        data = _result_data(result)
        if data.get("status") == STATUS_SKIPPED_WITH_REASON:
            return True
        if not data.get("allocations_released"):
            raise GpuBackendError(
                "pcc-metal runtime returned before fence-safe resource release"
            )
        return True

    def execute(
        self,
        module: KernelModule | PlainTirModule,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        *,
        target: str = "metal:0",
        pipeline: str = PCC_METAL_SCALAR_PIPELINE,
        input_matrices: Mapping[str, object],
        cpu_reference: Any,
        output_name: str | None = None,
        entry: str | None = None,
        timeout: float = 30.0,
        launcher_links_libpython: bool,
        schedule: KernelSchedule | None = None,
    ) -> GpuOwnerExecutionResult:
        compiled = self.compile(
            module,
            target,
            pipeline,
            Path(artifact_dir) / "compile",
            schedule=schedule,
        )
        package = self.package(
            compiled,
            packed_args,
            Path(artifact_dir) / "package",
            entry=entry,
        )
        raw_result = self.launch(
            package,
            input_matrices=input_matrices,
            cpu_reference=cpu_reference,
            output_name=output_name,
            wait_until_completed=True,
            timeout=timeout,
        )
        synchronized = self.synchronize(raw_result)
        resources_destroyed = self.destroy(raw_result)
        manifest = self._manifest(
            compiled,
            raw_result,
            launcher_links_libpython=launcher_links_libpython,
        )
        return GpuOwnerExecutionResult(
            manifest=manifest,
            raw_result=raw_result,
            synchronized=synchronized,
            resources_destroyed=resources_destroyed,
        )

    def _manifest(
        self,
        compiled: PccMetalCompileArtifacts,
        result: Any,
        *,
        launcher_links_libpython: bool,
    ) -> GpuOwnerManifest:
        from pcc.kernel_ir.gpu_claims import (
            classify_metal_source_runtime_package_result,
        )

        data = _result_data(result)
        evidence = classify_metal_source_runtime_package_result(
            compiled.frozen_module.module,
            data,
        )
        artifact_hashes: dict[str, str] = {
            "canonical_frozen_ir": compiled.canonical_frozen_ir_sha256,
            "metal_source": compiled.metal_source_sha256,
        }
        if compiled.schedule_plan_sha256 is not None:
            artifact_hashes["schedule_plan"] = compiled.schedule_plan_sha256
        try:
            from pcc.kernel_ir.metal_source_runtime import (
                metal_source_runtime_package_manifest_dict,
            )

            runtime_manifest = metal_source_runtime_package_manifest_dict(result)
            for name, record in runtime_manifest.get("artifacts", {}).items():
                digest = record.get("sha256") if isinstance(record, dict) else None
                if isinstance(digest, str):
                    artifact_hashes["runtime." + str(name)] = digest
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            # Injected unit results still carry the canonical IR and Metal
            # source hashes.  Real package results add every runtime artifact.
            pass
        return GpuOwnerManifest(
            requested_gpu_backend=self.backend_id,
            actual_gpu_backend=self.backend_id,
            semantic_ir_owner="pcc-kernel-ir-tirx",
            codegen_owner="pcc-metal-finalizer",
            runtime_owner="pcc-metal-runtime-source",
            target=compiled.target,
            provider_identity=compiled.provider_identity,
            canonical_frozen_ir_sha256=compiled.canonical_frozen_ir_sha256,
            pass_pipeline_identity=compiled.pipeline,
            artifact_hashes=artifact_hashes,
            fallback_used=False,
            launcher_links_libpython=launcher_links_libpython,
            provider_process_links_libpython=False,
            claim_level=evidence.level.name,
            gate_result=evidence.status,
            whole_program_gpu=False,
            schedule_plan_sha256=compiled.schedule_plan_sha256,
            schedule_trace=compiled.schedule_trace,
        )


class TvmTilelangGpuBackendDriver:
    """Pinned TileLang/TVM codegen owner using pcc's packed Metal runtime ABI."""

    backend_id = GPU_BACKEND_TVM_TILELANG

    def __init__(
        self,
        *,
        runtime_runner: Callable[..., Any] = run_metal_source_runtime_package,
        provider_compiler: Callable[..., TvmTilelangCompileResult] = (
            compile_with_tvm_tilelang_provider
        ),
        provider_config: TvmTilelangProviderConfig | None = None,
    ) -> None:
        self._runtime_runner = runtime_runner
        self._provider_compiler = provider_compiler
        self._provider_config = provider_config

    def capabilities(self, target: str) -> GpuBackendCapabilities:
        normalized = str(target).lower()
        supported = normalized == "metal" or normalized.startswith("metal:")
        return GpuBackendCapabilities(
            backend=self.backend_id,
            target=normalized,
            supported=supported,
            compile_modes=(TVM_TILELANG_PIPELINE,),
        )

    def validate(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        *,
        schedule: KernelSchedule | None = None,
    ) -> TvmTilelangValidation:
        capabilities = self.capabilities(target)
        if not capabilities.supported:
            raise GpuBackendError(
                f"{self.backend_id} does not support target {target!r}; no fallback"
            )
        plain, schedule_digest, schedule_trace = _freeze_with_schedule(
            module,
            target=capabilities.target,
            schedule=schedule,
            backend=self.backend_id,
        )
        if isinstance(module, PlainTirModule):
            if plain.target != "metal" or plain.marker != PLAIN_TIR_FREEZE_MARKER:
                raise GpuBackendError(
                    "tvm-tilelang requires canonical Metal plain-TIR freeze input"
                )
        digest = _sha256_bytes(_canonical_frozen_ir(plain))
        return TvmTilelangValidation(
            backend=self.backend_id,
            target=capabilities.target,
            frozen_module=plain,
            canonical_frozen_ir_sha256=digest,
            schedule_plan_sha256=schedule_digest,
            schedule_trace=schedule_trace,
        )

    def compile(
        self,
        module: KernelModule | PlainTirModule,
        target: str,
        pipeline: str,
        artifact_dir: str | Path,
        *,
        schedule: KernelSchedule | None = None,
    ) -> TvmTilelangCompileArtifacts:
        validation = self.validate(module, target, schedule=schedule)
        try:
            result = self._provider_compiler(
                validation.frozen_module,
                artifact_dir,
                pipeline=pipeline,
                config=self._provider_config,
            )
        except TvmTilelangProviderError as exc:
            raise GpuBackendUnavailable(str(exc)) from exc
        if result.backend != self.backend_id:
            raise GpuBackendError(
                "TVM/TileLang provider returned another execution owner; no fallback"
            )
        if result.canonical_frozen_ir_sha256 != validation.canonical_frozen_ir_sha256:
            raise GpuBackendError("TVM/TileLang provider changed canonical frozen IR")
        artifact_identity = (
            result.provider_identity
            + "\0"
            + result.canonical_frozen_ir_sha256
            + "\0"
            + result.pipeline
            + "\0"
            + result.provider_metal_source_sha256
            + "\0"
            + result.metal_source_sha256
        )
        if validation.schedule_plan_sha256 is not None:
            artifact_identity += "\0" + validation.schedule_plan_sha256
        artifact_id = _sha256_bytes(artifact_identity.encode("utf-8"))
        return TvmTilelangCompileArtifacts(
            backend=self.backend_id,
            target=validation.target,
            pipeline=pipeline,
            frozen_module=validation.frozen_module,
            provider_result=result,
            artifact_id=artifact_id,
            schedule_plan_sha256=validation.schedule_plan_sha256,
            schedule_trace=validation.schedule_trace,
        )

    def package(
        self,
        artifacts: TvmTilelangCompileArtifacts,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        *,
        entry: str | None = None,
    ) -> TvmTilelangPackage:
        if artifacts.backend != self.backend_id:
            raise GpuBackendError("cannot package artifacts from another GPU owner")
        launch_plan = plan_metal_launch(
            artifacts.frozen_module,
            packed_args,
            entry=entry,
        )
        out_dir = Path(artifact_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return TvmTilelangPackage(
            backend=self.backend_id,
            target=artifacts.target,
            compiled=artifacts,
            packed_args=packed_args,
            launch_plan=launch_plan,
            artifact_dir=str(out_dir),
        )

    def launch(
        self,
        package: TvmTilelangPackage,
        *,
        input_matrices: Mapping[str, object],
        cpu_reference: Any,
        output_name: str | None = None,
        wait_until_completed: bool = True,
        timeout: float = 30.0,
    ) -> Any:
        if package.backend != self.backend_id:
            raise GpuBackendError("cannot launch a package from another GPU owner")
        if not wait_until_completed:
            raise GpuBackendError(
                "tvm-tilelang owner v1 requires synchronized oracle verification"
            )
        return self._runtime_runner(
            package.compiled.frozen_module,
            package.packed_args,
            Path(package.artifact_dir) / "runtime",
            metal_source=package.compiled.provider_result.metal_source,
            input_matrices=input_matrices,
            cpu_reference=cpu_reference,
            output_name=output_name,
            wait_until_completed=True,
            timeout=timeout,
        )

    def synchronize(self, result: Any) -> bool:
        data = _result_data(result)
        if data.get("status") == STATUS_SKIPPED_WITH_REASON:
            return False
        invocation = data.get("invocation")
        if not isinstance(invocation, dict) or not invocation.get("fence_completed"):
            raise GpuBackendError("tvm-tilelang launch did not complete its fence")
        if not data.get("runtime_launch_executed"):
            raise GpuBackendError("tvm-tilelang result did not execute a device launch")
        return True

    def destroy(self, result: Any) -> bool:
        data = _result_data(result)
        if data.get("status") == STATUS_SKIPPED_WITH_REASON:
            return True
        if not data.get("allocations_released"):
            raise GpuBackendError(
                "tvm-tilelang runtime returned before fence-safe resource release"
            )
        return True

    def execute(
        self,
        module: KernelModule | PlainTirModule,
        packed_args: PccPackedArgs,
        artifact_dir: str | Path,
        *,
        target: str = "metal:0",
        pipeline: str = TVM_TILELANG_PIPELINE,
        input_matrices: Mapping[str, object],
        cpu_reference: Any,
        output_name: str | None = None,
        entry: str | None = None,
        timeout: float = 30.0,
        launcher_links_libpython: bool,
        schedule: KernelSchedule | None = None,
    ) -> GpuOwnerExecutionResult:
        compiled = self.compile(
            module,
            target,
            pipeline,
            Path(artifact_dir) / "compile",
            schedule=schedule,
        )
        package = self.package(
            compiled,
            packed_args,
            Path(artifact_dir) / "package",
            entry=entry,
        )
        raw_result = self.launch(
            package,
            input_matrices=input_matrices,
            cpu_reference=cpu_reference,
            output_name=output_name,
            wait_until_completed=True,
            timeout=timeout,
        )
        synchronized = self.synchronize(raw_result)
        resources_destroyed = self.destroy(raw_result)
        manifest = self._manifest(
            compiled,
            raw_result,
            launcher_links_libpython=launcher_links_libpython,
        )
        return GpuOwnerExecutionResult(
            manifest=manifest,
            raw_result=raw_result,
            synchronized=synchronized,
            resources_destroyed=resources_destroyed,
        )

    def _manifest(
        self,
        compiled: TvmTilelangCompileArtifacts,
        result: Any,
        *,
        launcher_links_libpython: bool,
    ) -> GpuOwnerManifest:
        from pcc.kernel_ir.gpu_claims import (
            classify_metal_source_runtime_package_result,
        )

        data = _result_data(result)
        evidence = classify_metal_source_runtime_package_result(
            compiled.frozen_module.module,
            data,
        )
        provider = compiled.provider_result
        artifact_hashes = provider.artifact_hashes()
        if compiled.schedule_plan_sha256 is not None:
            artifact_hashes["schedule_plan"] = compiled.schedule_plan_sha256
        try:
            from pcc.kernel_ir.metal_source_runtime import (
                metal_source_runtime_package_manifest_dict,
            )

            runtime_manifest = metal_source_runtime_package_manifest_dict(result)
            for name, record in runtime_manifest.get("artifacts", {}).items():
                digest = record.get("sha256") if isinstance(record, dict) else None
                if isinstance(digest, str):
                    artifact_hashes["runtime." + str(name)] = digest
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            pass
        return GpuOwnerManifest(
            requested_gpu_backend=self.backend_id,
            actual_gpu_backend=self.backend_id,
            semantic_ir_owner="pcc-kernel-ir-tirx",
            codegen_owner="pinned-tvm-tilelang-provider",
            runtime_owner="pcc-metal-runtime-source",
            target=compiled.target,
            provider_identity=provider.provider_identity,
            canonical_frozen_ir_sha256=provider.canonical_frozen_ir_sha256,
            pass_pipeline_identity=provider.pipeline,
            artifact_hashes=artifact_hashes,
            fallback_used=False,
            launcher_links_libpython=launcher_links_libpython,
            provider_process_links_libpython=(
                provider.provider_process_links_libpython
            ),
            claim_level=evidence.level.name,
            gate_result=evidence.status,
            whole_program_gpu=False,
            schedule_plan_sha256=compiled.schedule_plan_sha256,
            schedule_trace=compiled.schedule_trace,
        )


def get_gpu_backend_driver(requested_gpu_backend: str) -> GpuBackendDriver:
    """Return exactly the requested owner, or fail without probing fallback."""
    requested = str(requested_gpu_backend).strip().lower()
    if requested == GPU_BACKEND_PCC_METAL:
        return PccMetalGpuBackendDriver()
    if requested == "auto":
        raise GpuBackendError("'auto' is not a GPU execution-owner mode")
    if requested == GPU_BACKEND_TVM_TILELANG:
        return TvmTilelangGpuBackendDriver()
    raise GpuBackendUnavailable(
        f"unknown GPU execution owner {requested_gpu_backend!r}; no fallback"
    )


__all__ = [
    "GPU_BACKEND_PCC_METAL",
    "GPU_BACKEND_TVM_TILELANG",
    "PCC_METAL_SCALAR_PIPELINE",
    "PCC_METAL_SIMDGROUP_GEMM_PIPELINE",
    "PCC_METAL_DRIVER_IDENTITY",
    "GpuBackendError",
    "GpuBackendUnavailable",
    "GpuBackendCapabilities",
    "GpuBackendDriver",
    "GpuOwnerManifest",
    "GpuOwnerExecutionResult",
    "PccMetalValidation",
    "PccMetalCompileArtifacts",
    "PccMetalPackage",
    "PccMetalGpuBackendDriver",
    "TvmTilelangValidation",
    "TvmTilelangCompileArtifacts",
    "TvmTilelangPackage",
    "TvmTilelangGpuBackendDriver",
    "get_gpu_backend_driver",
    "pcc_metal_owner_identity_fields",
    "tvm_tilelang_owner_identity_fields",
    "validate_gpu_owner_identity",
]
