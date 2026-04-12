"""Argument Promotion — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/ArgumentPromotion.cpp``
  implements :cpp:class:`llvm::ArgumentPromotionPass`. When a
  function has an internal ``ptr`` argument that is only loaded (no
  stores, no escape, no GEP-beyond-element-0), the pass rewrites
  the function signature to take the pointed-to value directly, and
  rewrites each call site to load the value and pass it by value.

Subset implemented here (labelled ``subset``, built on
:mod:`pcc.ir_passes.ir_mutator`):

- Internal-linkage function ``@f(ptr %p, ...)``.
- Every use of ``%p`` inside ``@f`` is a ``load TY, ptr %p`` (same
  element type, no offset).
- No stores to ``%p``, no pass-through as argument to another call.
- Each call site passes a concrete ``ptr %actual`` for this slot.

The pass:

1. Rewrites ``@f``'s argument from ``ptr %p`` to ``TY %p``.
2. Drops every ``%x = load TY, ptr %p`` inside ``@f``, substituting
   each ``%x`` use with ``%p``.
3. At every call site, inserts ``%load.p = load TY, ptr %actual``
   before the call and replaces the argument with ``%load.p``.

This matches the common upstream win on helper functions where a
C-language ``const *`` turns into a value parameter.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import (
    Argument,
    Instruction,
    MutableModule,
)
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class ArgPromotionPass(ModulePass):
    name = "pcc-argpromotion"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = argpromotion_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def argpromotion_module(ir_text: str) -> tuple[str, bool]:
    m = MutableModule.parse(ir_text)
    plan = _build_promotion_plan(m)
    if not plan:
        return ir_text, False
    _apply_plan(m, plan)
    try:
        m.verify_roundtrip()
    except RuntimeError:
        return ir_text, False
    return m.serialize(), True


# ---------------------------------------------------------------------------
# Analysis: decide which function/arg positions are promotable.
# ---------------------------------------------------------------------------


_LOAD_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*load\s+(?P<ty>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


def _is_internal(fn_header: str) -> bool:
    return "internal" in fn_header or "private" in fn_header


def _build_promotion_plan(module: MutableModule) -> dict:
    """Return plan = {fn_name: {arg_idx: (ptr_arg_name, elem_ty)}}.

    Only includes positions that pass every safety check.
    """
    plan: dict[str, dict[int, tuple[str, str]]] = {}
    for fn in module.functions:
        if not _is_internal(fn.header_line):
            continue
        for i, arg in enumerate(fn.args):
            if arg.ty.strip() != "ptr":
                continue
            elem_ty = _only_loaded_with_uniform_type(fn, arg.name)
            if elem_ty is None:
                continue
            plan.setdefault(fn.name, {})[i] = (arg.name, elem_ty)

    # For every target fn, ensure every call site passes a straightforward
    # ptr operand (not a function pointer, not a complex expression).
    _drop_unsafe_call_sites(module, plan)
    plan = {fn: args for fn, args in plan.items() if args}
    return plan


def _only_loaded_with_uniform_type(fn, ptr_name: str) -> str | None:
    """If every use of %ptr_name is a load of the same type, return it.

    Returns None when:
    - the pointer is stored to,
    - it escapes (passed to a call, used as anything besides load's
      pointer operand),
    - loads disagree on the loaded type.
    """
    seen_ty: str | None = None
    for block in fn.blocks:
        for inst in block.instructions:
            if f"%{ptr_name}" not in inst.text:
                continue
            # Ignore the def line itself (function arg is never defined
            # by an instruction, so this does nothing inside fn.blocks).
            m = _LOAD_RE.match(inst.text.rstrip("\n"))
            if m and m.group("ptr") == ptr_name:
                ty = m.group("ty").strip()
                if seen_ty is None:
                    seen_ty = ty
                elif seen_ty != ty:
                    return None
                continue
            # Any reference other than the canonical load → unsafe.
            return None
    return seen_ty


_CALL_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:%(?P<res>[\w\.]+)\s*=\s*)?"
    r"(?:tail\s+|musttail\s+|notail\s+)?call\s+"
    r"(?P<rty>[^@]+?)@(?P<callee>[\w\.]+)\s*\((?P<args>[^)]*)\)\s*$"
)


def _drop_unsafe_call_sites(module: MutableModule, plan: dict) -> None:
    """Drop promotion for functions whose call sites pass anything
    besides a direct ``ptr %name`` operand for the targeted slot.
    """
    to_drop: list[tuple[str, int]] = []
    for fn in module.functions:
        for block in fn.blocks:
            for inst in block.instructions:
                m = _CALL_LINE_RE.match(inst.text.rstrip("\n"))
                if not m:
                    continue
                callee = m.group("callee")
                if callee not in plan:
                    continue
                actuals = _parse_call_actuals(m.group("args"))
                for idx in plan[callee]:
                    if idx >= len(actuals):
                        to_drop.append((callee, idx))
                        continue
                    ty, val = actuals[idx]
                    if ty.strip() != "ptr":
                        to_drop.append((callee, idx))
                        continue
                    if not (val.startswith("%") or val.startswith("@")):
                        # Constant / global / expression — could be
                        # promoted later but narrow for now.
                        to_drop.append((callee, idx))
    for fn_name, idx in to_drop:
        plan.get(fn_name, {}).pop(idx, None)


def _parse_call_actuals(args_text: str) -> list[tuple[str, str]]:
    """Split ``ty val, ty val, ...`` into pairs."""
    out: list[tuple[str, str]] = []
    for piece in args_text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        parts = piece.rsplit(None, 1)
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
        else:
            out.append(("", piece))
    return out


# ---------------------------------------------------------------------------
# Apply plan
# ---------------------------------------------------------------------------


def _apply_plan(module: MutableModule, plan: dict) -> None:
    # 1. Per promoted function: change ptr args to value args, remove
    # loads of those pointers, substitute load-result uses with the
    # new value arg.
    for fn_name, arg_map in plan.items():
        fn = module.function(fn_name)
        if fn is None:
            continue
        # Replace arg types.
        for idx, (arg_name, elem_ty) in arg_map.items():
            fn.args[idx] = Argument(ty=elem_ty, name=arg_name)
        # For each promoted pointer, find every `%x = load ty, ptr %p`
        # and record the rename `x -> p`, then drop the load line.
        renames: list[tuple[str, str]] = []
        for idx, (arg_name, elem_ty) in arg_map.items():
            for block in fn.blocks:
                new_insts: list[Instruction] = []
                for inst in block.instructions:
                    m = _LOAD_RE.match(inst.text.rstrip("\n"))
                    if m and m.group("ptr") == arg_name:
                        renames.append((m.group("res"), arg_name))
                        continue
                    new_insts.append(inst)
                block.instructions = new_insts
        # Substitute the load results globally within the function.
        for old, new in renames:
            _substitute_within_function(fn, old, new)

    # 2. Rewrite call sites: insert a load before the call and swap
    # the actual from `ptr %x` to the loaded value.
    counter = 0
    for fn in module.functions:
        for block in fn.blocks:
            new_insts: list[Instruction] = []
            for inst in block.instructions:
                m = _CALL_LINE_RE.match(inst.text.rstrip("\n"))
                if not m or m.group("callee") not in plan:
                    new_insts.append(inst)
                    continue
                callee = m.group("callee")
                actuals = _parse_call_actuals(m.group("args"))
                prefix_insts: list[Instruction] = []
                new_actuals: list[tuple[str, str]] = []
                for idx, (ty, val) in enumerate(actuals):
                    if idx in plan[callee] and (val.startswith("%") or val.startswith("@")):
                        counter += 1
                        elem_ty = plan[callee][idx][1]
                        load_name = f"argprom{counter}"
                        prefix_insts.append(Instruction.from_text(
                            f"{m.group('indent')}%{load_name} = load {elem_ty}, ptr {val}\n"
                        ))
                        new_actuals.append((elem_ty, f"%{load_name}"))
                    else:
                        new_actuals.append((ty, val))
                new_args_text = ", ".join(f"{ty} {val}" for ty, val in new_actuals)
                new_line = re.sub(
                    r"\(([^)]*)\)", f"({new_args_text})",
                    inst.text, count=1,
                )
                new_insts.extend(prefix_insts)
                new_insts.append(Instruction.from_text(new_line))
            block.instructions = new_insts


def _substitute_within_function(fn, old: str, new: str) -> None:
    pat = re.compile(r"%" + re.escape(old) + r"(?![\w\.])")
    for block in fn.blocks:
        for inst in block.instructions:
            new_text = pat.sub(f"%{new}", inst.text)
            if new_text != inst.text:
                inst.text = new_text
                if inst.result_name == old:
                    inst.result_name = new
