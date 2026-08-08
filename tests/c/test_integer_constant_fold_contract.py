"""Finite, independent model checks for pcc-owned integer folds."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from pcc.ast import c_ast
from pcc.codegen.c_codegen import LLVMCodeGenerator
from pcc.codegen.c_declaration_state import CodegenError
from pcc.codegen.c_integer_fold_contract import (
    C_INTEGER_BINARY_OPS,
    fold_c_integer_binary,
    fold_c_integer_unary,
)
from pcc.ir_passes.constant_lattice import LatticeValue, evaluate_binary
from pcc.ir_passes.instcombine import _fold_const_binop
from pcc.ir_passes.instsimplify import _fold_constant_binop, _simplify_icmp
from pcc.ir_passes.integer_fold_contract import (
    LLVM_INTEGER_BINARY_OPS,
    LLVM_INTEGER_COMPARE_PREDS,
    fold_llvm_integer_binary,
    fold_llvm_integer_compare,
)
from pcc.ir_passes.loop_unroll import _try_fold_const_binop
from pcc.ir_passes.reassociate import _combine_constants
from pcc.parse.c_parser import CParser
from pcc.ssa import LatticeKind, SSABuilder, SSASCCPAnalyzer


ROOT = Path(__file__).resolve().parents[2]
CONSTANT = "constant"
POISON = "poison"
UNSUPPORTED = "unsupported"


def _unsigned(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


def _signed(value: int, width: int) -> int:
    raw = _unsigned(value, width)
    return raw - (1 << width) if raw & (1 << (width - 1)) else raw


def _trunc_div(lhs: int, rhs: int) -> int:
    quotient = abs(lhs) // abs(rhs)
    return -quotient if (lhs < 0) != (rhs < 0) else quotient


def _llvm_reference(
    op: str,
    width: int,
    lhs: int,
    rhs: int,
    flags=(),
) -> tuple[str, int]:
    flags = frozenset(flags)
    valid = (
        flags <= {"nsw", "nuw"}
        if op in ("add", "sub", "mul", "shl")
        else flags <= {"exact"}
        if op in ("lshr", "ashr", "udiv", "sdiv")
        else not flags
    )
    if width <= 0 or not valid:
        return UNSUPPORTED, 0
    mask = (1 << width) - 1
    lu, ru = lhs & mask, rhs & mask
    ls, rs = _signed(lu, width), _signed(ru, width)
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    if op in ("add", "sub", "mul"):
        unsigned_math = (
            lu + ru if op == "add" else lu - ru if op == "sub" else lu * ru
        )
        signed_math = (
            ls + rs if op == "add" else ls - rs if op == "sub" else ls * rs
        )
        if "nuw" in flags and not 0 <= unsigned_math <= mask:
            return POISON, 0
        if "nsw" in flags and not lo <= signed_math <= hi:
            return POISON, 0
        return CONSTANT, unsigned_math & mask
    if op == "and":
        return CONSTANT, lu & ru
    if op == "or":
        return CONSTANT, lu | ru
    if op == "xor":
        return CONSTANT, lu ^ ru
    if op in ("shl", "lshr", "ashr"):
        if ru >= width:
            return POISON, 0
        if op == "shl":
            unsigned_math, signed_math = lu << ru, ls * (1 << ru)
            if "nuw" in flags and unsigned_math > mask:
                return POISON, 0
            if "nsw" in flags and not lo <= signed_math <= hi:
                return POISON, 0
            return CONSTANT, unsigned_math & mask
        if "exact" in flags and ru and lu & ((1 << ru) - 1):
            return POISON, 0
        if op == "lshr":
            return CONSTANT, lu >> ru
        return CONSTANT, _unsigned(ls >> ru, width)
    if op in ("udiv", "urem"):
        if ru == 0:
            return POISON, 0
        if op == "udiv":
            if "exact" in flags and lu % ru:
                return POISON, 0
            return CONSTANT, lu // ru
        return CONSTANT, lu % ru
    if op in ("sdiv", "srem"):
        if rs == 0:
            return POISON, 0
        if op == "sdiv" and ls == lo and rs == -1:
            return POISON, 0
        quotient = _trunc_div(ls, rs)
        remainder = ls - quotient * rs
        if op == "sdiv":
            if "exact" in flags and remainder:
                return POISON, 0
            return CONSTANT, _unsigned(quotient, width)
        return CONSTANT, _unsigned(remainder, width)
    return UNSUPPORTED, 0


def _llvm_compare_reference(
    pred: str,
    width: int,
    lhs: int,
    rhs: int,
) -> tuple[str, int]:
    if width <= 0 or pred not in LLVM_INTEGER_COMPARE_PREDS:
        return UNSUPPORTED, 0
    lu, ru = _unsigned(lhs, width), _unsigned(rhs, width)
    ls, rs = _signed(lu, width), _signed(ru, width)
    relations = {
        "eq": lu == ru,
        "ne": lu != ru,
        "ult": lu < ru,
        "ule": lu <= ru,
        "ugt": lu > ru,
        "uge": lu >= ru,
        "slt": ls < rs,
        "sle": ls <= rs,
        "sgt": ls > rs,
        "sge": ls >= rs,
    }
    return CONSTANT, 1 if relations[pred] else 0


def _c_reference(
    op: str,
    width: int,
    unsigned: bool,
    lhs: int,
    rhs: int,
) -> tuple[str, int]:
    if width <= 0:
        return UNSUPPORTED, 0
    mask = (1 << width) - 1
    lu, ru = lhs & mask, rhs & mask
    ln = lu if unsigned else _signed(lu, width)
    rn = ru if unsigned else _signed(ru, width)
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    if op in ("+", "-", "*"):
        result = ln + rn if op == "+" else ln - rn if op == "-" else ln * rn
        if not unsigned and not lo <= result <= hi:
            return POISON, 0
        raw = result & mask
        return CONSTANT, raw if unsigned else _signed(raw, width)
    if op in ("&", "|", "^"):
        result = lu & ru if op == "&" else lu | ru if op == "|" else lu ^ ru
        return CONSTANT, result if unsigned else _signed(result, width)
    if op in ("/", "%"):
        if rn == 0 or (not unsigned and ln == lo and rn == -1):
            return POISON, 0
        quotient = lu // ru if unsigned else _trunc_div(ln, rn)
        remainder = lu % ru if unsigned else ln - quotient * rn
        return CONSTANT, quotient if op == "/" else remainder
    if op in ("<<", ">>"):
        shift = rhs
        if shift < 0 or shift >= width:
            return POISON, 0
        if op == "<<":
            if not unsigned and ln < 0:
                return POISON, 0
            result = lu << shift if unsigned else ln << shift
            if not unsigned and not lo <= result <= hi:
                return POISON, 0
            raw = result & mask
            return CONSTANT, raw if unsigned else _signed(raw, width)
        return CONSTANT, lu >> shift if unsigned else ln >> shift
    if op in ("==", "!=", "<", "<=", ">", ">="):
        relation = {
            "==": ln == rn,
            "!=": ln != rn,
            "<": ln < rn,
            "<=": ln <= rn,
            ">": ln > rn,
            ">=": ln >= rn,
        }[op]
        return CONSTANT, 1 if relation else 0
    return UNSUPPORTED, 0


def test_llvm_integer_binary_rules_match_independent_exhaustive_i8_model():
    for op in LLVM_INTEGER_BINARY_OPS:
        for lhs in range(256):
            for rhs in range(256):
                assert fold_llvm_integer_binary(op, 8, lhs, rhs) == _llvm_reference(
                    op, 8, lhs, rhs
                )


def test_llvm_integer_compare_rules_match_independent_exhaustive_i8_model():
    for pred in LLVM_INTEGER_COMPARE_PREDS:
        for lhs in range(256):
            for rhs in range(256):
                assert fold_llvm_integer_compare(pred, 8, lhs, rhs) == (
                    _llvm_compare_reference(pred, 8, lhs, rhs)
                )


def test_llvm_poison_flags_match_independent_exhaustive_i4_model():
    flag_rows = {
        "add": ((), ("nsw",), ("nuw",), ("nsw", "nuw")),
        "sub": ((), ("nsw",), ("nuw",), ("nsw", "nuw")),
        "mul": ((), ("nsw",), ("nuw",), ("nsw", "nuw")),
        "shl": ((), ("nsw",), ("nuw",), ("nsw", "nuw")),
        "lshr": ((), ("exact",)),
        "ashr": ((), ("exact",)),
        "udiv": ((), ("exact",)),
        "sdiv": ((), ("exact",)),
    }
    for op, rows in flag_rows.items():
        for flags in rows:
            for lhs in range(16):
                for rhs in range(16):
                    assert fold_llvm_integer_binary(op, 4, lhs, rhs, flags) == (
                        _llvm_reference(op, 4, lhs, rhs, flags)
                    )


def test_c_integer_rules_match_independent_exhaustive_i8_model():
    for unsigned in (False, True):
        for op in C_INTEGER_BINARY_OPS:
            for lhs_raw in range(256):
                lhs = lhs_raw if unsigned else _signed(lhs_raw, 8)
                for rhs_raw in range(256):
                    rhs = rhs_raw if unsigned else _signed(rhs_raw, 8)
                    assert fold_c_integer_binary(op, 8, unsigned, lhs, rhs) == (
                        _c_reference(op, 8, unsigned, lhs, rhs)
                    )


def test_c_unary_rules_cover_signed_min_and_unsigned_wrap():
    assert fold_c_integer_unary("-", 8, False, -128) == (POISON, 0)
    assert fold_c_integer_unary("-", 8, True, 1) == (CONSTANT, 255)
    assert fold_c_integer_unary("~", 8, False, 0) == (CONSTANT, -1)
    assert fold_c_integer_unary("!", 8, False, -1) == (CONSTANT, 0)


class _ConstExprHarness:
    _eval_const_expr = LLVMCodeGenerator._eval_const_expr


def _int(text: str) -> c_ast.Constant:
    return c_ast.Constant("int", text)


def _binary(op: str, lhs, rhs) -> c_ast.BinaryOp:
    return c_ast.BinaryOp(op, lhs, rhs)


def test_c_codegen_constant_expression_uses_contract_and_short_circuits():
    source = inspect.getsource(LLVMCodeGenerator._eval_const_expr)
    assert "_fold_c_integer_binary(" in source
    assert "_fold_c_integer_unary(" in source
    harness = _ConstExprHarness()

    unsigned_wrap = harness._eval_const_expr(_binary("-", _int("0U"), _int("1U")))
    assert int(unsigned_wrap) == 0xFFFFFFFF
    assert unsigned_wrap.is_unsigned

    signed_unsigned_compare = harness._eval_const_expr(
        _binary("<", c_ast.UnaryOp("-", _int("1")), _int("1U"))
    )
    assert int(signed_unsigned_compare) == 0

    poison_rhs = _binary("/", _int("1"), _int("0"))
    assert int(harness._eval_const_expr(_binary("&&", _int("0"), poison_rhs))) == 0
    assert int(harness._eval_const_expr(_binary("||", _int("1"), poison_rhs))) == 1


@pytest.mark.parametrize(
    "expr",
    [
        _binary("+", _int("2147483647"), _int("1")),
        _binary("<<", _int("1"), _int("32")),
        _binary("<<", c_ast.UnaryOp("-", _int("1")), _int("1")),
        _binary(
            "/",
            _binary("-", c_ast.UnaryOp("-", _int("2147483647")), _int("1")),
            c_ast.UnaryOp("-", _int("1")),
        ),
    ],
)
def test_c_codegen_constant_expression_rejects_undefined_integer_rows(expr):
    with pytest.raises(CodegenError, match="undefined integer constant expression"):
        _ConstExprHarness()._eval_const_expr(expr)


def test_all_pcc_ir_fold_adapters_share_poison_and_width_contract():
    assert evaluate_binary(
        "add", LatticeValue.const(127, 8), LatticeValue.const(1, 8), ("nsw",)
    ).is_overdefined()
    assert _fold_constant_binop("add", "i8", "127", "1", " nsw") == "poison"
    assert _fold_const_binop("add", "i8", "127", "1", " nsw") == "poison"
    assert _try_fold_const_binop("%x = add nsw i8 127, 1") == "poison"
    assert _combine_constants("add", "i8", 127, 1) == -128
    assert _simplify_icmp("slt", "i8", "255", "0") == "true"


def _bootstrap_ssa_return_lattice(expression: str):
    source = f"int folded(void) {{ return {expression}; }}"
    module = CParser(
        lex_optimize=True, yacc_debug=False, yacc_optimize=True
    ).parse(source)
    function = SSABuilder().build_function(module.ext[0])
    result = SSASCCPAnalyzer().analyze(function)
    returns = [
        block.terminator.value
        for block in function.blocks
        if block.name in result.reachable_blocks
        and block.terminator is not None
        and block.terminator.__class__.__name__ == "SSAReturn"
    ]
    assert len(returns) == 1
    return result.lattice_for(returns[0])


def test_bootstrap_ssa_adapter_uses_bit_precise_c_integer_contract():
    signed_remainder = _bootstrap_ssa_return_lattice("-7 % 3")
    assert signed_remainder.constant == -1
    assert signed_remainder.is_safe

    promoted_complement = _bootstrap_ssa_return_lattice("~(unsigned char)0")
    assert promoted_complement.constant == -1
    assert promoted_complement.is_safe

    unsigned_wrap = _bootstrap_ssa_return_lattice("0U - 1U")
    assert unsigned_wrap.constant == 0xFFFFFFFF
    # Suffixed literals are not yet certified for direct SSA replacement;
    # their mathematical lattice value must still obey the unsigned contract.
    assert not unsigned_wrap.is_safe

    converted_compare = _bootstrap_ssa_return_lattice("-1 < 1U")
    assert converted_compare.constant == 0
    assert not converted_compare.is_safe

    for poison in ("2147483647 + 1", "1 << -1", "1 << 32"):
        lattice = _bootstrap_ssa_return_lattice(poison)
        assert lattice.kind == LatticeKind.OVERDEFINED


def test_fold_inventory_names_every_current_semantic_owner_and_adapter():
    inventory = json.loads((ROOT / "tests/constant_fold_inventory.json").read_text())
    assert inventory["schema_version"] == 1
    assert inventory["task_id"] == "LLVMREF-P3-ALIVE2-CONSTFOLD"
    for row in inventory["semantic_owners"]:
        source = (ROOT / row["path"]).read_text()
        assert f"def {row['symbol']}(" in source, row
    for row in inventory["semantic_owners"] + inventory["fold_sites"]:
        path = ROOT / row["path"]
        assert path.is_file(), row
        source = path.read_text()
        for call in row.get("owner_calls", ()):
            assert call in source, row

    listed_calls = {
        (row["path"], call)
        for row in inventory["fold_sites"]
        for call in row["owner_calls"]
    }
    definitions = {
        "_fold_c_integer_binary(": "pcc/codegen/c_integer_fold_contract.py",
        "_fold_c_integer_unary(": "pcc/codegen/c_integer_fold_contract.py",
        "fold_llvm_integer_binary(": "pcc/ir_passes/integer_fold_contract.py",
        "fold_llvm_integer_compare(": "pcc/ir_passes/integer_fold_contract.py",
        "evaluate_binary(": "pcc/ir_passes/constant_lattice.py",
        "evaluate_compare(": "pcc/ir_passes/constant_lattice.py",
    }
    for call, definition_path in definitions.items():
        call_name = call.removesuffix("(")
        discovered = set()
        for path in (ROOT / "pcc").rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative == definition_path:
                continue
            source = path.read_text()
            if call_name not in source:
                continue
            tree = ast.parse(source)
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == call_name
                for node in ast.walk(tree)
            ):
                discovered.add((relative, call))
        assert discovered == {row for row in listed_calls if row[1] == call}
    assert inventory["excluded"]
