"""Float2Int — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/Float2Int.cpp``
  uses interval analysis to convert subgraphs of floating-point
  arithmetic into equivalent integer arithmetic when every reachable
  value provably lies inside the destination integer range.

Subset implemented here (labelled ``subset``):

- Fold a bit-exact round-trip:

  ``%t = sitofp iN %x to FP``    (integer → float)
  ``%v = fptosi FP  %t  to iN``  (float   → integer)

  When ``FP`` has enough mantissa bits to represent every value of
  ``iN`` exactly, rewrite the float round-trip to the same integer
  ext/trunc shape upstream ``float2int`` emits:

  - signed: ``sext iN %x to iM`` then ``trunc iM %wide to iN``
  - unsigned: ``zext iN %x to iM`` then ``trunc iM %wide to iN``

  where ``iM`` is ``i32`` for ``i8``/``i16`` inputs and ``i64`` for
  ``i32`` inputs. Constant inputs fold directly to the original
  constant.

- Exact widths handled (upstream Float2Int folds many more shapes):
  - ``i8`` via ``float``,
  - ``i8`` via ``double``,
  - ``i16`` via ``float``,
  - ``i16`` via ``double``,
  - ``i32`` via ``double``.

  ``i32 → float`` and ``i64 → double`` are **not** folded because
  their mantissa is too narrow to hold every integer exactly.
- Signed and unsigned variants (``sitofp``/``uitofp`` with matching
  ``fptosi``/``fptoui``) are both supported. Mixed signed/unsigned
  chains are rejected.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ITOFP_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    (?P<op>sitofp|uitofp)\s+
    (?P<ity>i(?P<ibits>\d+))\s+(?P<val>[^\s,]+)\s+
    to\s+(?P<fty>float|double)\s*$
    """,
    re.VERBOSE,
)
_FPTOI_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    (?P<op>fptosi|fptoui)\s+
    (?P<fty>float|double)\s+(?P<val>[^\s,]+)\s+
    to\s+(?P<ity>i(?P<ibits>\d+))\s*$
    """,
    re.VERBOSE,
)

# (int_bits, fp_type) pairs where every iN value round-trips exactly
# through the FP type.
_EXACT_ROUND_TRIPS: frozenset[tuple[int, str]] = frozenset(
    {
        (8, "float"),
        (8, "double"),
        (16, "float"),
        (16, "double"),
        (32, "double"),
    }
)

_SIGNED_PAIR = {("sitofp", "fptosi"), ("uitofp", "fptoui")}


class Float2IntIRPass(ModulePass):
    """Round-trip ``fptosi(sitofp(%x))`` identity folder (subset)."""

    name = "pcc-float2int"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = float2int_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def float2int_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    any_changed = False
    for fn in mut.functions:
        if _fold_roundtrips_in_function(fn):
            any_changed = True
    if not any_changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _fold_roundtrips_in_function(fn: Function) -> bool:
    # 1. Index all int-to-float casts.
    itofp_info: dict[str, tuple[str, int, str, str]] = {}
    use_counts = _ssa_use_counts(fn)
    for block in fn.blocks:
        for inst in block.instructions:
            m = _ITOFP_RE.match(inst.text.rstrip("\n"))
            if m is None:
                continue
            itofp_info[m.group("res")] = (
                m.group("op"),
                int(m.group("ibits")),
                m.group("fty"),
                m.group("val"),
            )

    # 2. Walk for matching float-to-int casts whose input matches a
    #    known int-to-float.
    replacements: dict[str, str] = {}
    inserted_by_result: dict[str, list[Instruction]] = {}
    for block in fn.blocks:
        for inst in block.instructions:
            m = _FPTOI_RE.match(inst.text.rstrip("\n"))
            if m is None:
                continue
            fp_val = m.group("val")
            if not fp_val.startswith("%"):
                continue
            key = fp_val[1:]
            source = itofp_info.get(key)
            if source is None:
                continue
            if use_counts.get(key, 0) != 1:
                continue
            itofp_op, ibits, fty, orig_val = source
            if (itofp_op, m.group("op")) not in _SIGNED_PAIR:
                continue
            if fty != m.group("fty"):
                continue
            if int(m.group("ibits")) != ibits:
                continue
            if (ibits, fty) not in _EXACT_ROUND_TRIPS:
                continue
            result_name = m.group("res")
            if _is_immediate_integer(orig_val):
                replacements[result_name] = orig_val
                continue
            promoted_bits = _promoted_bits(ibits)
            ext_op = "sext" if itofp_op == "sitofp" else "zext"
            wide_name = _fresh_name(fn, f"{result_name}.wide")
            trunc_name = _fresh_name(fn, f"{result_name}.trunc")
            inserted_by_result[result_name] = [
                Instruction.from_text(
                    f"  %{wide_name} = {ext_op} i{ibits} {orig_val} to i{promoted_bits}\n"
                ),
                Instruction.from_text(
                    f"  %{trunc_name} = trunc i{promoted_bits} %{wide_name} to i{ibits}\n"
                ),
            ]
            replacements[result_name] = f"%{trunc_name}"

    if not replacements:
        return False

    # 3. Replace matching fptoi instructions with integer ext/trunc
    #    chains (or drop them for constant-fold cases).
    changed = False
    for block in fn.blocks:
        new_insts: list[Instruction] = []
        for inst in block.instructions:
            if inst.result_name and inst.result_name in replacements:
                if inst.result_name in inserted_by_result:
                    new_insts.extend(inserted_by_result[inst.result_name])
                changed = True
                continue
            new_insts.append(inst)
        block.instructions = new_insts

    # 4. Rewrite operand references to the dropped cast results.
    for old, new in replacements.items():
        pattern = re.compile(r"%" + re.escape(old) + r"(?![\w\.])")
        for block in fn.blocks:
            for inst in block.instructions:
                replaced = pattern.sub(new, inst.text)
                if replaced != inst.text:
                    inst.text = replaced

    # 5. Remove now-unused ``sitofp``/``uitofp`` sources.
    still_referenced = _referenced_ssa_names(fn)
    for block in fn.blocks:
        new_insts = []
        for inst in block.instructions:
            if (
                inst.result_name in itofp_info
                and inst.result_name not in still_referenced
            ):
                changed = True
                continue
            new_insts.append(inst)
        block.instructions = new_insts

    return changed


def _promoted_bits(ibits: int) -> int:
    if ibits <= 16:
        return 32
    return 64


def _is_immediate_integer(token: str) -> bool:
    tok = token.strip()
    if tok.startswith("%") or tok.startswith("@"):
        return False
    try:
        int(tok, 0)
        return True
    except ValueError:
        return False


def _fresh_name(fn: Function, base: str) -> str:
    names = fn.defined_names()
    if base not in names:
        return base
    idx = 1
    while f"{base}.{idx}" in names:
        idx += 1
    return f"{base}.{idx}"


def _referenced_ssa_names(fn: Function) -> set[str]:
    out: set[str] = set()
    for block in fn.blocks:
        for inst in block.instructions:
            for m in re.finditer(r"%([\w\.]+)", inst.text):
                out.add(m.group(1))
            # Remove this instruction's own result from the set —
            # otherwise a dead single-result-only instruction would
            # count itself as used.
            if inst.result_name:
                out.discard(inst.result_name)
                # But we only discarded too eagerly if the regex above
                # already added it. Put uses from OTHER instructions
                # back on the next pass — easiest fix: rebuild below.
                pass
    # Rebuild correctly: an SSA name is "referenced" iff some instruction
    # other than its defining instruction mentions it.
    out = set()
    for block in fn.blocks:
        for inst in block.instructions:
            for m in re.finditer(r"%([\w\.]+)", inst.text):
                name = m.group(1)
                if name == inst.result_name:
                    continue
                out.add(name)
    return out


def _ssa_use_counts(fn: Function) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in fn.blocks:
        for inst in block.instructions:
            for m in re.finditer(r"%([\w\.]+)", inst.text):
                name = m.group(1)
                if name == inst.result_name:
                    continue
                counts[name] = counts.get(name, 0) + 1
    return counts
