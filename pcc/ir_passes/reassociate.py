"""Reassociate — canonical-order arithmetic expressions.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/Reassociate.cpp``
  implements :cpp:class:`llvm::ReassociatePass`. The full algorithm
  normalizes associative commutative chains (``add``, ``mul``,
  ``and``, ``or``, ``xor``, ``fadd``, ``fmul``) by:

  1. flattening each chain of the same opcode into a list of leaves,
  2. sorting leaves in ``rank`` order (rank = block DFS order + extra
     bits so operands that are "more constant" sink toward the RHS),
  3. re-emitting the chain in canonical right-leaning form,
  4. coalescing multiple identical leaves (``x + x → 2*x`` etc.).

Subset implemented here (labelled ``subset``):

- function-local constant sinking: move integer literals to the RHS,
- function-local constant folding across two-step chains for
  ``add`` / ``mul`` / ``and`` / ``or`` / ``xor``,
- sibling-chain constant fusion for patterns like
  ``(%x + c1) + (%y + c2)``,
- dead-inner cleanup after reassociation via local DCE.

This still does not implement LLVM's rank-based operand ordering,
global chain flattening, or repeated-factor extraction.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dce import dce_module_text
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ASSOC_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    %(?P<result>[\w\.]+)\s*=\s*
    (?P<op>add|mul|and|or|xor)
    (?P<flags>(?:\s+(?:nsw|nuw))*)
    \s+(?P<ty>i\d+)\s+
    (?P<lhs>[^,]+?)\s*,\s*(?P<rhs>[^,]+?)\s*$
    """,
    re.VERBOSE,
)


def _bit_width(ty: str) -> int:
    match = re.match(r"i(\d+)", ty)
    return int(match.group(1)) if match else 0


def _try_int(tok: str) -> int | None:
    tok = tok.strip()
    try:
        return int(tok)
    except ValueError:
        return None


def _normalize_unsigned(value: int, ty: str) -> int:
    width = _bit_width(ty)
    if width <= 0:
        return value
    return value & ((1 << width) - 1)


def _normalize_signed(value: int, ty: str) -> int:
    width = _bit_width(ty)
    if width <= 0:
        return value
    unsigned = value & ((1 << width) - 1)
    if unsigned >= (1 << (width - 1)):
        return unsigned - (1 << width)
    return unsigned


def _combine_constants(op: str, ty: str, lhs: int, rhs: int) -> int:
    lu = _normalize_unsigned(lhs, ty)
    ru = _normalize_unsigned(rhs, ty)
    if op == "add":
        return _normalize_signed(lu + ru, ty)
    if op == "mul":
        return _normalize_signed(lu * ru, ty)
    if op == "and":
        return _normalize_signed(lu & ru, ty)
    if op == "or":
        return _normalize_signed(lu | ru, ty)
    if op == "xor":
        return _normalize_signed(lu ^ ru, ty)
    raise ValueError(f"unsupported op: {op}")


def _single_constant_form(info: dict[str, str]) -> tuple[str, int] | None:
    lhs_const = _try_int(info["lhs"])
    rhs_const = _try_int(info["rhs"])
    if lhs_const is not None and rhs_const is None:
        return info["rhs"], lhs_const
    if rhs_const is not None and lhs_const is None:
        return info["lhs"], rhs_const
    return None


def _format_line(
    *,
    indent: str,
    result: str,
    op: str,
    flags: str,
    ty: str,
    lhs: str,
    rhs: str,
) -> str:
    return f"{indent}%{result} = {op}{flags} {ty} {lhs}, {rhs}\n"


class ReassociatePass(ModulePass):
    name = "pcc-reassociate"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = reassociate_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _one_function_pass(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    assoc: dict[str, dict[str, str]] = {}
    for idx, line in enumerate(lines):
        match = _ASSOC_RE.match(line.rstrip("\n"))
        if not match:
            continue
        assoc[match.group("result")] = {
            "line_idx": idx,
            "indent": match.group("indent"),
            "result": match.group("result"),
            "op": match.group("op"),
            "flags": match.group("flags"),
            "ty": match.group("ty"),
            "lhs": match.group("lhs").strip(),
            "rhs": match.group("rhs").strip(),
        }

    changed = False
    for name, info in assoc.items():
        op = info["op"]
        ty = info["ty"]
        lhs_const = _try_int(info["lhs"])
        rhs_const = _try_int(info["rhs"])

        # Canonicalize constants to the RHS.
        if lhs_const is not None and rhs_const is None:
            lines[info["line_idx"]] = _format_line(
                indent=info["indent"],
                result=name,
                op=op,
                flags=info["flags"],
                ty=ty,
                lhs=info["rhs"],
                rhs=info["lhs"],
            )
            info["lhs"], info["rhs"] = info["rhs"], info["lhs"]
            lhs_const, rhs_const = rhs_const, lhs_const
            changed = True

        # Fold (%inner op c1) op c2 -> %base op combined(c1, c2)
        if rhs_const is not None and info["lhs"].startswith("%"):
            inner = assoc.get(info["lhs"][1:])
            if (
                inner is not None
                and inner["op"] == op
                and inner["ty"] == ty
            ):
                inner_single = _single_constant_form(inner)
                if inner_single is not None:
                    base, c1 = inner_single
                    new_const = _combine_constants(op, ty, c1, rhs_const)
                    flags = info["flags"] if info["flags"] == inner["flags"] else ""
                    lines[info["line_idx"]] = _format_line(
                        indent=info["indent"],
                        result=name,
                        op=op,
                        flags=flags,
                        ty=ty,
                        lhs=base,
                        rhs=str(new_const),
                    )
                    info["lhs"], info["rhs"], info["flags"] = base, str(new_const), flags
                    rhs_const = new_const
                    changed = True

        # Fold sibling chains: (%x op c1) op (%y op c2)
        if info["lhs"].startswith("%") and info["rhs"].startswith("%"):
            left = assoc.get(info["lhs"][1:])
            right = assoc.get(info["rhs"][1:])
            if (
                left is not None
                and right is not None
                and left["op"] == right["op"] == op
                and left["ty"] == right["ty"] == ty
            ):
                left_single = _single_constant_form(left)
                right_single = _single_constant_form(right)
                if left_single is not None and right_single is not None:
                    left_var, c1 = left_single
                    right_var, c2 = right_single
                    merged = _combine_constants(op, ty, c1, c2)
                    merged_flags = (
                        left["flags"]
                        if left["flags"] == right["flags"] == info["flags"]
                        else ""
                    )
                    left_line = _format_line(
                        indent=left["indent"],
                        result=left["result"],
                        op=op,
                        flags=merged_flags,
                        ty=ty,
                        lhs=left_var,
                        rhs=str(merged),
                    )
                    current_line = _format_line(
                        indent=info["indent"],
                        result=name,
                        op=op,
                        flags=info["flags"],
                        ty=ty,
                        lhs=f"%{left['result']}",
                        rhs=right_var,
                    )
                    lines[left["line_idx"]] = left_line
                    lines[info["line_idx"]] = current_line
                    left["lhs"], left["rhs"], left["flags"] = left_var, str(merged), merged_flags
                    info["lhs"], info["rhs"] = f"%{left['result']}", right_var
                    changed = True

    if not changed:
        return fn_text, False
    rewritten = "".join(lines)
    rewritten, _ = dce_module_text(rewritten)
    return rewritten, True


def reassociate_text(ir_text: str) -> tuple[str, bool]:
    """Reassociate supported function-local constant chains."""
    out: list[str] = []
    changed = False
    in_function = False
    fn_lines: list[str] = []

    for line in ir_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not in_function and stripped.startswith("define "):
            in_function = True
            fn_lines = [line]
            continue
        if in_function:
            fn_lines.append(line)
            if stripped.startswith("}"):
                fn_text = "".join(fn_lines)
                current = fn_text
                fn_changed = False
                for _ in range(8):
                    current, local_changed = _one_function_pass(current)
                    if not local_changed:
                        break
                    fn_changed = True
                out.append(current)
                changed = changed or fn_changed
                in_function = False
                fn_lines = []
            continue
        out.append(line)

    if in_function and fn_lines:
        current = "".join(fn_lines)
        fn_changed = False
        for _ in range(8):
            current, local_changed = _one_function_pass(current)
            if not local_changed:
                break
            fn_changed = True
        out.append(current)
        changed = changed or fn_changed

    if not changed:
        return ir_text, False
    return "".join(out), True
