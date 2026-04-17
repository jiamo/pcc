from __future__ import annotations

"""Target registry for self-backend emitters.

This is the first explicit target table for the self backend. The goal is to
stop growing target dispatch as ad-hoc conditionals and instead expose a stable
registry that future translated targets can plug into.
"""

from collections.abc import Callable
from dataclasses import dataclass

from . import BackendUnavailable
from .self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from .self_backend_target_match import (
    is_aarch64_darwin_triple,
    is_x86_64_linux_triple,
)
from .self_backend_x86_64_linux import emit_x86_64_linux_asm


SelfAsmEmitter = Callable[[str], str]


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


def resolve_self_backend_target(triple: str) -> SelfBackendTargetSpec:
    for target in SELF_BACKEND_TARGETS:
        if target.matches_triple(triple):
            return target
    raise BackendUnavailable(
        f"self backend has no emitter for target triple {triple!r}"
    )
