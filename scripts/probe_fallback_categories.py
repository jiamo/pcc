#!/usr/bin/env python3
"""Closed-world spike step 1: fallback origin categorization (#217).

Read the tight stage1 closure IR and classify the 27k+ ``py_cpy_*``
calls by:

1. Action vs Plumbing — Plumbing (decref/from_*/ensure_init) is a side
   effect of Actions (import/getattr/call/setitem/iter/...). Counting
   only Actions gives the real number of dynamic-Python idioms.
2. SSA name prefix — codegen embeds source-level intent in SSA names
   (cpy.import.X / cpy.get.X / cpy.callkw.X / cpy.fn.X). Grouping by
   prefix surfaces the top idiom patterns.
3. Target function/attr name — pulled from the SSA suffix, so we can
   tell whether `compile_python` or `runtime["py_cpy_call1"]` or
   `print` dominates.

Output: docs/plans/stage1-closure-fallback-categories.md.

This is a read-only probe per codex's recommended step 1: do not
change codegen, do not change layer1, just measure where the dynamic
Python idioms come from so we know whether closed-world specialization
has a viable hot-list before committing to a 2-week spike.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter


_ACTIONS = {
    # Module import.
    "py_cpy_import": "import",
    # Dynamic attribute access.
    "py_cpy_getattr": "getattr",
    "py_cpy_setattr": "setattr",
    # Calls.
    "py_cpy_call_noargs": "call",
    "py_cpy_call1": "call",
    "py_cpy_call2": "call",
    "py_cpy_call3": "call",
    "py_cpy_call_kw": "call",
    "py_cpy_call_argv": "call",
    "py_cpy_call_list": "call",
    "py_cpy_call_kwdict": "call",
    "py_cpy_call_kwdict_plus": "call",
    "py_cpy_call_list_kwdict": "call",
    # Subscript.
    "py_cpy_setitem": "subscript",
    "py_cpy_getitem": "subscript",
    # Iteration.
    "py_cpy_iter": "iter",
    "py_cpy_iter_next": "iter",
    # Builtins.
    "py_cpy_len": "len",
    "py_cpy_truthy": "truthy",
    # Wrap pcc fn into PyCFunction.
    "py_cpy_wrap_pcc_0arg": "wrap",
    "py_cpy_wrap_pcc_1arg": "wrap",
    "py_cpy_wrap_pcc_2arg": "wrap",
    "py_cpy_wrap_pcc_3arg": "wrap",
    "py_cpy_wrap_pcc_4arg": "wrap",
    "py_cpy_wrap_pcc_5arg": "wrap",
    "py_cpy_wrap_pcc_6arg": "wrap",
    "py_cpy_wrap_pcc_7arg": "wrap",
    "py_cpy_wrap_pcc_8arg": "wrap",
    "py_cpy_wrap_pcc_9arg": "wrap",
    # Main exit.
    "py_cpy_main_exitcode": "main_exit",
}
_PLUMBING = {
    "py_cpy_from_pcc_obj": "from_pcc_obj",
    "py_cpy_from_i64": "from_i64",
    "py_cpy_from_f64": "from_f64",
    "py_cpy_from_pccstr": "from_pccstr",
    "py_cpy_to_pcc_str": "to_pcc_str",
    "py_cpy_to_pcc_obj": "to_pcc_obj",
    "py_cpy_to_i64": "to_i64",
    "py_cpy_to_f64": "to_f64",
    "py_cpy_decref": "decref",
    "py_cpy_incref": "incref",
    "py_cpy_ensure_init": "ensure_init",
}
_BRIDGE_SYMBOLS = {
    "py_cpy_to_pcc_obj",
    "py_cpy_to_pcc_str",
}


_CALL_RE = re.compile(
    r"^\s*"
    r"(?:%(?P<ssa>[a-zA-Z0-9_.\-]+)\s*=\s*)?"
    r"(?:tail\s+)?call\s+[^\n]*?@(?P<sym>py_cpy_[a-z0-9_]+)\s*\("
)


def _read_ir(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _ssa_prefix(name: str | None, depth: int = 2) -> str:
    if not name:
        return "(void)"
    parts = name.split(".")
    return ".".join(parts[:depth]) if len(parts) >= depth else name


def _ssa_target(name: str | None) -> str:
    """Pull the human-readable last segment off an SSA name like
    `cpy.fn.compile_python.851` → `compile_python`."""
    if not name:
        return ""
    parts = name.split(".")
    if len(parts) <= 2:
        return ""
    # Drop trailing numeric serial(s).
    while parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) <= 2:
        return ""
    return ".".join(parts[2:])


def _scan(ir_text: str) -> dict:
    by_sym = Counter()
    by_action = Counter()
    by_plumbing = Counter()
    by_ssa_prefix = Counter()  # prefix only on Action calls
    by_target = Counter()      # target name on Action calls
    action_target_pairs = Counter()  # (action_kind, target) — the most useful
    total = 0

    for line in ir_text.splitlines():
        m = _CALL_RE.match(line)
        if not m:
            continue
        sym = m.group("sym")
        ssa = m.group("ssa")
        by_sym[sym] += 1
        total += 1
        if sym in _ACTIONS:
            kind = _ACTIONS[sym]
            by_action[kind] += 1
            prefix = _ssa_prefix(ssa, depth=2)
            by_ssa_prefix[prefix] += 1
            target = _ssa_target(ssa)
            if target:
                by_target[target] += 1
                action_target_pairs[(kind, target)] += 1
        elif sym in _PLUMBING:
            by_plumbing[_PLUMBING[sym]] += 1

    actions_total = sum(by_action.values())
    plumbing_total = sum(by_plumbing.values())
    bridge_total = sum(by_sym[sym] for sym in _BRIDGE_SYMBOLS)
    return {
        "total": total,
        "actions_total": actions_total,
        "plumbing_total": plumbing_total,
        "bridge_total": bridge_total,
        "non_bridge_total": total - bridge_total,
        "by_sym": by_sym,
        "by_action": by_action,
        "by_plumbing": by_plumbing,
        "by_ssa_prefix": by_ssa_prefix,
        "by_target": by_target,
        "action_target_pairs": action_target_pairs,
    }


def _table(rows: list[tuple], headers: list[str]) -> list[str]:
    out = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---:" if h.endswith("count") or h.endswith("%") else "---" for h in headers]) + "|")
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def _format_pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


def _write_report(stats: dict, out_path: str, ir_path: str) -> None:
    rep = []
    rep.append("# Stage1 Closure Fallback Categorization\n")
    rep.append(f"Generated by `scripts/probe_fallback_categories.py` (#217)")
    rep.append(f"from `{os.path.relpath(ir_path)}`.\n")

    total = stats["total"]
    actions = stats["actions_total"]
    plumbing = stats["plumbing_total"]
    rep.append("## Summary\n")
    rep.append(f"- Total `py_cpy_*` calls: **{total}**")
    rep.append(f"- Actions (root cause idioms): **{actions}** ({_format_pct(actions, total)})")
    rep.append(f"- Plumbing (decref/from_*/etc., side effect of actions): **{plumbing}** ({_format_pct(plumbing, total)})")
    rep.append(f"- Native bridge calls (`py_cpy_to_pcc_*`): **{stats['bridge_total']}** ({_format_pct(stats['bridge_total'], total)})")
    rep.append(f"- Non-bridge `py_cpy_*` calls: **{stats['non_bridge_total']}** ({_format_pct(stats['non_bridge_total'], total)})")
    rep.append("")
    rep.append(
        "Action / Plumbing ratio gives the real shape: every action "
        "(import / getattr / call / setitem / ...) brings a tail of "
        "plumbing fallbacks (from_pccstr, decref, etc.) along with it. "
        "**Action count is the number of dynamic-Python idioms to "
        "specialize away.**"
    )
    rep.append("")

    rep.append("## Action category distribution\n")
    rows = []
    for kind, n in stats["by_action"].most_common():
        rows.append((kind, n, _format_pct(n, actions)))
    rep.extend(_table(rows, ["action", "count", "% actions"]))
    rep.append("")

    rep.append("## Top 25 SSA prefixes (action calls only)\n")
    rep.append(
        "SSA prefix groups action call sites by codegen-assigned "
        "intent. `cpy.import.X` = `import X`; `cpy.get.X` = first "
        "`getattr` after import; `cpy.fn.X` = function reference "
        "extracted from a module; `cpy.callkw.X` = keyword call; "
        "`cpy.builtins` / `cpy.builtin.X` = `print` / `len` / etc.\n"
    )
    top_prefix = stats["by_ssa_prefix"].most_common(25)
    rows = [(p, n, _format_pct(n, actions)) for p, n in top_prefix]
    rep.extend(_table(rows, ["ssa prefix", "count", "% actions"]))
    rep.append("")

    rep.append("## Top 30 (action, target name) pairs\n")
    rep.append(
        "If specialization is going to work, the top pairs need to "
        "be **concentrated** (e.g. top 5 covers 50%+). If they are "
        "scattered (long tail), closed-world specialization will not "
        "pay back the 2-week spike — codex's go/no-go gate applies.\n"
    )
    top_pairs = stats["action_target_pairs"].most_common(30)
    rows = [(f"`{kind}` → `{target}`", n, _format_pct(n, actions))
            for (kind, target), n in top_pairs]
    rep.extend(_table(rows, ["(action, target)", "count", "% actions"]))
    rep.append("")

    rep.append("## Plumbing breakdown\n")
    rows = [(k, n, _format_pct(n, plumbing))
            for k, n in stats["by_plumbing"].most_common()]
    rep.extend(_table(rows, ["plumbing kind", "count", "% plumbing"]))
    rep.append("")

    # Decision aid: top 5 / top 10 concentration.
    cum = 0
    top5_pct = top10_pct = top20_pct = 0.0
    for i, (_, n) in enumerate(stats["action_target_pairs"].most_common()):
        cum += n
        if i + 1 == 5:
            top5_pct = 100.0 * cum / actions if actions else 0.0
        if i + 1 == 10:
            top10_pct = 100.0 * cum / actions if actions else 0.0
        if i + 1 == 20:
            top20_pct = 100.0 * cum / actions if actions else 0.0
    rep.append("## Decision aid\n")
    rep.append(f"- Top 5 (action, target) pairs cover: **{top5_pct:.1f}%** of actions")
    rep.append(f"- Top 10: **{top10_pct:.1f}%**")
    rep.append(f"- Top 20: **{top20_pct:.1f}%**")
    rep.append("")
    rep.append(
        "**codex's go/no-go gate** (paraphrased): top concentration "
        "is the signal. If top 5 covers 50%+ of actions, two desugar "
        "transforms can plausibly clear most of the dynamic-Python "
        "idioms — closed-world spike has a viable target list. If top "
        "20 still covers < 40%, the long tail is the real cost — stop "
        "and accept the hybrid bootstrap as a design choice."
    )
    rep.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rep))


def main() -> int:
    ir_path = "/tmp/stage1_closure_probe_tight.ll"
    if not os.path.exists(ir_path):
        print(f"missing {ir_path}; run scripts/probe_stage1_closure.py first", file=sys.stderr)
        return 2
    print(f"reading {ir_path}", flush=True)
    ir_text = _read_ir(ir_path)
    print(f"  {len(ir_text)//1024} KiB, {ir_text.count(chr(10))} lines", flush=True)

    print("scanning...", flush=True)
    stats = _scan(ir_text)
    print(f"  total fallbacks: {stats['total']}", flush=True)
    print(f"  actions:         {stats['actions_total']}", flush=True)
    print(f"  plumbing:        {stats['plumbing_total']}", flush=True)

    out_md = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..",
                     "docs", "plans",
                     "stage1-closure-fallback-categories.md")
    )
    _write_report(stats, out_md, ir_path)
    print(f"wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
