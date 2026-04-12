"""Sparse conditional constant propagation over the bootstrap SSA IR."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .ir import (
    SSABinaryOp,
    SSABlock,
    SSABranch,
    SSASwitch,
    SSACast,
    SSAConstant,
    SSAFieldAddr,
    SSAFunction,
    SSAJump,
    SSALoad,
    SSAStore,
    SSAParam,
    SSAPhi,
    SSAReturn,
    SSAUnaryOp,
    SSAUndef,
    SSAValue,
)


class LatticeKind(str, Enum):
    UNKNOWN = "unknown"
    CONSTANT = "constant"
    OVERDEFINED = "overdefined"


@dataclass(frozen=True, slots=True)
class SSALatticeValue:
    kind: LatticeKind
    constant: int | None = None
    is_safe: bool = False

    @classmethod
    def unknown(cls) -> "SSALatticeValue":
        return cls(LatticeKind.UNKNOWN, None, False)

    @classmethod
    def overdefined(cls) -> "SSALatticeValue":
        return cls(LatticeKind.OVERDEFINED, None, False)

    @classmethod
    def constant_value(cls, value: int, *, is_safe: bool = False) -> "SSALatticeValue":
        return cls(LatticeKind.CONSTANT, value, is_safe)


@dataclass(slots=True)
class SSASCCPResult:
    function_name: str
    values: dict[str, SSALatticeValue] = field(default_factory=dict)
    reachable_blocks: set[str] = field(default_factory=set)
    folded_branches: dict[str, str] = field(default_factory=dict)

    def constant_value_names(self) -> dict[str, int]:
        return {
            name: value.constant
            for name, value in self.values.items()
            if value.kind == LatticeKind.CONSTANT and value.constant is not None
        }

    def safe_constant_value_names(self) -> dict[str, int]:
        """Return only `is_safe` constants — safe to emit verbatim in codegen.

        Unsafe folds are results of ops whose C semantics depend on
        signedness (ordered compares, +/-/* under unsigned wrap, etc.) —
        the lattice stores a Python int but using that value directly
        in an LLVM instruction would produce the wrong result for the
        unsigned wrap case.
        """
        return {
            name: value.constant
            for name, value in self.values.items()
            if (
                value.kind == LatticeKind.CONSTANT
                and value.constant is not None
                and value.is_safe
            )
        }

    def lattice_for(self, value: SSAValue | None) -> SSALatticeValue:
        if value is None:
            return SSALatticeValue.unknown()
        if isinstance(value, SSAConstant):
            return SSALatticeValue.constant_value(value.value, is_safe=value.is_safe)
        if isinstance(value, SSAUndef):
            return SSALatticeValue.unknown()
        if isinstance(value, SSAParam):
            return self.values.get(value.name, SSALatticeValue.overdefined())
        return self.values.get(value.name, SSALatticeValue.unknown())


class SSASCCPAnalyzer:
    """Run SCCP on the minimal bootstrap SSA representation."""

    # Ordered comparisons (<, <=, >, >=) are excluded because their
    # result depends on signedness, which the SSA layer does not track.
    # Arithmetic ops (+, -, *, /, %) are excluded because unsigned
    # overflow wraps differently from Python's signed semantics —
    # EXCEPT when both operands are literal constants AND the folded
    # result fits in a signed 32-bit range. In that narrow case, the
    # result is deterministic (no wrap concern) and is used by
    # downstream branch-pruning to kill always-dead `if (4-4)` arms.
    _SAFE_BINARY_OPS = frozenset({
        "==", "!=",
        "&&", "||",
    })
    _SAFE_ARITH_OPS_FOR_CONSTANTS = frozenset({
        "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
    })
    _SAFE_UNARY_OPS = frozenset({"+", "-", "!"})
    _SIGNED_INT32_MIN = -(1 << 31)
    _SIGNED_INT32_MAX = (1 << 31) - 1

    def analyze(self, func: SSAFunction) -> SSASCCPResult:
        result = SSASCCPResult(function_name=func.name)
        result.reachable_blocks.add(func.entry_block)

        for param in func.params:
            result.values[param.name] = SSALatticeValue.overdefined()

        changed = True
        while changed:
            changed = False
            for block in func.blocks:
                if block.name not in result.reachable_blocks:
                    continue
                if self._visit_block(block, result):
                    changed = True

        return result

    def _visit_block(self, block: SSABlock, result: SSASCCPResult) -> bool:
        changed = False

        for instr in block.instructions:
            next_value = self._evaluate_instruction(instr, result, block)
            if self._update_value(result, instr.name, next_value):
                changed = True

        term = block.terminator
        if isinstance(term, SSAJump):
            if self._mark_reachable(result, term.target):
                changed = True
        elif isinstance(term, SSABranch):
            condition = self._value_state(term.condition, result)
            if (
                condition.kind == LatticeKind.CONSTANT
                and condition.constant is not None
                and condition.is_safe
            ):
                chosen = term.true_target if condition.constant else term.false_target
                result.folded_branches[block.name] = chosen
                if self._mark_reachable(result, chosen):
                    changed = True
            else:
                result.folded_branches.pop(block.name, None)
                for target in (term.true_target, term.false_target):
                    if self._mark_reachable(result, target):
                        changed = True
        elif isinstance(term, SSASwitch):
            value = self._value_state(term.value, result)
            if (
                value.kind == LatticeKind.CONSTANT
                and value.constant is not None
                and value.is_safe
            ):
                chosen = term.default_target
                for case_const, target in term.cases:
                    if case_const == value.constant:
                        chosen = target
                        break
                result.folded_branches[block.name] = chosen
                if self._mark_reachable(result, chosen):
                    changed = True
            else:
                result.folded_branches.pop(block.name, None)
                if self._mark_reachable(result, term.default_target):
                    changed = True
                for _, target in term.cases:
                    if self._mark_reachable(result, target):
                        changed = True
        elif isinstance(term, SSAReturn):
            pass

        return changed

    def _evaluate_instruction(
        self,
        instr,
        result: SSASCCPResult,
        block: SSABlock,
    ) -> SSALatticeValue:
        if isinstance(instr, SSAPhi):
            return self._evaluate_phi(instr, result, block)
        if isinstance(instr, SSABinaryOp):
            return self._evaluate_binary(instr, result)
        if isinstance(instr, SSACast):
            return self._evaluate_cast(instr, result)
        if isinstance(instr, SSAFieldAddr):
            return SSALatticeValue.overdefined()
        if isinstance(instr, SSALoad):
            return SSALatticeValue.overdefined()
        if isinstance(instr, SSAStore):
            return SSALatticeValue.overdefined()
        if isinstance(instr, SSAUnaryOp):
            return self._evaluate_unary(instr, result)
        return SSALatticeValue.overdefined()

    def _evaluate_phi(
        self,
        phi: SSAPhi,
        result: SSASCCPResult,
        block: SSABlock,
    ) -> SSALatticeValue:
        reachable_preds = {
            pred for pred in block.predecessors if pred in result.reachable_blocks
        }
        if not reachable_preds:
            return SSALatticeValue.unknown()

        current_constant: int | None = None
        saw_constant = False
        all_safe = True

        for pred_name, value in phi.incomings:
            if pred_name not in reachable_preds:
                continue
            lattice = self._value_state(value, result)
            if lattice.kind == LatticeKind.OVERDEFINED:
                return lattice
            if lattice.kind == LatticeKind.UNKNOWN:
                return lattice
            if lattice.constant is None:
                return SSALatticeValue.overdefined()
            if not saw_constant:
                current_constant = lattice.constant
                saw_constant = True
                all_safe = lattice.is_safe
                continue
            if lattice.constant != current_constant:
                return SSALatticeValue.overdefined()
            all_safe = all_safe and lattice.is_safe

        if saw_constant:
            return SSALatticeValue.constant_value(current_constant, is_safe=all_safe)
        return SSALatticeValue.unknown()

    def _evaluate_binary(
        self,
        instr: SSABinaryOp,
        result: SSASCCPResult,
    ) -> SSALatticeValue:
        left = self._value_state(instr.left, result)
        right = self._value_state(instr.right, result)

        if left.kind == LatticeKind.OVERDEFINED or right.kind == LatticeKind.OVERDEFINED:
            return SSALatticeValue.overdefined()
        if left.kind == LatticeKind.UNKNOWN or right.kind == LatticeKind.UNKNOWN:
            return SSALatticeValue.unknown()

        assert left.constant is not None
        assert right.constant is not None
        folded = self._fold_binary(instr.op, left.constant, right.constant)
        if folded is None:
            return SSALatticeValue.overdefined()
        op_is_always_safe = instr.op in self._SAFE_BINARY_OPS
        op_safe_for_constants = (
            instr.op in self._SAFE_ARITH_OPS_FOR_CONSTANTS
            and self._SIGNED_INT32_MIN <= folded <= self._SIGNED_INT32_MAX
        )
        return SSALatticeValue.constant_value(
            folded,
            is_safe=(
                left.is_safe
                and right.is_safe
                and (op_is_always_safe or op_safe_for_constants)
            ),
        )

    def _evaluate_unary(
        self,
        instr: SSAUnaryOp,
        result: SSASCCPResult,
    ) -> SSALatticeValue:
        operand = self._value_state(instr.operand, result)
        if operand.kind == LatticeKind.OVERDEFINED:
            return SSALatticeValue.overdefined()
        if operand.kind == LatticeKind.UNKNOWN:
            return SSALatticeValue.unknown()

        assert operand.constant is not None
        folded = self._fold_unary(instr.op, operand.constant)
        if folded is None:
            return SSALatticeValue.overdefined()
        return SSALatticeValue.constant_value(
            folded,
            is_safe=operand.is_safe and instr.op in self._SAFE_UNARY_OPS,
        )

    def _evaluate_cast(
        self,
        instr: SSACast,
        result: SSASCCPResult,
    ) -> SSALatticeValue:
        operand = self._value_state(instr.operand, result)
        if operand.kind != LatticeKind.CONSTANT or operand.constant is None:
            return operand
        return SSALatticeValue.constant_value(
            operand.constant,
            is_safe=operand.is_safe,
        )

    def _value_state(
        self,
        value: SSAValue | None,
        result: SSASCCPResult,
    ) -> SSALatticeValue:
        return result.lattice_for(value)

    @staticmethod
    def _fold_binary(op: str, left: int, right: int) -> int | None:
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                return None
            return int(left / right)
        if op == "%":
            if right == 0:
                return None
            return left % right
        if op == "<<":
            return left << right
        if op == ">>":
            return left >> right
        if op == "&":
            return left & right
        if op == "|":
            return left | right
        if op == "^":
            return left ^ right
        if op == "==":
            return int(left == right)
        if op == "!=":
            return int(left != right)
        if op == "<":
            return int(left < right)
        if op == "<=":
            return int(left <= right)
        if op == ">":
            return int(left > right)
        if op == ">=":
            return int(left >= right)
        if op == "&&":
            return int(bool(left) and bool(right))
        if op == "||":
            return int(bool(left) or bool(right))
        return None

    @staticmethod
    def _fold_unary(op: str, operand: int) -> int | None:
        if op == "+":
            return operand
        if op == "-":
            return -operand
        if op == "!":
            return int(not operand)
        if op == "~":
            return ~operand
        return None

    @staticmethod
    def _mark_reachable(result: SSASCCPResult, block_name: str) -> bool:
        before = len(result.reachable_blocks)
        result.reachable_blocks.add(block_name)
        return len(result.reachable_blocks) != before

    @staticmethod
    def _update_value(
        result: SSASCCPResult,
        name: str,
        next_value: SSALatticeValue,
    ) -> bool:
        current = result.values.get(name, SSALatticeValue.unknown())
        merged = SSASCCPAnalyzer._merge_lattice(current, next_value)
        if merged == current:
            return False
        result.values[name] = merged
        return True

    @staticmethod
    def _merge_lattice(
        current: SSALatticeValue,
        next_value: SSALatticeValue,
    ) -> SSALatticeValue:
        if current.kind == LatticeKind.OVERDEFINED or next_value.kind == LatticeKind.UNKNOWN:
            return current
        if next_value.kind == LatticeKind.OVERDEFINED:
            return next_value
        if current.kind == LatticeKind.UNKNOWN:
            return next_value
        if next_value.kind == LatticeKind.CONSTANT and current.constant == next_value.constant:
            return SSALatticeValue.constant_value(
                current.constant,
                is_safe=current.is_safe and next_value.is_safe,
            )
        return SSALatticeValue.overdefined()
