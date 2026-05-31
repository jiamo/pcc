from __future__ import annotations

"""Target-specific pass hook for the self backend.

LLVM keeps target-independent IR passes and target/codegen passes as separate
pipelines.  The local reference points are LLVM 20.1.8's
``llvm/IR/PassManager.h`` for module/function IR passes and
``llvm/Passes/CodeGenPassBuilder.h`` / ``llvm/CodeGen/TargetPassConfig.h`` for
machine/codegen passes.

This module is the first PCC-side target-pass hook.  It intentionally starts at
the assembly-text boundary because that is the self backend's stable output
today.  Later target-memory IR should plug in here without changing callers.
"""

from dataclasses import dataclass
import os
from typing import Protocol

from . import BackendUnavailable


PCC_SELF_TARGET_PASSES_ENV = "PCC_SELF_TARGET_PASSES"
PCC_SELF_TARGET_PASS_TRANSPORT_ENV = "PCC_SELF_TARGET_PASS_TRANSPORT"

_TRANSPORT_TEXT = "text"
_TRANSPORT_MEMORY = "memory"


@dataclass(frozen=True)
class SelfTargetPassContext:
    target_id: str
    transport: str = _TRANSPORT_TEXT


class SelfTargetPass(Protocol):
    name: str

    def run(self, asm_text: str, ctx: SelfTargetPassContext) -> str:
        ...


class SelfTargetMemoryPass(Protocol):
    name: str

    def run(self, prepared, ctx: SelfTargetPassContext):
        ...


@dataclass(frozen=True)
class StripTrailingWhitespacePass:
    name: str = "strip-trailing-whitespace"

    def run(self, asm_text: str, ctx: SelfTargetPassContext) -> str:
        lines = asm_text.splitlines()
        out = "\n".join(line.rstrip() for line in lines)
        if asm_text.endswith("\n"):
            out += "\n"
        return out


@dataclass(frozen=True)
class VerifyPreparedModulePass:
    name: str = "verify-prepared-module"

    def run(self, prepared, ctx: SelfTargetPassContext):
        if not getattr(prepared, "triple", ""):
            raise BackendUnavailable("self target memory pass saw module without target triple")
        for func in getattr(prepared, "functions", ()):
            if not getattr(func, "block_map", None):
                raise BackendUnavailable(
                    "self target memory pass saw unprepared function "
                    f"{getattr(func, 'name', '<unknown>')!r}"
                )
            for arg in getattr(func, "args", ()):
                if arg.name not in getattr(func, "value_types", {}):
                    raise BackendUnavailable(
                        "self target memory pass saw missing argument type for "
                        f"{getattr(func, 'name', '<unknown>')!r}/{arg.name!r}"
                    )
        return prepared


_PASS_REGISTRY: dict[str, SelfTargetPass] = {
    "strip-trailing-whitespace": StripTrailingWhitespacePass(),
}
_MEMORY_PASS_REGISTRY: dict[str, SelfTargetMemoryPass] = {
    "verify-prepared-module": VerifyPreparedModulePass(),
}


def resolve_self_target_pass_transport(raw: str | None = None) -> str:
    value = (
        os.environ.get(PCC_SELF_TARGET_PASS_TRANSPORT_ENV, "")
        if raw is None
        else raw
    )
    normalized = str(value or "").strip().lower()
    if normalized in ("", _TRANSPORT_TEXT):
        return _TRANSPORT_TEXT
    if normalized == _TRANSPORT_MEMORY:
        return _TRANSPORT_MEMORY
    raise BackendUnavailable(
        "unknown self target pass transport "
        f"{value!r}; expected 'text' or 'memory'"
    )


def resolve_self_target_pass_names(
    raw: str | None = None,
    *,
    transport: str | None = None,
) -> tuple[str, ...]:
    value = os.environ.get(PCC_SELF_TARGET_PASSES_ENV, "") if raw is None else raw
    normalized = str(value or "").strip()
    if normalized == "":
        return ()
    lowered = normalized.lower()
    if lowered in ("off", "none", "0", "false", "no"):
        return ()
    if lowered in ("default",):
        return ()
    if lowered in ("all",):
        selected_transport = (
            resolve_self_target_pass_transport()
            if transport is None else transport
        )
        if selected_transport == _TRANSPORT_MEMORY:
            return tuple(_MEMORY_PASS_REGISTRY)
        return tuple(_PASS_REGISTRY)

    out: list[str] = []
    selected_transport = (
        resolve_self_target_pass_transport()
        if transport is None else transport
    )
    registry = (
        _MEMORY_PASS_REGISTRY
        if selected_transport == _TRANSPORT_MEMORY else _PASS_REGISTRY
    )
    for item in normalized.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in registry:
            raise BackendUnavailable(
                f"unknown self target pass {name!r}; known passes: "
                + ", ".join(sorted(registry))
            )
        out.append(name)
    return tuple(out)


def run_self_target_pass_pipeline(
    asm_text: str,
    target_id: str,
    *,
    raw_passes: str | None = None,
    raw_transport: str | None = None,
) -> str:
    transport = resolve_self_target_pass_transport(raw_transport)
    if transport == _TRANSPORT_MEMORY:
        return asm_text
    pass_names = resolve_self_target_pass_names(
        raw_passes,
        transport=transport,
    )
    if not pass_names:
        return asm_text

    ctx = SelfTargetPassContext(target_id=target_id, transport=transport)
    current = asm_text
    for name in pass_names:
        current = _PASS_REGISTRY[name].run(current, ctx)
    return current


def run_self_target_memory_pass_pipeline(
    prepared,
    target_id: str,
    *,
    raw_passes: str | None = None,
    raw_transport: str | None = None,
):
    transport = resolve_self_target_pass_transport(raw_transport)
    if transport != _TRANSPORT_MEMORY:
        return prepared
    pass_names = resolve_self_target_pass_names(
        raw_passes,
        transport=transport,
    )
    if not pass_names:
        return prepared
    ctx = SelfTargetPassContext(target_id=target_id, transport=transport)
    current = prepared
    for name in pass_names:
        current = _MEMORY_PASS_REGISTRY[name].run(current, ctx)
    return current
