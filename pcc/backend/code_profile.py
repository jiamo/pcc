"""Conservative, code-identified profile consumption for self backends.

The first owned decision is deliberately small: order already-legal function
bodies in the emitted text section.  Profiles never make code legal, select a
semantic lowering, or become necessary for an artifact.  A profile is used
only when every identity field matches the current compile; malformed, stale,
or unmatched input returns the original deterministic AOT order.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Sequence, TypeVar


CODE_PROFILE_SCHEMA = "pcc.code-profile.v1"
CODE_PROFILE_ENV = "PCC_CODE_PROFILE"
CODE_PROFILE_SOURCE_IDENTITY_ENV = "PCC_CODE_PROFILE_SOURCE_IDENTITY"
CODE_PROFILE_SEMANTIC_MODE_ENV = "PCC_CODE_PROFILE_SEMANTIC_MODE"
CODE_PROFILE_RUNTIME_ABI_ENV = "PCC_CODE_PROFILE_RUNTIME_ABI"
FUNCTION_ORDER_DECISION = "function-order"
_MAX_PROFILE_BYTES = 1_048_576


@dataclass(frozen=True)
class FunctionOrderDecision:
    status: str
    profile_path: str = ""
    matched_samples: int = 0
    unmatched_samples: int = 0
    ordered_symbols: tuple[str, ...] = ()


T = TypeVar("T")


def code_profile_identity(ir_text: str) -> str:
    digest = hashlib.sha256(str(ir_text).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def source_profile_identity(source_bytes: bytes) -> str:
    digest = hashlib.sha256(bytes(source_bytes)).hexdigest()
    return "sha256:" + digest


def _nonempty_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def _read_profile(path: str) -> dict | None:
    try:
        if os.path.getsize(path) > _MAX_PROFILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _profile_sample_counts(value) -> dict[str, int] | None:
    if not isinstance(value, list):
        return None
    counts: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        symbol = _nonempty_text(item.get("symbol"))
        count = item.get("count")
        if (
            symbol is None
            or symbol in counts
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            return None
        counts[symbol] = count
    return counts


def _environment_value(
    environ: dict[str, str] | None,
    name: str,
) -> str:
    """Read one profile setting without erasing mapping provenance.

    Assigning ``os.environ`` and an abstract ``Mapping`` parameter to one
    local widened that local to a CPython-backed dynamic object in pcc1.  The
    whole profile consumer was consequently replaced by a strict no-libpython
    stub even when profiling was disabled.  Keep the process-environment and
    explicit-dict projections separate so the ordinary no-profile path remains
    natively self-hostable.
    """

    if environ is None:
        return str(os.environ.get(name, "") or "").strip()
    return str(environ.get(name, "") or "").strip()


def apply_function_order_profile(
    functions: Sequence[T],
    *,
    ir_text: str,
    target: str,
    environ: dict[str, str] | None = None,
) -> tuple[list[T], FunctionOrderDecision]:
    """Return profile-ordered functions or the exact original order.

    ``functions`` need only expose a string ``name`` attribute.  Stable source
    order breaks equal-count ties and orders all unmatched functions, so the
    same profile and input always produce identical output.
    """

    original = list(functions)
    profile_path = _environment_value(environ, CODE_PROFILE_ENV)
    if not profile_path:
        return original, FunctionOrderDecision(status="disabled")

    source_identity = _environment_value(
        environ, CODE_PROFILE_SOURCE_IDENTITY_ENV
    )
    semantic_mode = _environment_value(
        environ, CODE_PROFILE_SEMANTIC_MODE_ENV
    )
    runtime_abi = _environment_value(environ, CODE_PROFILE_RUNTIME_ABI_ENV)
    if not source_identity or not semantic_mode or not runtime_abi:
        return original, FunctionOrderDecision(
            status="missing-context",
            profile_path=profile_path,
        )

    profile = _read_profile(profile_path)
    if profile is None:
        return original, FunctionOrderDecision(
            status="invalid",
            profile_path=profile_path,
        )
    decision = profile.get("decision")
    if (
        profile.get("schema") != CODE_PROFILE_SCHEMA
        or not isinstance(decision, dict)
        or decision.get("kind") != FUNCTION_ORDER_DECISION
    ):
        return original, FunctionOrderDecision(
            status="invalid",
            profile_path=profile_path,
        )

    expected = {
        "source_identity": source_identity,
        "code_identity": code_profile_identity(ir_text),
        "semantic_mode": semantic_mode,
        "runtime_abi": runtime_abi,
        "target": str(target),
    }
    for field, current_value in expected.items():
        if profile.get(field) != current_value:
            return original, FunctionOrderDecision(
                status="unmatched-" + field.replace("_", "-"),
                profile_path=profile_path,
            )

    counts = _profile_sample_counts(decision.get("function_samples"))
    if counts is None:
        return original, FunctionOrderDecision(
            status="invalid",
            profile_path=profile_path,
        )

    known: list[tuple[int, int, T, str]] = []
    unknown: list[tuple[int, T]] = []
    seen_symbols: set[str] = set()
    for index, function in enumerate(original):
        symbol = str(getattr(function, "name", "") or "")
        seen_symbols.add(symbol)
        if symbol in counts:
            known.append((-counts[symbol], index, function, symbol))
        else:
            unknown.append((index, function))
    known.sort(key=lambda item: (item[0], item[1]))
    ordered = [item[2] for item in known]
    ordered.extend(item[1] for item in unknown)
    ordered_symbols = tuple(str(getattr(item, "name", "") or "") for item in ordered)
    unmatched_samples = 0
    for symbol in counts:
        if symbol not in seen_symbols:
            unmatched_samples += 1
    return ordered, FunctionOrderDecision(
        status="matched",
        profile_path=profile_path,
        matched_samples=len(known),
        unmatched_samples=unmatched_samples,
        ordered_symbols=ordered_symbols,
    )


__all__ = [
    "CODE_PROFILE_ENV",
    "CODE_PROFILE_RUNTIME_ABI_ENV",
    "CODE_PROFILE_SCHEMA",
    "CODE_PROFILE_SEMANTIC_MODE_ENV",
    "CODE_PROFILE_SOURCE_IDENTITY_ENV",
    "FUNCTION_ORDER_DECISION",
    "FunctionOrderDecision",
    "apply_function_order_profile",
    "code_profile_identity",
    "source_profile_identity",
]
