"""Global Optimizer — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/GlobalOpt.cpp``
  implements :cpp:class:`llvm::GlobalOptPass`. It covers many
  transforms:

  - promote internal globals that fit in a register to SSA locals,
  - shrink-wrap globals into local functions when only one function
    uses them,
  - fold stores into initializers when the value never changes,
  - replace loads of constant-initialized globals with the literal
    constant,
  - deduplicate `@llvm.global_ctors` entries.

Subset implemented here (labelled ``subset``):

- For every internal/private scalar global whose value is known for
  every direct load, replace those loads with the known value:

  - globals declared ``constant``,
  - mutable internal globals that are never stored in the module,
  - globals with one direct store whose block dominates every direct
    load of that same global in the same function.

- When the rewritten module has no remaining references to such a
  global, drop the now-dead internal global definition too.
- Add narrow address-significance attributes that upstream
  ``globalopt`` also materializes in easy cases:
  - ``unnamed_addr`` on internal/private scalar constants whose uses
    are only direct loads,
  - ``local_unnamed_addr`` on defined functions whose address is never
    referenced in the module body.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import compute_dominator_tree
from .ir_mutator import Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class GlobalOptPass(ModulePass):
    name = "pcc-globalopt"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = globalopt_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_GLOBAL_SCALAR_RE = re.compile(
    r"^\s*@(?P<name>[\w\.]+)\s*=\s*"
    r"(?P<linkage>private\s+|internal\s+|)"
    r"(?P<addr>(?:local_)?unnamed_addr\s+|)"
    r"(?P<kind>constant|global)\s+(?P<ty>i\d+|ptr)\s+"
    r"(?P<init>[^,\n]+?)\s*(?:,\s*align\s+\d+)?\s*$"
)
_LOAD_GLOBAL_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*load\s+"
    r"(?P<ty>i\d+|ptr)\s*,\s*ptr\s+@(?P<name>[\w\.]+)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_LOAD_PTR_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*load\s+"
    r"(?P<ty>i\d+|ptr)\s*,\s*ptr\s+(?P<ptr>@[\w\.]+|%[\w\.]+)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_STORE_GLOBAL_RE = re.compile(
    r"^(?P<indent>\s*)store(?P<volatile>\s+volatile)?\s+"
    r"(?P<ty>i\d+|ptr)\s+(?P<val>[^,]+?)\s*,\s*ptr\s+@(?P<name>[\w\.]+)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_STORE_PTR_RE = re.compile(
    r"^(?P<indent>\s*)store(?P<volatile>\s+volatile)?\s+"
    r"(?P<ty>i\d+|ptr)\s+(?P<val>[^,]+?)\s*,\s*ptr\s+(?P<ptr>@[\w\.]+|%[\w\.]+)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_ZERO_GEP_GLOBAL_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*getelementptr(?:\s+inbounds)?\s+[^,]+,\s+ptr\s+@(?P<name>[\w\.]+)\s*,\s*i\d+\s+0\s*$"
)
_GLOBAL_REF_RE = re.compile(r"@([\w\.]+)")
_DIRECT_CALL_RE = re.compile(
    r"\b(?:(?:tail|musttail|notail)\s+)?call\b.*?@(?P<name>[\w\.]+)\("
)
_IMM_RE = re.compile(r"^(?:-?\d+|true|false|null|@[\w\.]+)$")


def globalopt_text(ir_text: str) -> tuple[str, bool]:
    """Inline direct loads of scalar internal globals when value-known."""
    module = llvm.parse_assembly(ir_text)
    module.verify()

    globals_info: dict[str, dict[str, str]] = {}
    for line in ir_text.splitlines():
        m = _GLOBAL_SCALAR_RE.match(line.rstrip("\n"))
        if not m:
            continue
        linkage = (m.group("linkage") or "").strip()
        globals_info[m.group("name")] = {
            "linkage": linkage,
            "kind": m.group("kind"),
            "ty": m.group("ty"),
            "init": m.group("init").strip(),
        }

    refs_by_global = {
        name: {"loads": [], "stores": [], "other": []}
        for name in globals_info
    }
    defined_fn_names = {fn.name for fn in module.functions if not fn.is_declaration}
    fn_direct_call_refs: dict[str, int] = {}
    fn_addr_refs: dict[str, int] = {}
    dom_by_fn: dict[str, object] = {}
    for fn in module.functions:
        if fn.is_declaration:
            continue
        fn_direct_call_refs[fn.name] = 0
        fn_addr_refs[fn.name] = 0
        dom_by_fn[fn.name] = compute_dominator_tree(fn)
        records: list[tuple[str, int, str]] = []
        alias_defs: dict[str, dict[str, str | int]] = {}
        for block in fn.blocks:
            block_name = block.name or "entry"
            for inst_index, inst in enumerate(block.instructions):
                text = str(inst).strip()
                records.append((block_name, inst_index, text))
                m = _ZERO_GEP_GLOBAL_ALIAS_RE.match(text)
                if m and m.group("name") in globals_info:
                    alias_defs[m.group("dst")] = {
                        "name": m.group("name"),
                        "fn": fn.name,
                        "block": block_name,
                        "inst_index": inst_index,
                    }

        alias_mem_uses: dict[str, list[tuple[str, str, int]]] = {
            alias: [] for alias in alias_defs
        }
        alias_other_uses: dict[str, int] = {alias: 0 for alias in alias_defs}
        for block_name, inst_index, text in records:
            load_m = _LOAD_PTR_RE.match(text)
            store_m = _STORE_PTR_RE.match(text)
            ptr_operand = None
            if load_m is not None:
                ptr_operand = load_m.group("ptr")
            elif store_m is not None:
                ptr_operand = store_m.group("ptr")
            for alias in _rhs_ssa_names(text):
                if alias not in alias_defs:
                    continue
                if ptr_operand == f"%{alias}":
                    alias_mem_uses[alias].append((fn.name, block_name, inst_index))
                else:
                    alias_other_uses[alias] += 1

        for block_name, inst_index, text in records:
                handled: set[str] = set()
                direct_callees = {m.group("name") for m in _DIRECT_CALL_RE.finditer(text)}
                alias_def = _ZERO_GEP_GLOBAL_ALIAS_RE.match(text)
                if alias_def and alias_def.group("name") in globals_info:
                    handled.add(alias_def.group("name"))
                m = _LOAD_GLOBAL_RE.match(text)
                if m and m.group("name") in globals_info:
                    refs_by_global[m.group("name")]["loads"].append({
                        "fn": fn.name,
                        "block": block_name,
                        "inst_index": inst_index,
                        "res": m.group("res"),
                        "ty": m.group("ty"),
                    })
                    handled.add(m.group("name"))
                elif (m := _LOAD_PTR_RE.match(text)) is not None:
                    ptr = m.group("ptr")
                    if ptr.startswith("%"):
                        alias = ptr[1:]
                        alias_def_info = alias_defs.get(alias)
                        if alias_def_info is not None and alias_other_uses.get(alias, 0) == 0:
                            global_name = str(alias_def_info["name"])
                            if globals_info[global_name]["kind"] == "global":
                                refs_by_global[global_name]["loads"].append({
                                    "fn": fn.name,
                                    "block": block_name,
                                    "inst_index": inst_index,
                                    "res": m.group("res"),
                                    "ty": m.group("ty"),
                                    "alias": alias,
                                })
                            handled.add(global_name)
                m = _STORE_GLOBAL_RE.match(text)
                if m and m.group("name") in globals_info:
                    refs_by_global[m.group("name")]["stores"].append({
                        "fn": fn.name,
                        "block": block_name,
                        "inst_index": inst_index,
                        "val": m.group("val").strip(),
                        "ty": m.group("ty"),
                        "volatile": bool(m.group("volatile")),
                    })
                    handled.add(m.group("name"))
                elif (m := _STORE_PTR_RE.match(text)) is not None:
                    ptr = m.group("ptr")
                    if ptr.startswith("%"):
                        alias = ptr[1:]
                        alias_def_info = alias_defs.get(alias)
                        if alias_def_info is not None and alias_other_uses.get(alias, 0) == 0:
                            global_name = str(alias_def_info["name"])
                            if globals_info[global_name]["kind"] == "global":
                                refs_by_global[global_name]["stores"].append({
                                    "fn": fn.name,
                                    "block": block_name,
                                    "inst_index": inst_index,
                                    "val": m.group("val").strip(),
                                    "ty": m.group("ty"),
                                    "volatile": bool(m.group("volatile")),
                                    "alias": alias,
                                })
                            handled.add(global_name)
                for name in set(_GLOBAL_REF_RE.findall(text)) - handled:
                    if name in globals_info:
                        refs_by_global[name]["other"].append({
                            "fn": fn.name,
                            "block": block_name,
                            "inst_index": inst_index,
                            "text": text,
                        })
                    if name in defined_fn_names:
                        if name in direct_callees:
                            fn_direct_call_refs[name] = fn_direct_call_refs.get(name, 0) + 1
                        else:
                            fn_addr_refs[name] = fn_addr_refs.get(name, 0) + 1
        for alias, uses in alias_mem_uses.items():
            if alias_other_uses.get(alias, 0) != 0:
                continue
            global_name = str(alias_defs[alias]["name"])
            refs_by_global[global_name].setdefault("alias_defs", []).append({
                "fn": str(alias_defs[alias]["fn"]),
                "block": str(alias_defs[alias]["block"]),
                "inst_index": int(alias_defs[alias]["inst_index"]),
                "uses": uses,
            })

    substitutions_by_fn: dict[str, dict[str, str]] = {}
    stateful_load_sites: dict[tuple[str, str, int], dict[str, str]] = {}
    stateful_store_sites: dict[tuple[str, str, int], dict[str, str]] = {}
    stateful_globals: dict[str, dict[str, str]] = {}
    dead_sites: set[tuple[str, str, int]] = set()
    changed_globals: set[str] = set()
    unnamed_addr_globals: set[str] = set()
    local_unnamed_addr_globals: set[str] = set()

    for name, info in globals_info.items():
        refs = refs_by_global[name]
        loads = refs["loads"]
        stores = refs["stores"]
        alias_defs_for_global = refs.get("alias_defs", [])
        if (
            info["linkage"] in ("private", "internal")
            and not loads
            and not stores
            and not refs["other"]
            and not alias_defs_for_global
        ):
            changed_globals.add(name)
            continue
        if (
            _all_addrsig_benign_uses(name, refs["other"])
            and not any(store["volatile"] for store in stores)
            and (
                (info["kind"] == "constant" and not refs["stores"])
                or loads
                or stores
            )
        ):
            if info["linkage"] in ("private", "internal"):
                unnamed_addr_globals.add(name)
            else:
                local_unnamed_addr_globals.add(name)
        if info["linkage"] not in ("private", "internal"):
            continue
        if refs["other"]:
            continue
        if any(store["volatile"] for store in stores):
            continue

        replacement: str | None = None
        removable = False
        if info["kind"] == "constant" or not stores:
            if not loads:
                continue
            if any(load["ty"] != info["ty"] for load in loads):
                continue
            load_fns = {load["fn"] for load in loads}
            if info["kind"] == "constant" and len(load_fns) != 1:
                continue
            replacement = info["init"]
            removable = True
        elif len(stores) == 1:
            store = stores[0]
            if any("alias" in load for load in loads):
                continue
            if not _IMM_RE.fullmatch(store["val"]):
                continue
            if any(load["ty"] != store["ty"] for load in loads):
                continue
            cross_function_loads = any(ref["fn"] != store["fn"] for ref in loads)
            if cross_function_loads:
                if (
                    info["ty"] != "ptr"
                    and store["ty"] != "ptr"
                    and "alias" not in store
                    and loads
                    and not alias_defs_for_global
                    and info["ty"] == store["ty"]
                    and info["init"] != store["val"]
                    and all("alias" not in load for load in loads)
                ):
                    stateful_globals[name] = {
                        "ty": info["ty"],
                        "init": info["init"],
                        "store_val": store["val"],
                        "linkage": info["linkage"],
                    }
                    stateful_store_sites[(store["fn"], store["block"], store["inst_index"])] = {
                        "name": name,
                    }
                    for load in loads:
                        stateful_load_sites[(load["fn"], load["block"], load["inst_index"])] = {
                            "name": name,
                            "res": load["res"],
                            "ty": load["ty"],
                            "init": info["init"],
                            "store_val": store["val"],
                        }
                continue
            if any(ref["fn"] != store["fn"] for ref in loads):
                continue
            dom = dom_by_fn.get(store["fn"])
            if dom is None:
                continue
            ok = True
            for load in loads:
                if not dom.dominates(store["block"], load["block"]):
                    ok = False
                    break
                if (
                    load["block"] == store["block"]
                    and load["inst_index"] <= store["inst_index"]
                ):
                    ok = False
                    break
            if not ok:
                continue
            replacement = store["val"]
            if "alias" not in store or info["ty"] != "ptr":
                dead_sites.add((store["fn"], store["block"], store["inst_index"]))
            removable = True
        if replacement is None:
            continue
        if info["kind"] == "constant" and any(
            alias_def["uses"] for alias_def in alias_defs_for_global
        ):
            continue
        if (
            any("alias" in load for load in loads)
            and any("alias" not in load for load in loads)
        ):
            continue

        for load in loads:
            substitutions_by_fn.setdefault(load["fn"], {})[load["res"]] = replacement
            dead_sites.add((load["fn"], load["block"], load["inst_index"]))
        if removable:
            changed_globals.add(name)
        for alias_def in alias_defs_for_global:
            uses = alias_def["uses"]
            if uses and all(use in dead_sites for use in uses):
                dead_sites.add((alias_def["fn"], alias_def["block"], alias_def["inst_index"]))

    if not substitutions_by_fn and not dead_sites and not stateful_globals:
        # The pass may still add conservative address-significance attrs.
        pass

    mut = MutableModule.parse(ir_text)
    changed = bool(substitutions_by_fn or dead_sites or stateful_globals)

    def _fresh_temp_name(prefix: str, taken: set[str]) -> str:
        name = prefix
        counter = 0
        while name in taken:
            counter += 1
            name = f"{prefix}.{counter}"
        taken.add(name)
        return name

    for fn in mut.functions:
        fn_subs = substitutions_by_fn.get(fn.name, {})
        fn_taken_names = fn.defined_names()
        for block in fn.blocks:
            kept = []
            for inst_index, inst in enumerate(block.instructions):
                site = (fn.name, block.name, inst_index)
                if site in dead_sites:
                    continue
                stateful_load = stateful_load_sites.get(site)
                stateful_store = stateful_store_sites.get(site)
                if stateful_store is not None:
                    kept.append(Instruction.from_text(f"  store i1 true, ptr @{stateful_store['name']}, align 1\n"))
                    continue
                text = inst.text
                for old, new in fn_subs.items():
                    text = re.sub(
                        r"%" + re.escape(old) + r"(?![\w\.])",
                        new,
                        text,
                    )
                if stateful_load is not None:
                    flag_name = _fresh_temp_name(f"{stateful_load['res']}.b", fn_taken_names)
                    kept.append(Instruction.from_text(f"  %{flag_name} = load i1, ptr @{stateful_load['name']}, align 1\n"))
                    kept.append(
                        Instruction.from_text(
                            f"  %{stateful_load['res']} = select i1 %{flag_name}, "
                            f"{stateful_load['ty']} {stateful_load['store_val']}, "
                            f"{stateful_load['ty']} {stateful_load['init']}\n"
                        )
                    )
                    continue
                inst.text = text
                kept.append(inst)
            block.instructions = kept
        direct_refs = fn_direct_call_refs.get(fn.name, 0)
        addr_refs = fn_addr_refs.get(fn.name, 0)
        if (
            addr_refs == 0
            and direct_refs > 0
            and " unnamed_addr" not in fn.header_line
            and " local_unnamed_addr" not in fn.header_line
        ):
            stripped = fn.header_line.rstrip("\n")
            fn.header_line = re.sub(r"\s*\{\s*$", " unnamed_addr {", stripped) + "\n"
            changed = True
        elif (
            addr_refs == 0
            and direct_refs == 0
            and " local_unnamed_addr" not in fn.header_line
        ):
            stripped = fn.header_line.rstrip("\n")
            fn.header_line = re.sub(r"\s*\{\s*$", " local_unnamed_addr {", stripped) + "\n"
            changed = True

    new_globals: list[str] = []
    for line in mut.globals_:
        m = _GLOBAL_SCALAR_RE.match(line.rstrip("\n"))
        if (
            m
            and m.group("name") in changed_globals
            and not _module_references_global(mut, m.group("name"))
        ):
            changed = True
            continue
        if m and m.group("name") in unnamed_addr_globals and not m.group("addr"):
            line = line.replace(
                f"= {m.group('linkage')}{m.group('kind')}",
                f"= {m.group('linkage')}unnamed_addr {m.group('kind')}",
                1,
            )
            changed = True
        elif m and m.group("name") in local_unnamed_addr_globals and not m.group("addr"):
            line = line.replace(
                f"= {m.group('linkage')}{m.group('kind')}",
                f"= {m.group('linkage')}local_unnamed_addr {m.group('kind')}",
                1,
            )
            changed = True
        if m and m.group("name") in stateful_globals:
            linkage = stateful_globals[m.group("name")]["linkage"]
            linkage_prefix = f"{linkage} " if linkage else ""
            line = f"@{m.group('name')} = {linkage_prefix}unnamed_addr global i1 false\n"
            changed = True
        new_globals.append(line)
    mut.globals_ = new_globals

    if not changed:
        return ir_text, False
    return mut.serialize(), True


def _module_references_global(mut: MutableModule, name: str) -> bool:
    token_re = re.compile(r"@" + re.escape(name) + r"(?![\w\.])")
    for line in mut.header_lines:
        if token_re.search(line):
            return True
    for line in mut.declarations:
        if token_re.search(line):
            return True
    for line in mut.globals_:
        m = _GLOBAL_SCALAR_RE.match(line.rstrip("\n"))
        if m and m.group("name") == name:
            continue
        if token_re.search(line):
            return True
    for fn in mut.functions:
        for block in fn.blocks:
            for inst in block.instructions:
                if token_re.search(inst.text):
                    return True
    for line in mut.tail_lines:
        if token_re.search(line):
            return True
    return False


def _all_addrsig_benign_uses(name: str, refs: list[dict[str, str]]) -> bool:
    return all(_is_addrsig_benign_use(name, ref["text"]) for ref in refs)


def _is_addrsig_benign_use(name: str, text: str) -> bool:
    m = _ZERO_GEP_GLOBAL_ALIAS_RE.match(text)
    return m is not None and m.group("name") == name


def _rhs_ssa_names(text: str) -> set[str]:
    match = re.match(r"^\s*%[\w\.]+\s*=\s*(?P<body>.+?)\s*$", text)
    body = match.group("body") if match is not None else text
    return set(re.findall(r"%([\w\.]+)\b", body))
