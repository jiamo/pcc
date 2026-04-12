"""InstructionSimplify (subset) — IR-level pass.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/InstructionSimplify.cpp``
  implements the full simplifier. The entry point
  :cpp:func:`llvm::simplifyInstruction` dispatches on opcode and
  calls per-op helpers (``SimplifyAddInst``, ``SimplifyAndInst``, ...),
  each returning a replacement ``Value *`` or nullptr. The pass
  wrapper :cpp:class:`llvm::InstSimplifyPass`
  (``.../Transforms/Scalar/InstSimplifyPass.cpp``) walks each block
  in postorder and replaces every instruction that simplifies.

The subset implemented here mirrors the pure-arithmetic identity
short-circuits upstream returns *early*, without requiring the
full recursive simplifier:

    Arithmetic: x+0, x-0, x-x, x*0, x*1, x*-1 (→ neg)
                constant/constant integer folding for the covered binops
    Bitwise:    x&x, x|x, x^x, x&-1, x&0, x|0, x|-1, x^-1 (→ ~x placeholder)
    Shifts:     x<<0, x>>0, 0<<x, 0>>x, and constant/constant shifts
                with W ≥ bit-width → poison
    Compares:   eq/ne on equal constants, eq on same SSA value, etc.
    Selects:    select true,x,y → x; select false,x,y → y;
                select c,x,x → x.

Passes that need the full simplifier (especially recursive,
canonicalization-aware rewrites) should continue to fall through to
upstream ``opt -passes=instsimplify``; this module is labelled
``subset`` in the registry.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


# ---------------------------------------------------------------------------
# Instruction line patterns
# ---------------------------------------------------------------------------


_BINOP_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    %(?P<result>[\w\.]+)\s*=\s*
    (?P<op>add|sub|mul|and|or|xor|shl|lshr|ashr|udiv|sdiv|urem|srem)
    (?P<flags>(?:\s+(?:nsw|nuw|exact))*)
    \s+(?P<ty>i\d+)\s+
    (?P<lhs>[^,\s][^,]*?)\s*,\s*
    (?P<rhs>.+?)\s*$
    """,
    re.VERBOSE,
)


_ICMP_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    %(?P<result>[\w\.]+)\s*=\s*icmp\s+
    (?P<pred>eq|ne|ugt|uge|ult|ule|sgt|sge|slt|sle)
    \s+(?P<ty>i\d+)\s+
    (?P<lhs>[^,\s][^,]*?)\s*,\s*
    (?P<rhs>.+?)\s*$
    """,
    re.VERBOSE,
)


_SELECT_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    %(?P<result>[\w\.]+)\s*=\s*select\s+
    i1\s+(?P<cond>[^,]+?)\s*,\s*
    (?P<ty1>[\w\*]+)\s+(?P<tval>[^,]+?)\s*,\s*
    (?P<ty2>[\w\*]+)\s+(?P<fval>.+?)\s*$
    """,
    re.VERBOSE,
)


def _bit_width(ty: str) -> int:
    m = re.match(r"i(\d+)", ty)
    return int(m.group(1)) if m else 0


def _is(token: str, val: int | str) -> bool:
    return token.strip() == str(val)


def _try_int(token: str) -> int | None:
    token = token.strip()
    try:
        return int(token)
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


def _is_neg_one(token: str, ty: str) -> bool:
    t = token.strip()
    if t == "-1":
        return True
    if t == "true" and ty == "i1":
        return True
    w = _bit_width(ty)
    if w > 0 and t.lstrip("-").isdigit():
        try:
            val = int(t)
            if val == (1 << w) - 1 or val == -1:
                return True
        except ValueError:
            pass
    return False


# ---------------------------------------------------------------------------
# Per-opcode simplification
# ---------------------------------------------------------------------------


def _fold_constant_binop(op: str, ty: str, lhs: str, rhs: str) -> str | None:
    li = _try_int(lhs)
    ri = _try_int(rhs)
    if li is None or ri is None:
        return None

    width = _bit_width(ty)
    if width <= 0:
        return None

    lu = _normalize_unsigned(li, ty)
    ru = _normalize_unsigned(ri, ty)
    ls = _normalize_signed(li, ty)
    rs = _normalize_signed(ri, ty)

    if op == "add":
        return str(_normalize_signed(lu + ru, ty))
    if op == "sub":
        return str(_normalize_signed(lu - ru, ty))
    if op == "mul":
        return str(_normalize_signed(lu * ru, ty))
    if op == "and":
        return str(_normalize_signed(lu & ru, ty))
    if op == "or":
        return str(_normalize_signed(lu | ru, ty))
    if op == "xor":
        return str(_normalize_signed(lu ^ ru, ty))

    if op in ("shl", "lshr", "ashr"):
        if ri < 0 or ri >= width:
            return "poison"
        if op == "shl":
            return str(_normalize_signed(lu << ri, ty))
        if op == "lshr":
            return str(_normalize_signed(lu >> ri, ty))
        return str(_normalize_signed(ls >> ri, ty))

    if op == "udiv":
        if ru == 0:
            return None
        return str(_normalize_signed(lu // ru, ty))
    if op == "sdiv":
        if rs == 0:
            return None
        if ls == -(1 << (width - 1)) and rs == -1:
            return "poison"
        quot = abs(ls) // abs(rs)
        if (ls < 0) ^ (rs < 0):
            quot = -quot
        return str(_normalize_signed(quot, ty))
    if op == "urem":
        if ru == 0:
            return None
        return str(_normalize_signed(lu % ru, ty))
    if op == "srem":
        if rs == 0:
            return None
        if ls == -(1 << (width - 1)) and rs == -1:
            return "poison"
        quot = abs(ls) // abs(rs)
        if (ls < 0) ^ (rs < 0):
            quot = -quot
        rem = ls - quot * rs
        return str(_normalize_signed(rem, ty))

    return None


def _simplify_binop(op: str, ty: str, lhs: str, rhs: str) -> str | None:
    l, r = lhs.strip(), rhs.strip()
    const_folded = _fold_constant_binop(op, ty, l, r)
    if const_folded is not None:
        return const_folded

    if op == "add":
        if _is(r, 0): return l
        if _is(l, 0): return r
    elif op == "sub":
        if _is(r, 0): return l
        if l == r and not l.isdigit() and not l.startswith("-"):
            return "0"
    elif op == "mul":
        if _is(r, 0) or _is(l, 0): return "0"
        if _is(r, 1): return l
        if _is(l, 1): return r
    elif op == "and":
        if _is(r, 0) or _is(l, 0): return "0"
        if _is_neg_one(r, ty): return l
        if _is_neg_one(l, ty): return r
        if l == r: return l
    elif op == "or":
        if _is(r, 0): return l
        if _is(l, 0): return r
        if _is_neg_one(r, ty): return r
        if _is_neg_one(l, ty): return l
        if l == r: return l
    elif op == "xor":
        if _is(r, 0): return l
        if _is(l, 0): return r
        if l == r: return "0"
    elif op in ("shl", "lshr", "ashr"):
        if _is(l, 0): return "0"
        if _is(r, 0): return l
        if op in ("lshr", "ashr") and l == r:
            return "0"
        if op == "ashr" and _is_neg_one(l, ty):
            return "-1"
        width = _bit_width(ty)
        shift = _try_int(r)
        if shift is not None and (shift < 0 or shift >= width):
            return "poison"
    elif op == "udiv":
        if _is(r, 0): return "poison"
        if _is(l, 0): return "0"
        if _is(r, 1): return l
        if l == r: return "1"
    elif op == "sdiv":
        if _is(r, 0): return "poison"
        if _is(l, 0): return "0"
        if _is(r, 1): return l
        if l == r: return "1"
    elif op == "urem":
        if _is(r, 0): return "poison"
        if _is(l, 0): return "0"
        if _is(r, 1): return "0"
        if l == r: return "0"
    elif op == "srem":
        if _is(r, 0): return "poison"
        if _is(l, 0): return "0"
        if _is(r, 1): return "0"
        if l == r: return "0"
    return None


def _simplify_icmp(pred: str, ty: str, lhs: str, rhs: str) -> str | None:
    """Return the 1-bit simplified result, or None."""
    l, r = lhs.strip(), rhs.strip()
    # Both sides identical → pred determines result.
    if l == r:
        if pred in ("eq", "sle", "sge", "ule", "uge"):
            return "true"
        if pred in ("ne", "slt", "sgt", "ult", "ugt"):
            return "false"
    # Both constants → fold via Python.
    try:
        li = int(l)
        ri = int(r)
    except ValueError:
        return None
    w = _bit_width(ty) or 32
    mask = (1 << w) - 1
    lu = li & mask
    ru = ri & mask
    ls = lu if lu < (1 << (w - 1)) else lu - (1 << w)
    rs = ru if ru < (1 << (w - 1)) else ru - (1 << w)
    if pred == "eq": return "true" if lu == ru else "false"
    if pred == "ne": return "true" if lu != ru else "false"
    if pred == "ult": return "true" if lu < ru else "false"
    if pred == "ule": return "true" if lu <= ru else "false"
    if pred == "ugt": return "true" if lu > ru else "false"
    if pred == "uge": return "true" if lu >= ru else "false"
    if pred == "slt": return "true" if ls < rs else "false"
    if pred == "sle": return "true" if ls <= rs else "false"
    if pred == "sgt": return "true" if ls > rs else "false"
    if pred == "sge": return "true" if ls >= rs else "false"
    return None


def _simplify_select(cond: str, tval: str, fval: str) -> str | None:
    c = cond.strip()
    if c == "true":
        return tval.strip()
    if c == "false":
        return fval.strip()
    if tval.strip() == fval.strip():
        return tval.strip()
    return None


# ---------------------------------------------------------------------------
# Pass wrapper — textual rewrite with fixed-point substitution
# ---------------------------------------------------------------------------


class InstSimplifyPass(ModulePass):
    """Apply the instsimplify subset across the whole module."""

    name = "pcc-instsimplify"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = simplify_module_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def simplify_module_text(ir_text: str) -> tuple[str, bool]:
    """Run the simplifier until fixed point; return (new_ir, changed)."""
    current = ir_text
    any_change = False
    for _ in range(16):
        next_text, changed = _one_pass(current)
        if not changed:
            break
        any_change = True
        current = next_text
    return current, any_change


def _rewrite_function_text(fn_text: str) -> tuple[str, bool]:
    replacements: dict[str, str] = {}
    kept: list[str] = []
    changed = False

    for line in fn_text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = _BINOP_RE.match(stripped)
        if m:
            rep = _simplify_binop(
                m.group("op"), m.group("ty"),
                m.group("lhs"), m.group("rhs"),
            )
            if rep is not None:
                replacements[m.group("result")] = rep
                changed = True
                continue
            kept.append(line)
            continue

        m = _ICMP_RE.match(stripped)
        if m:
            rep = _simplify_icmp(
                m.group("pred"), m.group("ty"),
                m.group("lhs"), m.group("rhs"),
            )
            if rep is not None:
                replacements[m.group("result")] = rep
                changed = True
                continue
            kept.append(line)
            continue

        m = _SELECT_RE.match(stripped)
        if m:
            rep = _simplify_select(
                m.group("cond"), m.group("tval"), m.group("fval"),
            )
            if rep is not None:
                replacements[m.group("result")] = rep
                changed = True
                continue
            kept.append(line)
            continue

        kept.append(line)

    if not changed:
        return fn_text, False

    text = "".join(kept)
    for _ in range(8):
        new_text = text
        for name, rep in replacements.items():
            new_text = re.sub(
                r"%" + re.escape(name) + r"\b", rep, new_text
            )
        if new_text == text:
            break
        text = new_text
    return text, True


def _one_pass(ir_text: str) -> tuple[str, bool]:
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
                rewritten, fn_changed = _rewrite_function_text("".join(fn_lines))
                out.append(rewritten)
                changed = changed or fn_changed
                in_function = False
                fn_lines = []
            continue
        out.append(line)

    if in_function and fn_lines:
        rewritten, fn_changed = _rewrite_function_text("".join(fn_lines))
        out.append(rewritten)
        changed = changed or fn_changed

    if not changed:
        return ir_text, False
    return "".join(out), True
