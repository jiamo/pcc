"""Promote Memory to Register (mem2reg) — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/Mem2Reg.cpp``
  wraps :cpp:class:`llvm::PromoteMemToReg` (in
  ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/PromoteMemoryToRegister.cpp``).
  The upstream algorithm:

  1. For each alloca, confirm it's only used by ``load`` / ``store``
     instructions (no GEP, ptrtoint, call, etc.).
  2. Compute the iterated dominance frontier of every block that
     contains a store, insert phi nodes at those blocks.
  3. Rename: walk the dominator tree, maintaining a stack of current
     values per alloca; replace loads with the current value, update
     the stack at stores, and fill phi operands on the way.

This module implements a subset:

- Only allocas that are (a) loaded/stored solely, AND (b) dominated
  by a single store whose value is live for every load are
  promoted. In practice this covers:

    - entry-block allocas used in straight-line code,
    - entry-block allocas with a single initialization store before
      all loads.

  For allocas with multiple stores that need phi insertion, the pass
  used to bail out entirely. We now also cover the common branch-join
  shape where one store reaches each predecessor of a single load
  block; in that case we synthesize one phi in the load block and
  rewrite the loads to use it directly.

The promoted alloca, its stores, and its loads are deleted; loads
are replaced with the stored value directly.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG, compute_dominator_tree
from .ir_mutator import Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class Mem2RegPass(ModulePass):
    name = "pcc-mem2reg"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = mem2reg_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_ALLOCA_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*alloca\s+(?P<ty>[\w\*\[\] ]+?)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_STORE_RE = re.compile(
    r"^\s*store\s+(?P<ty>[\w\*\[\] ]+?)\s+(?P<val>[^,]+?)\s*,\s*"
    r"(?:ptr|[\w\*]+\*)\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)
_LOAD_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*load\s+(?P<ty>[\w\*\[\] ]+?)\s*,\s*"
    r"(?:ptr|[\w\*]+\*)\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


def mem2reg_module(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()

    any_changed = False
    for fn in module.functions:
        if fn.is_declaration:
            continue
        new_text, changed = _promote_fn(ir_text, fn)
        if changed:
            ir_text = new_text
            any_changed = True
    return ir_text, any_changed


def _collect_alloca_info(fn: llvm.ValueRef) -> dict[str, dict]:
    """For each promotable alloca, return usage info."""
    info: dict[str, dict] = {}
    # Pass 1: find allocas.
    for block in fn.blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            m = _ALLOCA_RE.match(text)
            if m:
                info[m.group("name")] = {
                    "ty": m.group("ty").strip(),
                    "stores": [],  # (block, inst_index, val)
                    "loads": [],   # (block, inst_index, result_name)
                    "safe": True,
                    "alloca_text": text,
                }

    # Pass 2: scan uses. Any use other than load/store → unsafe.
    # Also collect stores and loads by block.
    for block in fn.blocks:
        block_name = block.name or ""
        for inst_index, inst in enumerate(block.instructions):
            text = str(inst).strip()
            m_store = _STORE_RE.match(text)
            m_load = _LOAD_RE.match(text)
            if m_store and m_store.group("ptr") in info:
                info[m_store.group("ptr")]["stores"].append(
                    (block_name, inst_index, m_store.group("val").strip())
                )
                continue
            if m_load and m_load.group("ptr") in info:
                info[m_load.group("ptr")]["loads"].append(
                    (block_name, inst_index, m_load.group("res"))
                )
                continue
            # Any other reference to an alloca pointer makes it unsafe.
            for alloca_name in list(info):
                # Skip the alloca-definition instruction itself.
                if _ALLOCA_RE.match(text) and re.match(
                    rf"^\s*%{re.escape(alloca_name)}\s*=\s*alloca\b", text
                ):
                    continue
                pattern = re.compile(
                    r"%" + re.escape(alloca_name) + r"(?![\w\.])"
                )
                if pattern.search(text):
                    info[alloca_name]["safe"] = False

    return info


def _promote_fn(ir_text: str, fn: llvm.ValueRef) -> tuple[str, bool]:
    alloca_info = _collect_alloca_info(fn)
    if not alloca_info:
        return ir_text, False

    dom = compute_dominator_tree(fn)
    cfg = CFG.of_function(fn)

    # Subset: promote only allocas where exactly one store dominates
    # every load. That covers the common entry-block init pattern.
    promote_plan: dict[str, str] = {}  # alloca_name → value to replace loads with
    phi_plan: dict[str, dict[str, object]] = {}
    linear_plan: dict[str, dict[str, str]] = {}
    taken_names = _defined_names(fn)

    for name, data in alloca_info.items():
        if not data["safe"]:
            continue
        touched_blocks = {
            block for block, _, _ in data["stores"]
        } | {
            block for block, _, _ in data["loads"]
        }
        if data["loads"] and len(touched_blocks) == 1:
            only_block = next(iter(touched_blocks))
            if only_block in cfg.predecessors.get(only_block, ()):
                pass
            else:
                current_val = "undef"
                substitutions: dict[str, str] = {}
                events = sorted(
                    [(idx, "store", val) for _, idx, val in data["stores"]]
                    + [(idx, "load", res) for _, idx, res in data["loads"]],
                    key=lambda item: (item[0], 0 if item[1] == "store" else 1),
                )
                for _, kind, payload in events:
                    if kind == "store":
                        current_val = payload
                    else:
                        substitutions[payload] = current_val
                linear_plan[name] = substitutions
                continue
        if data["stores"]:
            store_blocks = {block for block, _, _ in data["stores"]}
            if len(store_blocks) == 1:
                store_block = next(iter(store_blocks))
                if store_block in cfg.predecessors.get(store_block, ()):
                    pass
                else:
                    store_events = sorted(
                        [(idx, val) for block, idx, val in data["stores"] if block == store_block],
                        key=lambda item: item[0],
                    )
                    substitutions: dict[str, str] = {}
                    current_val = "undef"
                    cursor = 0
                    same_block_loads = sorted(
                        [(idx, res) for block, idx, res in data["loads"] if block == store_block],
                        key=lambda item: item[0],
                    )
                    for load_idx, load_res in same_block_loads:
                        while cursor < len(store_events) and store_events[cursor][0] < load_idx:
                            current_val = store_events[cursor][1]
                            cursor += 1
                        substitutions[load_res] = current_val
                    while cursor < len(store_events):
                        current_val = store_events[cursor][1]
                        cursor += 1
                    if all(
                        load_block == store_block or dom.dominates(store_block, load_block)
                        for load_block, _, _ in data["loads"]
                    ):
                        for load_block, _, load_res in data["loads"]:
                            if load_block != store_block:
                                substitutions[load_res] = current_val
                        linear_plan[name] = substitutions
                        continue
        if not data["stores"] and data["loads"]:
            promote_plan[name] = "undef"
            continue
        if len(data["stores"]) == 1:
            store_block, store_idx, store_val = data["stores"][0]
            # Check store_block dominates every load block.
            if all(
                dom.dominates(store_block, load_block)
                and not (load_block == store_block and load_idx <= store_idx)
                for load_block, load_idx, _ in data["loads"]
            ):
                promote_plan[name] = store_val
                continue
            if all(
                load_block == store_block and load_idx < store_idx
                for load_block, load_idx, _ in data["loads"]
            ) and store_block not in cfg.predecessors.get(store_block, ()):
                promote_plan[name] = "undef"
                continue
        phi_candidate = _ssa_plan_for_alloca(
            name,
            data,
            cfg,
            dom,
            taken_names,
        )
        if phi_candidate is not None:
            phi_plan[name] = phi_candidate

    if not promote_plan and not phi_plan and not linear_plan:
        return ir_text, False

    # Also collect load results to substitute with the stored value.
    load_substitutions: dict[str, str] = {}  # load_result → stored_val
    for alloca_name, stored_val in promote_plan.items():
        for _, _, load_res in alloca_info[alloca_name]["loads"]:
            load_substitutions[load_res] = stored_val
    for plan in phi_plan.values():
        load_substitutions.update(plan["load_substitutions"])
    for substitutions in linear_plan.values():
        load_substitutions.update(substitutions)

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn.name)
    if fn_mut is None:
        return ir_text, False

    promoted_allocas = set(promote_plan) | set(phi_plan) | set(linear_plan)
    for block in fn_mut.blocks:
        kept: list[Instruction] = []
        for inst in block.instructions:
            stripped = inst.text.strip()
            m = _ALLOCA_RE.match(stripped)
            if m and m.group("name") in promoted_allocas:
                continue
            m = _STORE_RE.match(stripped)
            if m and m.group("ptr") in promoted_allocas:
                continue
            m = _LOAD_RE.match(stripped)
            if m and m.group("ptr") in promoted_allocas:
                continue
            kept.append(inst)
        block.instructions = kept

    phi_insert_positions: dict[str, int] = {}
    phi_entries: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for alloca_name, plan in phi_plan.items():
        for phi in plan["phis"]:
            phi_entries.setdefault(phi["block"], []).append((alloca_name, phi))

    for block_name, entries in phi_entries.items():
        entries.sort(key=lambda item: item[1]["phi_name"])
        entries.sort(key=lambda item: item[0], reverse=True)
        for alloca_name, phi in entries:
            load_block = fn_mut.block(phi["block"])
            if load_block is None:
                return ir_text, False
            insert_at = phi_insert_positions.get(block_name, 0)
            incoming_text = ", ".join(
                f"[ {phi['incoming'][pred]}, %{pred} ]"
                for pred in phi["preds"]
            )
            phi_text = (
                f"  %{phi['phi_name']} = phi {alloca_info[alloca_name]['ty']} "
                f"{incoming_text}\n"
            )
            load_block.instructions.insert(insert_at, Instruction.from_text(phi_text))
            phi_insert_positions[block_name] = insert_at + 1

    text = mut.serialize()
    # Substitute each load's result with the stored value at use sites.
    for load_res, stored_val in load_substitutions.items():
        text = re.sub(
            r"%" + re.escape(load_res) + r"\b", stored_val, text
        )

    return text, True


def _defined_names(fn: llvm.ValueRef) -> set[str]:
    names: set[str] = set()
    for arg in fn.arguments:
        if arg.name:
            names.add(arg.name)
    for block in fn.blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            m = re.match(r"^%([\w\.]+)\s*=", text)
            if m:
                names.add(m.group(1))
    return names


def _fresh_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    i = 1
    while True:
        candidate = f"{base}.{i}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        i += 1


def _remap_value_names(text: str, name_map: dict[str, str]) -> str:
    if not text.startswith("%"):
        return text
    old = text[1:]
    new = name_map.get(old)
    if new is None:
        return text
    return f"%{new}"


def _resolve_phi_value(text: str, replacements: dict[str, str]) -> str:
    current = text
    seen: set[str] = set()
    while current.startswith("%"):
        name = current[1:]
        if name in seen:
            break
        seen.add(name)
        new = replacements.get(name)
        if new is None:
            break
        current = new
    return current


def _ssa_plan_for_alloca(
    name: str,
    data: dict,
    cfg: CFG,
    dom,
    taken_names: set[str],
) -> dict[str, object] | None:
    if not data["loads"]:
        return None

    stores_by_block: dict[str, list[tuple[int, str]]] = {}
    for block, idx, val in data["stores"]:
        stores_by_block.setdefault(block, []).append((idx, val))
    for stores in stores_by_block.values():
        stores.sort(key=lambda item: item[0])

    loads_by_block: dict[str, list[tuple[int, str]]] = {}
    for block, idx, res in data["loads"]:
        loads_by_block.setdefault(block, []).append((idx, res))
    for loads in loads_by_block.values():
        loads.sort(key=lambda item: item[0])

    phi_nodes: list[dict[str, object]] = []
    entry_cache: dict[str, tuple[str, str] | None] = {}
    exit_cache: dict[str, tuple[str, str] | None] = {}
    phi_by_block: dict[str, dict[str, object]] = {}
    visiting_entry: set[str] = set()
    visiting_exit: set[str] = set()
    phi_counter = [0]
    pred_order_by_block = {
        block: {pred: idx for idx, pred in enumerate(cfg.predecessors.get(block, ()))}
        for block in cfg.blocks
    }

    def ensure_phi(block: str) -> dict[str, object]:
        phi = phi_by_block.get(block)
        if phi is not None:
            return phi
        phi_name = _fresh_name(f"{name}.{phi_counter[0]}", taken_names)
        phi_counter[0] += 1
        phi = {
            "block": block,
            "preds": tuple(cfg.predecessors.get(block, ())),
            "incoming": {},
            "phi_name": phi_name,
        }
        phi_by_block[block] = phi
        phi_nodes.append(phi)
        return phi

    def last_store_before(block: str, idx: int) -> str | None:
        stores = stores_by_block.get(block, ())
        current: str | None = None
        for store_idx, val in stores:
            if store_idx < idx:
                current = val
            else:
                break
        return current

    def sort_preds(block: str, pred_infos: dict[str, tuple[str, str]]) -> tuple[str, ...]:
        preds = tuple(cfg.predecessors.get(block, ()))
        pred_order = pred_order_by_block.get(block, {})
        local_non_entry_preds = {
            pred
            for pred, (_, kind) in pred_infos.items()
            if kind == "local" and pred != "entry"
        }

        def _branch_arm_priority(pred: str) -> int | None:
            kind = pred_infos[pred][1]
            if kind not in {"local", "inherited"}:
                return None
            for other, (_, other_kind) in pred_infos.items():
                if other == pred or {kind, other_kind} != {"local", "inherited"}:
                    continue
                local_pred = pred if kind == "local" else other
                inherited_pred = pred if kind == "inherited" else other
                if cfg.predecessors.get(local_pred, ()) != (inherited_pred,):
                    continue
                succs = cfg.successors.get(inherited_pred, ())
                if block not in succs or local_pred not in succs:
                    continue
                if succs.index(local_pred) < succs.index(block):
                    return 2 if pred == local_pred else 3
                return 3 if pred == local_pred else 2
            return None

        def _pred_sort_key(pred: str) -> tuple[int, int, int]:
            if pred == block:
                return (6, 0, pred_order.get(pred, 0))
            _, kind = pred_infos[pred]
            if (
                pred == "entry"
                and any(
                    other != pred and pred_infos[other][1] == "inherited"
                    for other in pred_infos
                )
            ):
                return (0, 0, pred_order.get(pred, 0))
            local_non_entry_dominator = any(
                pred != other
                and other in local_non_entry_preds
                and dom.dominates(other, pred)
                for other in local_non_entry_preds
            )
            if local_non_entry_dominator:
                return (1, -len(dom.dominators(pred)), pred_order.get(pred, 0))
            branch_arm_priority = _branch_arm_priority(pred)
            if branch_arm_priority is not None:
                return (branch_arm_priority, 0, pred_order.get(pred, 0))
            if (
                kind == "local"
                and pred != "entry"
                and any(
                    pred != other
                    and pred_infos[other][1] == "inherited"
                    and dom.dominates(other, pred)
                    for other in pred_infos
                )
            ):
                return (3, -len(dom.dominators(pred)), pred_order.get(pred, 0))
            if kind == "undef":
                return (5, 0, pred_order.get(pred, 0))
            return (4, 0, pred_order.get(pred, 0))

        return tuple(sorted(preds, key=_pred_sort_key))

    def block_exit_value(block: str) -> tuple[str, str] | None:
        if block in exit_cache:
            return exit_cache[block]
        if block in visiting_exit:
            preds = tuple(cfg.predecessors.get(block, ()))
            if len(preds) >= 2:
                phi = ensure_phi(block)
                return (f"%{phi['phi_name']}", "inherited")
            return None
        visiting_exit.add(block)
        try:
            stores = stores_by_block.get(block, ())
            if stores:
                result = (stores[-1][1], "local")
            else:
                result = block_entry_value(block)
            exit_cache[block] = result
            return result
        finally:
            visiting_exit.discard(block)

    def block_entry_value(block: str) -> tuple[str, str] | None:
        if block in entry_cache:
            return entry_cache[block]
        if block in visiting_entry:
            preds = tuple(cfg.predecessors.get(block, ()))
            if len(preds) >= 2:
                phi = ensure_phi(block)
                return (f"%{phi['phi_name']}", "inherited")
            return None
        visiting_entry.add(block)
        try:
            preds = tuple(cfg.predecessors.get(block, ()))
            if not preds:
                result = ("undef", "undef")
                entry_cache[block] = result
                return result
            pred_infos: dict[str, tuple[str, str]] = {}
            for pred in preds:
                info = block_exit_value(pred)
                if info is None:
                    return None
                pred_infos[pred] = info
            if len(preds) == 1:
                result = pred_infos[preds[0]]
                entry_cache[block] = result
                return result
            incoming_vals = tuple(pred_infos[pred][0] for pred in preds)
            if incoming_vals and all(val == incoming_vals[0] for val in incoming_vals[1:]):
                kinds = {pred_infos[pred][1] for pred in preds}
                if "local" in kinds:
                    result = (incoming_vals[0], "local")
                elif "undef" in kinds and len(kinds) == 1:
                    result = (incoming_vals[0], "undef")
                else:
                    result = (incoming_vals[0], "inherited")
                entry_cache[block] = result
                return result
            if block in phi_by_block:
                phi_by_block[block]["preds"] = sort_preds(block, pred_infos)
                phi_by_block[block]["incoming"] = {
                    pred: pred_infos[pred][0] for pred in preds
                }
                result = (f"%{phi_by_block[block]['phi_name']}", "inherited")
                entry_cache[block] = result
                return result
            ordered_preds = sort_preds(block, pred_infos)
            phi = ensure_phi(block)
            phi["preds"] = ordered_preds
            phi["incoming"] = {pred: pred_infos[pred][0] for pred in preds}
            result = (f"%{phi['phi_name']}", "inherited")
            entry_cache[block] = result
            return result
        finally:
            visiting_entry.discard(block)

    load_substitutions: dict[str, str] = {}
    for block, loads in loads_by_block.items():
        for idx, res in loads:
            local = last_store_before(block, idx)
            if local is not None:
                load_substitutions[res] = local
                continue
            info = block_entry_value(block)
            if info is None:
                return None
            load_substitutions[res] = info[0]

    phi_replacements: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        kept_phis: list[dict[str, object]] = []
        for phi in phi_nodes:
            phi["incoming"] = {
                pred: _resolve_phi_value(val, phi_replacements)
                for pred, val in phi["incoming"].items()
            }
            self_val = f"%{phi['phi_name']}"
            non_self_vals = {
                val for val in phi["incoming"].values() if val != self_val
            }
            if len(non_self_vals) == 1:
                phi_replacements[phi["phi_name"]] = next(iter(non_self_vals))
                changed = True
                continue
            kept_phis.append(phi)
        phi_nodes = kept_phis

    if phi_replacements:
        for phi in phi_nodes:
            phi["incoming"] = {
                pred: _resolve_phi_value(val, phi_replacements)
                for pred, val in phi["incoming"].items()
            }
        load_substitutions = {
            res: _resolve_phi_value(val, phi_replacements)
            for res, val in load_substitutions.items()
        }

    if phi_nodes:
        block_order = {block: idx for idx, block in enumerate(cfg.blocks)}
        phi_nodes.sort(key=lambda phi: block_order.get(phi["block"], len(block_order)))
        old_phi_names = {phi["phi_name"] for phi in phi_nodes}
        reserved = set(taken_names) - old_phi_names
        name_map: dict[str, str] = {}
        for idx, phi in enumerate(phi_nodes):
            desired = f"{name}.{idx}"
            new_name = desired
            if new_name in reserved:
                new_name = _fresh_name(desired, reserved)
            else:
                reserved.add(new_name)
            name_map[phi["phi_name"]] = new_name
        for phi in phi_nodes:
            phi["phi_name"] = name_map[phi["phi_name"]]
            phi["incoming"] = {
                pred: _remap_value_names(val, name_map)
                for pred, val in phi["incoming"].items()
            }
        load_substitutions = {
            res: _remap_value_names(val, name_map)
            for res, val in load_substitutions.items()
        }

    return {
        "phis": phi_nodes,
        "load_substitutions": load_substitutions,
    }


def _phi_candidate_for_alloca(
    name: str,
    data: dict,
    cfg: CFG,
    dom,
    taken_names: set[str],
) -> dict[str, object] | None:
    if not data["loads"]:
        return None
    load_blocks = {block for block, _, _ in data["loads"]}
    candidate_blocks = [
        block
        for block in cfg.blocks
        if len(cfg.predecessors.get(block, ())) >= 2
        and all(dom.dominates(block, load_block) for load_block in load_blocks)
    ]
    if not candidate_blocks:
        return None
    load_block = min(candidate_blocks, key=lambda block: len(dom.dominators(block)))
    preds = tuple(cfg.predecessors.get(load_block, ()))
    if len(preds) < 2:
        return None
    stores_by_block: dict[str, str] = {}
    for block, _, val in data["stores"]:
        stores_by_block[block] = val

    incoming: dict[str, str] = {}
    incoming_kind: dict[str, str] = {}
    for pred in preds:
        if pred in stores_by_block:
            incoming[pred] = stores_by_block[pred]
            incoming_kind[pred] = "local"
            continue
        dominating_stores = [
            (len(dom.dominators(block)), block, val)
            for block, val in stores_by_block.items()
            if dom.dominates(block, pred)
        ]
        if not dominating_stores:
            incoming[pred] = "undef"
            incoming_kind[pred] = "undef"
            continue
        dominating_stores.sort()
        best_depth = dominating_stores[-1][0]
        best = [entry for entry in dominating_stores if entry[0] == best_depth]
        if len(best) != 1:
            return None
        incoming[pred] = best[0][2]
        incoming_kind[pred] = "inherited"
    pred_order = {pred: idx for idx, pred in enumerate(preds)}

    local_non_entry_preds = {
        pred
        for pred in preds
        if incoming_kind[pred] == "local" and pred != "entry"
    }

    def _pred_sort_key(pred: str) -> tuple[int, int, int]:
        if incoming_kind[pred] == "undef":
            return (2, 0, pred_order[pred])
        local_non_entry_dominator = any(
            pred != other and other in local_non_entry_preds and dom.dominates(other, pred)
            for other in local_non_entry_preds
        )
        if local_non_entry_dominator:
            return (0, -len(dom.dominators(pred)), pred_order[pred])
        return (1, 0, pred_order[pred])

    ordered_preds = tuple(
        sorted(
            preds,
            key=_pred_sort_key,
        )
    )
    return {
        "load_block": load_block,
        "preds": ordered_preds,
        "incoming": incoming,
        "phi_name": _fresh_name(f"{name}.0", taken_names),
    }
