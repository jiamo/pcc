"""Sparse constant lattice used by SCCP.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Analysis/ValueLattice.h``
  defines :cpp:class:`llvm::ValueLatticeElement`. That type is richer
  (integer range, constant range, not-a-constant, is-a-constant, plus
  undef/poison bits). For SCCP's purposes the core lattice is a
  three-point structure:

  - ``top``           — not yet proven anything; conservative start,
  - ``constant(c)``   — proven a specific constant,
  - ``overdefined``   — proven not-constant.

  The ``meet`` operation is standard: ``meet(top, x) = x``,
  ``meet(const c, const c) = const c``,
  ``meet(const a, const b) = overdefined`` for ``a != b``,
  ``meet(overdefined, _) = overdefined``.

This module implements that lattice plus integer-typed evaluation
helpers used by the SCCP Phase 4 pass (``evaluate_binary``,
``evaluate_compare``). It is intentionally small — SCCP is a forward
dataflow pass that walks the def-use graph and propagates lattice
values, so its correctness hinges on a well-defined meet and an
evaluate function that is faithful to LLVM's semantics for integer
ops (wrap on overflow for ``add``/``sub``/``mul``, match ``icmp``
predicates).
"""

from __future__ import annotations

from dataclasses import dataclass

from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses
from .integer_fold_contract import (
    FOLD_CONSTANT,
    fold_llvm_integer_binary,
    fold_llvm_integer_compare,
)


@dataclass(frozen=True)
class LatticeValue:
    """Three-point lattice element.

    States:

    - ``kind == "top"``         — no information yet,
    - ``kind == "constant"``    — value is exactly ``constant``,
    - ``kind == "overdefined"`` — known not to be a constant.
    """

    kind: str  # "top" | "constant" | "overdefined"
    constant: int | None = None
    bit_width: int | None = None  # relevant when kind == "constant"

    # Singleton constructors -----------------------------------------------
    @classmethod
    def top(cls) -> "LatticeValue":
        return _TOP

    @classmethod
    def overdefined(cls) -> "LatticeValue":
        return _OVERDEF

    @classmethod
    def const(cls, value: int, bit_width: int = 32) -> "LatticeValue":
        return cls(kind="constant", constant=int(value), bit_width=bit_width)

    # Predicates -----------------------------------------------------------
    def is_top(self) -> bool:
        return self.kind == "top"

    def is_constant(self) -> bool:
        return self.kind == "constant"

    def is_overdefined(self) -> bool:
        return self.kind == "overdefined"


_TOP = LatticeValue(kind="top")
_OVERDEF = LatticeValue(kind="overdefined")


def meet(a: LatticeValue, b: LatticeValue) -> LatticeValue:
    """Return the lattice meet (greatest lower bound).

    This implements the standard three-point meet: ``top`` absorbs
    nothing, ``overdefined`` absorbs everything, matching constants
    stay, differing constants drop to ``overdefined``.
    """
    if a.is_top():
        return b
    if b.is_top():
        return a
    if a.is_overdefined() or b.is_overdefined():
        return _OVERDEF
    # Both are constants.
    if a.constant == b.constant and a.bit_width == b.bit_width:
        return a
    return _OVERDEF


# ---------------------------------------------------------------------------
# Integer evaluation helpers
# ---------------------------------------------------------------------------


def _mask(width: int) -> int:
    return (1 << width) - 1


def _to_signed(value: int, width: int) -> int:
    value &= _mask(width)
    if value >= (1 << (width - 1)):
        value -= 1 << width
    return value


def _to_unsigned(value: int, width: int) -> int:
    return value & _mask(width)


def evaluate_binary(
    op: str,
    lhs: LatticeValue,
    rhs: LatticeValue,
    flags=(),
) -> LatticeValue:
    """Fold a binary op applied to two lattice values."""
    if lhs.is_top() or rhs.is_top():
        return _TOP
    if lhs.is_overdefined() or rhs.is_overdefined():
        return _OVERDEF
    assert lhs.bit_width == rhs.bit_width, "bit-width mismatch"
    w = lhs.bit_width or 32
    status, value = fold_llvm_integer_binary(
        op,
        w,
        lhs.constant or 0,
        rhs.constant or 0,
        flags,
    )
    if status == FOLD_CONSTANT:
        return LatticeValue.const(value, w)
    return _OVERDEF


def evaluate_compare(
    pred: str,
    lhs: LatticeValue,
    rhs: LatticeValue,
) -> LatticeValue:
    """Fold an icmp-style compare predicate to a 1-bit lattice value."""
    if lhs.is_top() or rhs.is_top():
        return _TOP
    if lhs.is_overdefined() or rhs.is_overdefined():
        return _OVERDEF
    w = lhs.bit_width or 32
    status, value = fold_llvm_integer_compare(
        pred,
        w,
        lhs.constant or 0,
        rhs.constant or 0,
    )
    if status == FOLD_CONSTANT:
        return LatticeValue.const(value, 1)
    return _OVERDEF


# ---------------------------------------------------------------------------
# Analysis result wrapper (not typically used directly — SCCP embeds the
# lattice state per-value — but registered for parity with upstream where
# downstream passes can query final values).
# ---------------------------------------------------------------------------


class ConstantLatticeResult(AnalysisResult):
    KEY = AnalysisKey("constant-lattice")

    def __init__(self, values: dict[str, LatticeValue]) -> None:
        self.values = values

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


def register_constant_lattice(am: AnalysisManager) -> None:
    """No-op registration hook — SCCP populates values inline."""
    am.register(
        ConstantLatticeResult.KEY,
        lambda _unit: ConstantLatticeResult(values={}),
    )
