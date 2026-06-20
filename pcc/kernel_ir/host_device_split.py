"""Host/device split proof surface for TIRx -> Metal kernels.

This module does not execute a GPU kernel. It records the CPU-side launch
boundary that a host backend must own after the target split: ordinary Python
and runtime semantics stay on the host backend, while only validated kernel
arguments cross into the Metal entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pcc.kernel_ir.ir import KernelModule, MemoryScope
from pcc.kernel_ir.metal_finalize import MetalFinalizeResult, finalize_metal
from pcc.kernel_ir.target_split import DeviceTarget, HostBackend, TargetMachine, resolve
from pcc.kernel_ir.tirx_adapter import PlainTirModule, lower_to_plain_tir


class HostDeviceSplitError(ValueError):
    """A kernel module could not be represented as a host/device launch split."""


@dataclass(frozen=True)
class KernelArgBinding:
    """One argument crossing the CPU host -> Metal kernel boundary."""

    name: str
    kind: str
    dtype: str
    index: int
    address_space: str
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "dtype": self.dtype,
            "index": self.index,
            "address_space": self.address_space,
        }
        if self.rank is not None:
            data["rank"] = self.rank
        return data


@dataclass(frozen=True)
class DeviceLocalBinding:
    """One device-local allocation owned by the Metal kernel body."""

    name: str
    dtype: str
    scope: str
    shape: tuple[int, ...]
    address_space: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "scope": self.scope,
            "shape": list(self.shape),
            "address_space": self.address_space,
        }


@dataclass(frozen=True)
class KernelLaunchBoundary:
    """CPU-host launch boundary for one Metal kernel entry point."""

    kernel_entry: str
    launcher_symbol: str
    arg_bindings: tuple[KernelArgBinding, ...]
    device_locals: tuple[DeviceLocalBinding, ...]
    host_launch_boundary_proven: bool
    runtime_launch_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kernel_entry": self.kernel_entry,
            "launcher_symbol": self.launcher_symbol,
            "arg_bindings": [b.to_dict() for b in self.arg_bindings],
            "device_locals": [b.to_dict() for b in self.device_locals],
            "host_launch_boundary_proven": self.host_launch_boundary_proven,
            "runtime_launch_executed": self.runtime_launch_executed,
        }


@dataclass(frozen=True)
class HostDeviceSplitProof:
    """Mode-labeled proof that host and device responsibilities are split."""

    machine: TargetMachine
    launches: tuple[KernelLaunchBoundary, ...]
    ordinary_python_runs_on_host: bool
    whole_program_gpu: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_mode": "host/device split IR + Metal host launch boundary",
            "host": self.machine.host.to_dict(),
            "device": self.machine.device.to_dict(),
            "finalize_plan": self.machine.finalize_plan(),
            "ordinary_python_runs_on_host": self.ordinary_python_runs_on_host,
            "whole_program_gpu": self.whole_program_gpu,
            "launches": [launch.to_dict() for launch in self.launches],
        }


@dataclass(frozen=True)
class TirxMetalHostDeviceResult:
    """Combined artifact + host-launch-boundary proof for one kernel module."""

    split: HostDeviceSplitProof
    metal: MetalFinalizeResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_mode": "Metal source/metallib artifact plus host launch boundary",
            "whole_program_gpu": False,
            "runtime_launch_executed": False,
            "host_device_split": self.split.to_dict(),
            "device_artifacts": self.metal.to_dict(),
        }


def _coerce_plain(module: KernelModule | PlainTirModule) -> PlainTirModule:
    return module if isinstance(module, PlainTirModule) else lower_to_plain_tir(module, target="metal")


def _binding_from_param(entry: str, index: int, param: dict[str, Any]) -> KernelArgBinding:
    kind = param.get("kind")
    name = param.get("name")
    dtype = param.get("dtype")
    if not isinstance(name, str) or not isinstance(dtype, str):
        raise HostDeviceSplitError(f"{entry}: bad parameter record {param!r}")
    if kind == "buffer":
        scope = param.get("scope")
        if scope != MemoryScope.GLOBAL.value:
            raise HostDeviceSplitError(
                f"{entry}: non-global buffer {name!r} has scope {scope!r}; "
                "threadgroup/fragment storage must be allocated on the device "
                "side, not passed through the CPU host launch boundary"
            )
        rank = param.get("rank")
        if not isinstance(rank, int):
            raise HostDeviceSplitError(f"{entry}: buffer {name!r} has bad rank {rank!r}")
        return KernelArgBinding(
            name=name,
            kind="buffer",
            dtype=dtype,
            index=index,
            address_space="device",
            rank=rank,
        )
    if kind == "scalar":
        return KernelArgBinding(
            name=name,
            kind="scalar",
            dtype=dtype,
            index=index,
            address_space="constant",
        )
    raise HostDeviceSplitError(f"{entry}: unsupported launch parameter kind {kind!r}")


def _address_space_for_local(scope: str) -> str:
    if scope == MemoryScope.SHARED.value:
        return "threadgroup"
    if scope == MemoryScope.LOCAL.value:
        return "thread"
    if scope == MemoryScope.FRAGMENT.value:
        return "fragment"
    raise HostDeviceSplitError(f"unsupported device-local scope {scope!r}")


def _binding_from_local(entry: str, local: dict[str, Any]) -> DeviceLocalBinding:
    kind = local.get("kind")
    name = local.get("name")
    dtype = local.get("dtype")
    scope = local.get("scope")
    shape = local.get("shape")
    if kind != "local_buffer":
        raise HostDeviceSplitError(f"{entry}: unsupported local kind {kind!r}")
    if not isinstance(name, str) or not isinstance(dtype, str) or not isinstance(scope, str):
        raise HostDeviceSplitError(f"{entry}: bad local record {local!r}")
    if scope == MemoryScope.GLOBAL.value:
        raise HostDeviceSplitError(
            f"{entry}: global local {name!r} would be a hidden host argument"
        )
    if not isinstance(shape, list) or any((not isinstance(dim, int)) or dim <= 0 for dim in shape):
        raise HostDeviceSplitError(f"{entry}: bad local shape for {name!r}: {shape!r}")
    return DeviceLocalBinding(
        name=name,
        dtype=dtype,
        scope=scope,
        shape=tuple(shape),
        address_space=_address_space_for_local(scope),
    )


def build_host_launch_boundaries(
    module: KernelModule | PlainTirModule,
    *,
    host: str | HostBackend = HostBackend.SELF,
    device: str | DeviceTarget = DeviceTarget.METAL,
) -> HostDeviceSplitProof:
    """Build the CPU-host launch boundary for a Metal-targeted kernel module."""
    machine = resolve(host=host, device=device)
    if machine.device.device is not DeviceTarget.METAL:
        raise HostDeviceSplitError(
            f"host launch boundary proof requires device=metal, got {machine.device.device.value!r}"
        )
    plain = _coerce_plain(module)
    if plain.target != DeviceTarget.METAL.value:
        raise HostDeviceSplitError(f"expected Metal plain-TIR target, got {plain.target!r}")

    launches: list[KernelLaunchBoundary] = []
    for func in plain.funcs:
        entry = func.get("name")
        params = func.get("params")
        locals_ = func.get("locals", [])
        if not isinstance(entry, str) or not entry:
            raise HostDeviceSplitError(f"plain-TIR func has invalid name {entry!r}")
        if not isinstance(params, list):
            raise HostDeviceSplitError(f"{entry}: plain-TIR func has no params list")
        if not isinstance(locals_, list):
            raise HostDeviceSplitError(f"{entry}: plain-TIR func locals must be a list")
        bindings = tuple(
            _binding_from_param(entry, index, param)
            for index, param in enumerate(params)
        )
        local_bindings = tuple(_binding_from_local(entry, local) for local in locals_)
        launches.append(
            KernelLaunchBoundary(
                kernel_entry=entry,
                launcher_symbol=f"__pcc_launch_{entry}_metal",
                arg_bindings=bindings,
                device_locals=local_bindings,
                host_launch_boundary_proven=True,
            )
        )
    if not launches:
        raise HostDeviceSplitError("plain-TIR module has no kernel launch entries")

    return HostDeviceSplitProof(
        machine=machine,
        launches=tuple(launches),
        ordinary_python_runs_on_host=True,
        whole_program_gpu=False,
    )


def prove_tirx_metal_host_device_split(
    module: KernelModule,
    *,
    artifact_dir: str,
    compile_toolchain: bool = False,
    timeout: float = 30.0,
) -> TirxMetalHostDeviceResult:
    """Produce the first concrete TVM/TIRx -> Metal split proof.

    The result proves artifact staging plus the launch boundary shape. It does
    not run the launcher and does not claim whole-program GPU execution.
    """
    split = build_host_launch_boundaries(module, host=HostBackend.SELF, device=DeviceTarget.METAL)
    metal = finalize_metal(
        module,
        artifact_dir=artifact_dir,
        compile_toolchain=compile_toolchain,
        timeout=timeout,
    )
    return TirxMetalHostDeviceResult(split=split, metal=metal)


__all__ = [
    "HostDeviceSplitError",
    "KernelArgBinding",
    "DeviceLocalBinding",
    "KernelLaunchBoundary",
    "HostDeviceSplitProof",
    "TirxMetalHostDeviceResult",
    "build_host_launch_boundaries",
    "prove_tirx_metal_host_device_split",
]
