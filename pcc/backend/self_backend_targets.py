from __future__ import annotations

"""Target registry for self-backend emitters.

This is the first explicit target table for the self backend. The goal is to
stop growing target dispatch as ad-hoc conditionals and instead expose a stable
registry that future translated targets can plug into.
"""

from typing import Callable
from dataclasses import dataclass

from . import BackendUnavailable
from .self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from .self_backend_target_match import (
    is_aarch64_darwin_triple,
    is_x86_64_linux_triple,
)
from .self_backend_x86_64_linux import emit_x86_64_linux_asm

SelfAsmEmitter = Callable[[str], str]

STATUS_SELF_TARGET_SUPPORTED = "SUPPORTED"
STATUS_SELF_TARGET_UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class SelfBackendPlatformVerdict:
    triple: str
    status: str
    target_identity: str | None
    reason: str
    backend_executed: bool = False
    runtime_executed: bool = False

    @property
    def supported(self) -> bool:
        return self.status == STATUS_SELF_TARGET_SUPPORTED

    def to_dict(self) -> dict[str, object]:
        return {
            "triple": self.triple,
            "status": self.status,
            "target_identity": self.target_identity,
            "reason": self.reason,
            "backend_executed": self.backend_executed,
            "runtime_executed": self.runtime_executed,
        }

    def skip_reason(self) -> str:
        if self.supported:
            return f"SUPPORTED[self-backend:{self.triple}]: decorator inactive"
        return (
            f"UNSUPPORTED[self-backend:{self.triple}]: {self.reason}; "
            "backend_executed=false; runtime_executed=false"
        )


@dataclass(frozen=True)
class SelfBackendTargetSpec:
    identity: str
    matches_triple: Callable[[str], bool]
    emit_asm: SelfAsmEmitter


SELF_BACKEND_TARGETS: tuple[SelfBackendTargetSpec, ...] = (
    SelfBackendTargetSpec(
        identity="self-aarch64-darwin-v0",
        matches_triple=is_aarch64_darwin_triple,
        emit_asm=emit_aarch64_darwin_asm,
    ),
    SelfBackendTargetSpec(
        identity="self-x86_64-linux-v0",
        matches_triple=is_x86_64_linux_triple,
        emit_asm=emit_x86_64_linux_asm,
    ),
)


def known_self_backend_target_identities() -> tuple[str, ...]:
    return tuple(target.identity for target in SELF_BACKEND_TARGETS)


def is_supported_self_backend_target_triple(triple: str) -> bool:
    try:
        resolve_self_backend_target(triple)
    except BackendUnavailable:
        return False
    return True


def classify_self_backend_target_triple(triple: str) -> SelfBackendPlatformVerdict:
    try:
        target = resolve_self_backend_target(triple)
    except BackendUnavailable:
        return SelfBackendPlatformVerdict(
            triple=triple,
            status=STATUS_SELF_TARGET_UNSUPPORTED,
            target_identity=None,
            reason=f"no registered self-backend emitter matches {triple!r}",
        )
    return SelfBackendPlatformVerdict(
        triple=triple,
        status=STATUS_SELF_TARGET_SUPPORTED,
        target_identity=target.identity,
        reason=f"resolved to registered emitter {target.identity!r}",
    )


def resolve_self_backend_target(triple: str) -> SelfBackendTargetSpec:
    for target in SELF_BACKEND_TARGETS:
        if target.matches_triple(triple):
            return target
    raise BackendUnavailable(
        f"self backend has no emitter for target triple {triple!r}"
    )
