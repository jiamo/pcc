"""Scalar Replacement of Aggregates (SROA) — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SROA.cpp``
  implements :cpp:class:`llvm::SROAPass`. For each alloca of an
  aggregate (struct / array) type, the pass decomposes the memory
  access pattern into per-field uses, emits one alloca per field,
  and rewrites every load/store/GEP to refer to the new per-field
  slot. Fully-decomposed allocas then become eligible for mem2reg.

Subset implemented here (labelled ``subset``):

- **Scalar field splitting for small fixed aggregates**: when an
  alloca has aggregate type made entirely of integer scalars and every
  use is a constant-index field/element GEP (plus direct load/store on
  the one-field case), rewrite it into one scalar slot per field and
  remap GEP users to the new slots. After the split, run a local
  ``mem2reg`` pass so straight-line aggregate traffic collapses to SSA.

Larger aggregates, arrays, and partial-load decomposition are
deferred to the full implementation.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dce import dce_module_text
from .mem2reg import mem2reg_module
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_AGG_ALLOCA_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*alloca\s*(?P<agg>.+?)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_TYPEDEF_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*type\s+(?P<body>.+?)\s*$"
)
_GEP_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*getelementptr\s+(?:inbounds\s+)?"
    r"(?P<agg>.+?)\s*,\s*ptr\s+%(?P<ptr>[\w\.]+)\s*,\s*(?P<indices>.+)\s*$"
)
_DIRECT_LOAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*load\s+(?P<ty>.+?)\s*,\s*ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)
_DIRECT_STORE_PREFIX_RE = re.compile(r"^(?P<indent>\s*)store\s+")
_INT_TY_RE = re.compile(r"i\d+")
_NAMED_TY_RE = re.compile(r"%[\w\.]+")
_INDEX_RE = re.compile(r"i\d+\s+(-?\d+)")


class SROAPass(ModulePass):
    name = "pcc-sroa"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = sroa_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def sroa_text(ir_text: str) -> tuple[str, bool]:
    # 1. Find all fixed aggregate allocas we know how to split.
    lines = ir_text.splitlines(keepends=True)
    type_defs = _collect_type_defs(ir_text)
    allocas: dict[str, dict[str, object]] = {}
    taken_names = _defined_names(ir_text)
    for idx, line in enumerate(lines):
        m = _AGG_ALLOCA_RE.match(line.rstrip("\n"))
        if not m:
            continue
        layout = _parse_layout(m.group("agg"), type_defs)
        if layout is None:
            continue
        leaf_paths = list(layout.keys())
        slots = [layout[path] for path in leaf_paths]
        names = []
        if len(slots) == 1:
            names = [m.group("name")]
        else:
            for i in range(len(slots)):
                names.append(_fresh_name(f"{m.group('name')}.{i}", taken_names))
        allocas[m.group("name")] = {
            "line_idx": idx,
            "indent": m.group("indent"),
            "agg": m.group("agg"),
            "slots": slots,
            "leaf_paths": leaf_paths,
            "path_to_slot": {
                path: names[i] for i, path in enumerate(leaf_paths)
            },
            "slot_names": names,
        }

    if not allocas:
        return ir_text, False

    # 2. Scan uses. Safe uses are constant-index GEPs into the known
    # aggregate shape, plus direct load/store on the single-slot case.
    gep_subs: dict[str, str] = {}  # gep_result → replacement slot SSA name
    gep_origins: dict[str, str] = {}  # gep_result → original aggregate alloca
    unsafe: set[str] = set()
    dead_gep_lines: set[int] = set()
    for idx, line in enumerate(lines):
        stripped = line.rstrip("\n")
        safe_gep_for: set[str] = set()
        m = _GEP_RE.match(stripped)
        if m and m.group("ptr") in allocas:
            info = allocas[m.group("ptr")]
            indices = [int(tok) for tok in _INDEX_RE.findall(m.group("indices"))]
            if not indices or indices[0] != 0:
                unsafe.add(m.group("ptr"))
            else:
                path = tuple(indices[1:])
                slot_name = info["path_to_slot"].get(path)
                if slot_name is None:
                    unsafe.add(m.group("ptr"))
                else:
                    gep_subs[m.group("res")] = slot_name
                    gep_origins[m.group("res")] = m.group("ptr")
                    dead_gep_lines.add(idx)
                    safe_gep_for.add(m.group("ptr"))
        # Detect other references to the alloca.
        for name, info in allocas.items():
            if name in unsafe:
                continue
            if name in safe_gep_for:
                continue
            if idx == info["line_idx"]:
                continue
            load_match = _DIRECT_LOAD_RE.match(stripped)
            if load_match and load_match.group("ptr") == name and load_match.group("ty").strip() == info["agg"].strip():
                continue
            store_match = _parse_direct_store(stripped)
            if store_match and store_match["ptr"] == name and store_match["ty"].strip() == info["agg"].strip():
                continue
            if re.search(r"%" + re.escape(name) + r"(?![\w\.])", line):
                unsafe.add(name)

    for idx, line in enumerate(lines):
        stripped = line.rstrip("\n")
        gm = _GEP_RE.match(stripped)
        if gm and gm.group("res") in gep_origins:
            continue
        load_match = _DIRECT_LOAD_RE.match(stripped)
        store_match = _parse_direct_store(stripped)
        for gep_res, origin in gep_origins.items():
            if origin in unsafe:
                continue
            if not re.search(r"%" + re.escape(gep_res) + r"(?![\w\.])", line):
                continue
            if load_match and load_match.group("ptr") == gep_res:
                continue
            if store_match and store_match["ptr"] == gep_res:
                continue
            unsafe.add(origin)
            break

    promotable = {
        name: info for name, info in allocas.items()
        if name not in unsafe
    }
    if not promotable:
        return ir_text, False

    # 3. Rewrite:
    #    - Replace aggregate alloca with one scalar alloca per slot.
    #    - Drop safe GEP lines.
    #    - Substitute GEP result uses with the matching slot SSA name.
    out: list[str] = []
    rewritten_direct_use = False
    direct_load_subs: dict[str, str] = {}
    for idx, line in enumerate(lines):
        m = _AGG_ALLOCA_RE.match(line.rstrip("\n"))
        if m and m.group("name") in promotable:
            info = promotable[m.group("name")]
            for slot_name, slot_ty in zip(info["slot_names"], info["slots"], strict=True):
                out.append(f"{info['indent']}%{slot_name} = alloca {slot_ty}\n")
            continue
        lm = _DIRECT_LOAD_RE.match(line.rstrip("\n"))
        if lm and lm.group("ptr") in promotable:
            info = promotable[lm.group("ptr")]
            if lm.group("ty").strip() == info["agg"].strip():
                if len(info["slots"]) == 1:
                    load_tmp = _fresh_name(f"{lm.group('res')}.fca.0.load", taken_names)
                    agg_tmp = _fresh_name(f"{lm.group('res')}.fca.0.insert", taken_names)
                    path_suffix = ", ".join(str(i) for i in info["leaf_paths"][0])
                    out.append(f"{lm.group('indent')}%{load_tmp} = load {info['slots'][0]}, ptr %{info['slot_names'][0]}\n")
                    out.append(
                        f"{lm.group('indent')}%{agg_tmp} = insertvalue {info['agg'].strip()} poison, {info['slots'][0]} %{load_tmp}, {path_suffix}\n"
                    )
                    direct_load_subs[lm.group("res")] = agg_tmp
                else:
                    current_agg = "poison"
                    for slot_name, slot_ty, path in zip(
                        info["slot_names"],
                        info["slots"],
                        info["leaf_paths"],
                        strict=True,
                    ):
                        path_str = ".".join(str(i) for i in path)
                        load_tmp = _fresh_name(f"{lm.group('res')}.fca.{path_str}.load", taken_names)
                        insert_tmp = _fresh_name(f"{lm.group('res')}.fca.{path_str}.insert", taken_names)
                        path_suffix = ", ".join(str(i) for i in path)
                        out.append(f"{lm.group('indent')}%{load_tmp} = load {slot_ty}, ptr %{slot_name}\n")
                        out.append(
                            f"{lm.group('indent')}%{insert_tmp} = insertvalue {info['agg'].strip()} {current_agg}, {slot_ty} %{load_tmp}, {path_suffix}\n"
                        )
                        current_agg = f"%{insert_tmp}"
                    direct_load_subs[lm.group("res")] = current_agg[1:]
                rewritten_direct_use = True
                continue
        sm = _parse_direct_store(line.rstrip("\n"))
        if sm and sm["ptr"] in promotable:
            info = promotable[sm["ptr"]]
            if sm["ty"].strip() == info["agg"].strip():
                if len(info["slots"]) == 1:
                    extract_base = info["slot_names"][0]
                    if sm["val"].startswith("%"):
                        extract_base = sm["val"][1:]
                    tmp = _fresh_name(f"{extract_base}.fca.0.extract", taken_names)
                    path_suffix = ", ".join(str(i) for i in info["leaf_paths"][0])
                    out.append(
                        f"{sm['indent']}%{tmp} = extractvalue {info['agg'].strip()} {sm['val'].strip()}, {path_suffix}\n"
                    )
                    out.append(
                        f"{sm['indent']}store {info['slots'][0]} %{tmp}, ptr %{info['slot_names'][0]}\n"
                    )
                else:
                    extract_base = sm["val"][1:] if sm["val"].startswith("%") else "agg"
                    for slot_name, slot_ty, path in zip(
                        info["slot_names"],
                        info["slots"],
                        info["leaf_paths"],
                        strict=True,
                    ):
                        path_str = ".".join(str(i) for i in path)
                        tmp = _fresh_name(f"{extract_base}.fca.{path_str}.extract", taken_names)
                        path_suffix = ", ".join(str(i) for i in path)
                        out.append(
                            f"{sm['indent']}%{tmp} = extractvalue {info['agg'].strip()} {sm['val'].strip()}, {path_suffix}\n"
                        )
                        out.append(
                            f"{sm['indent']}store {slot_ty} %{tmp}, ptr %{slot_name}\n"
                        )
                rewritten_direct_use = True
                continue
        if idx in dead_gep_lines:
            gm = _GEP_RE.match(line.rstrip("\n"))
            if gm and gm.group("ptr") in promotable:
                continue
        out.append(line)

    text = "".join(out)
    for gep_res, slot_name in gep_subs.items():
        original_alloca = next(
            (name for name, info in promotable.items() if slot_name in info["slot_names"]),
            None,
        )
        if original_alloca is None:
            continue
        text = re.sub(
            r"%" + re.escape(gep_res) + r"(?![\w\.])",
            f"%{slot_name}",
            text,
        )
    for old, new in direct_load_subs.items():
        text = re.sub(
            r"%" + re.escape(old) + r"(?![\w\.])",
            f"%{new}",
            text,
        )
    if text == ir_text and not rewritten_direct_use:
        return text, False
    text, _mem2reg_changed = mem2reg_module(text)
    text, _ = dce_module_text(text)
    text = _drop_unused_type_defs(text)
    return text, True


def _collect_type_defs(ir_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ir_text.splitlines():
        m = _TYPEDEF_RE.match(line.strip())
        if m:
            out[f"%{m.group('name')}"] = m.group("body").strip()
    return out


def _drop_unused_type_defs(ir_text: str) -> str:
    lines = ir_text.splitlines(keepends=True)
    typedefs: dict[str, int] = {}
    for idx, line in enumerate(lines):
        m = _TYPEDEF_RE.match(line.strip())
        if m:
            typedefs[f"%{m.group('name')}"] = idx
    if not typedefs:
        return ir_text

    keep = [True] * len(lines)
    for name, idx in typedefs.items():
        used = False
        token = re.compile(r"(?<![\w\.])" + re.escape(name) + r"(?![\w\.])")
        for j, line in enumerate(lines):
            if j == idx:
                continue
            if token.search(line):
                used = True
                break
        if not used:
            keep[idx] = False
    return "".join(line for idx, line in enumerate(lines) if keep[idx])


def _parse_layout(
    agg: str,
    type_defs: dict[str, str],
) -> dict[tuple[int, ...], str] | None:
    parsed = _parse_type(agg.strip(), 0, type_defs, set())
    if parsed is None:
        return None
    node, pos = parsed
    if node[0] == "int":
        return None
    tail = agg.strip()[pos:].strip()
    if tail:
        return None
    layout: dict[tuple[int, ...], str] = {}
    _flatten_layout(node, (), layout)
    if not layout:
        return None
    return layout


def _parse_type(
    text: str,
    pos: int,
    type_defs: dict[str, str],
    seen: set[str],
):
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        return None
    m = _NAMED_TY_RE.match(text, pos)
    if m is not None:
        name = m.group(0)
        if name in seen or name not in type_defs:
            return None
        resolved = _parse_type(type_defs[name], 0, type_defs, seen | {name})
        if resolved is None:
            return None
        node, inner_pos = resolved
        if type_defs[name][inner_pos:].strip():
            return None
        return (node, m.end())
    if text.startswith("{", pos):
        elems = []
        pos += 1
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                return None
            if text.startswith("}", pos):
                pos += 1
                break
            parsed = _parse_type(text, pos, type_defs, seen)
            if parsed is None:
                return None
            elem, pos = parsed
            elems.append(elem)
            pos = _skip_ws(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
                continue
            if pos < len(text) and text[pos] == "}":
                pos += 1
                break
            return None
        return (("struct", tuple(elems)), pos)
    if text.startswith("[", pos):
        m = re.match(r"\[(?P<count>\d+)\s+x\s*", text[pos:])
        if m is None:
            return None
        count = int(m.group("count"))
        pos += m.end()
        parsed = _parse_type(text, pos, type_defs, seen)
        if parsed is None:
            return None
        elem, pos = parsed
        pos = _skip_ws(text, pos)
        if pos >= len(text) or text[pos] != "]":
            return None
        return (("array", count, elem), pos + 1)
    m = _INT_TY_RE.match(text, pos)
    if m is None:
        return None
    return (("int", m.group(0)), m.end())


def _flatten_layout(node, path: tuple[int, ...], out: dict[tuple[int, ...], str]) -> None:
    kind = node[0]
    if kind == "int":
        out[path] = node[1]
        return
    if kind == "struct":
        for i, elem in enumerate(node[1]):
            _flatten_layout(elem, path + (i,), out)
        return
    if kind == "array":
        _, count, elem = node
        for i in range(count):
            _flatten_layout(elem, path + (i,), out)


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _defined_names(ir_text: str) -> set[str]:
    return set(re.findall(r"^\s*%([\w\.]+)\s*=", ir_text, re.MULTILINE))


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


def _parse_direct_store(line: str) -> dict[str, str] | None:
    m = _DIRECT_STORE_PREFIX_RE.match(line)
    if m is None:
        return None
    rest = line[m.end():]
    ptr_marker = ", ptr %"
    ptr_pos = rest.rfind(ptr_marker)
    if ptr_pos == -1:
        return None
    typed_val = rest[:ptr_pos].rstrip()
    ptr_tail = rest[ptr_pos + len(ptr_marker):]
    ptr_match = re.match(r"(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$", ptr_tail)
    if ptr_match is None:
        return None
    ty_end = _scan_ir_type(typed_val)
    if ty_end is None:
        return None
    ty = typed_val[:ty_end].strip()
    val = typed_val[ty_end:].strip()
    if not ty or not val:
        return None
    return {
        "indent": m.group("indent"),
        "ty": ty,
        "val": val,
        "ptr": ptr_match.group("ptr"),
    }


def _scan_ir_type(text: str) -> int | None:
    pos = _skip_ws(text, 0)
    if pos >= len(text):
        return None
    m = _NAMED_TY_RE.match(text, pos)
    if m is not None:
        return m.end()
    parsed = _parse_type(text, pos, {}, set())
    if parsed is None:
        return None
    _node, end = parsed
    return end
