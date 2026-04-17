#!/usr/bin/env python3
"""Stage1 closure probe — ON mode variant (Path A measurement).

Runs each module in the tight closure through codegen WITH
``ir_scaffold_mode='on'`` and reports per-module fallback counts so we
can see where Path A stands. Modules that hit
``ScaffoldUnsupportedError`` are reported as failed (the error itself
names the missing method/symbol — that's the Phase-3+ migration TODO).

Usage:
    python3 scripts/probe_stage1_closure_on_mode.py
"""
from __future__ import annotations

import os
import re
import sys
import traceback


def main() -> int:
    sys.path.insert(
        0,
        os.path.abspath(os.path.dirname(os.path.dirname(__file__))),
    )

    import importlib.util as _imputil
    spec = _imputil.spec_from_file_location(
        "_probe_tight",
        os.path.join(os.path.dirname(__file__), "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)

    from pcc.py_frontend import type_infer as _type_infer
    from pcc.py_frontend.codegen import layer1 as _layer1
    from pcc.parse.py_lift import parse_and_lift

    entry = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "pcc", "__main__.py",
        )
    )
    srcs, mods = probe_mod._tightened_closure(entry)
    print(f"closure: {len(srcs)} files (tight)", flush=True)
    print()

    results = []
    for src, mod in zip(srcs, mods):
        with open(src, "r", encoding="utf-8") as f:
            source = f.read()
        ast_mod = parse_and_lift(source, src, mod)
        # OFF baseline
        try:
            typed = _type_infer.infer_module(ast_mod)
            cg_off = _layer1.L1CodeGen(
                typed, emit_cpy_main_exitcode=False,
                ir_scaffold_mode="off",
            )
            ir_off = str(cg_off.generate(typed))
            n_off = len(re.findall(
                r"\bcall [^\n]*@py_cpy_", ir_off,
            ))
            off_status = "ok"
        except Exception as e:
            n_off = -1
            off_status = f"OFF FAIL: {type(e).__name__}: {e}"
        # ON path
        try:
            typed = _type_infer.infer_module(ast_mod)
            cg_on = _layer1.L1CodeGen(
                typed, emit_cpy_main_exitcode=False,
                ir_scaffold_mode="on",
            )
            ir_on = str(cg_on.generate(typed))
            n_on = len(re.findall(
                r"\bcall [^\n]*@py_cpy_", ir_on,
            ))
            on_status = "ok"
        except _layer1.ScaffoldUnsupportedError as e:
            n_on = None
            on_status = f"ON STOP: {e}"
            if os.environ.get("PCC_PROBE_VERBOSE"):
                tb = traceback.format_exc()
                print(f"\n--- {mod} traceback ---\n{tb}", flush=True)
        except Exception as e:
            n_on = None
            on_status = f"ON FAIL: {type(e).__name__}: {e}"
        results.append(
            (mod, n_off, n_on, off_status, on_status),
        )

    # Print summary
    print(f"{'module':<45} {'OFF':>6} {'ON':>6} {'Δ':>7}  status")
    print("-" * 90)
    sum_off = sum_on = 0
    migrated_count = 0
    for mod, n_off, n_on, off_status, on_status in results:
        if n_off is None or n_off < 0:
            print(f"{mod:<45} {'?':>6} {'?':>6} {'?':>7}  {off_status}")
            continue
        if n_on is None:
            # ON failed: report stop reason
            print(
                f"{mod:<45} {n_off:>6} {'STOP':>6} {'?':>7}  "
                + on_status[:60]
            )
            continue
        sum_off += n_off
        sum_on += n_on
        migrated_count += 1
        delta = n_off - n_on
        pct = (delta / n_off * 100) if n_off > 0 else 0.0
        print(
            f"{mod:<45} {n_off:>6} {n_on:>6} {delta:>7}  "
            f"({pct:.0f}%)"
        )

    print("-" * 90)
    if sum_off > 0:
        print(
            f"{'TOTAL (modules with ON success)':<45} "
            f"{sum_off:>6} {sum_on:>6} {sum_off - sum_on:>7}  "
            f"({(1 - sum_on / sum_off) * 100:.1f}%)"
        )
        print(
            f"migrated cleanly: {migrated_count}/{len(results)} modules"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
