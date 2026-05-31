from __future__ import annotations

"""Dispatch layer for self-backend target emitters."""

from .self_backend_parse import parse_self_backend_target_triple
from .self_backend_targets import (
    SelfAsmEmitter,
    resolve_self_backend_target,
)
from .self_backend_target_passes import run_self_target_pass_pipeline


def resolve_self_asm_emitter(
    triple: str,
) -> tuple[str, SelfAsmEmitter]:
    target = resolve_self_backend_target(triple)
    return target.identity, target.emit_asm


def self_backend_target_identity(triple: str) -> str:
    target_id, _emitter = resolve_self_asm_emitter(triple)
    return target_id


def emit_self_asm(ir_text: str, triple: str | None = None) -> str:
    if triple is None:
        triple = parse_self_backend_target_triple(ir_text)
    target_id, emitter = resolve_self_asm_emitter(triple)
    asm_text = emitter(ir_text)
    return run_self_target_pass_pipeline(asm_text, target_id)
