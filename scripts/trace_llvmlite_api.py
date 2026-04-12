#!/usr/bin/env python3
"""scripts/trace_llvmlite_api.py — β4.0 surface trace.

Instruments ``llvmlite.ir`` and ``llvmlite.binding`` to count every
call site hit during a workload, then emits a partitioned coverage
report for the β4.1 backlog.

Usage::

    python scripts/trace_llvmlite_api.py \
        --out docs/plans/llvmlite-api-surface.md

Workloads traced (hard-coded for now, edit ``_WORKLOADS`` below):

  W1. ``tests/py_corpus/phase1/*`` — Python frontend codegen
  W2. ``tests/py_corpus/phase3/dataclass_basic`` — class_gen codegen
  W3. Inline C via ``CEvaluator.evaluate()`` — C frontend codegen
  W4. ir_passes unit tests (subset) — parse_assembly + walk

For each llvmlite callable (class constructor, method, or module-level
function), records:
  - call count
  - set of calling source locations (file:line) up to first-K samples
  - argument type signature (to help sizing a drop-in replacement)

The partitioned report buckets by frequency + source:

  A. Codegen-core (>= 100 calls, hit from pcc.codegen / pcc.py_frontend)
  B. Passes-core (hit from pcc.ir_passes)
  C. Metadata / DWARF only
  D. Long-tail (< 10 calls total)

This does NOT modify codegen or ship anything. Data gathering only.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------


class _Counter:
    def __init__(self) -> None:
        # key: "module.Class.method" or "module.function"
        self.counts: dict[str, int] = defaultdict(int)
        # key → list of caller file:line (first 5 samples)
        self.samples: dict[str, list[str]] = defaultdict(list)
        # key → count of sample where caller was in pcc/codegen vs ir_passes vs py_frontend
        self.buckets: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def hit(self, key: str) -> None:
        self.counts[key] += 1
        # Caller location (skip wrapper itself)
        frame = sys._getframe(2)
        caller = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        if len(self.samples[key]) < 5:
            if caller not in self.samples[key]:
                self.samples[key].append(caller)
        # Bucket by pcc sub-package
        path = frame.f_code.co_filename
        bucket = _bucket_of(path)
        self.buckets[key][bucket] += 1


def _bucket_of(filepath: str) -> str:
    if "pcc/codegen/" in filepath:
        return "codegen"
    if "pcc/py_frontend/codegen/" in filepath:
        return "py_frontend_codegen"
    if "pcc/ir_passes/" in filepath:
        return "ir_passes"
    if "pcc/passes/" in filepath:
        return "passes"
    if "pcc/evaluater/" in filepath:
        return "evaluater"
    return "other"


def _wrap_callable(obj: Any, qualname: str, counter: _Counter) -> Any:
    """Wrap ``obj`` so calls increment ``counter[qualname]`` then
    delegate to the original."""

    def wrapper(*args, **kwargs):
        counter.hit(qualname)
        return obj(*args, **kwargs)

    wrapper.__name__ = getattr(obj, "__name__", qualname)
    wrapper.__wrapped__ = obj
    return wrapper


def _instrument_class(cls: type, module_prefix: str, counter: _Counter) -> None:
    """Replace every public method on ``cls`` with a counting wrapper."""
    for name in list(vars(cls)):
        if name.startswith("_"):
            continue
        attr = vars(cls).get(name)
        if not callable(attr):
            continue
        qn = f"{module_prefix}.{cls.__name__}.{name}"
        try:
            setattr(cls, name, _wrap_callable(attr, qn, counter))
        except (TypeError, AttributeError):
            pass  # some slots / descriptors refuse assignment

    # Also wrap __init__ so we count construction — some ctypes-
    # backed classes are immutable, just skip those.
    if "__init__" in vars(cls):
        try:
            orig_init = cls.__init__
            qn = f"{module_prefix}.{cls.__name__}.__init__"

            def init_wrapper(self, *args, orig=orig_init, qn=qn, **kwargs):
                counter.hit(qn)
                return orig(self, *args, **kwargs)

            cls.__init__ = init_wrapper
        except (TypeError, AttributeError):
            pass


def install_hooks() -> _Counter:
    counter = _Counter()

    import llvmlite.ir as ir
    import llvmlite.binding as llvm

    # Instrument every public class in llvmlite.ir
    for name in dir(ir):
        if name.startswith("_"):
            continue
        obj = getattr(ir, name)
        if isinstance(obj, type):
            _instrument_class(obj, "ir", counter)
        elif callable(obj) and not isinstance(obj, type):
            # module-level function
            setattr(ir, name, _wrap_callable(obj, f"ir.{name}", counter))

    # Instrument llvmlite.binding (mostly functions + a few classes)
    for name in dir(llvm):
        if name.startswith("_"):
            continue
        obj = getattr(llvm, name)
        if isinstance(obj, type):
            _instrument_class(obj, "llvm", counter)
        elif callable(obj) and not isinstance(obj, type):
            try:
                setattr(llvm, name, _wrap_callable(obj, f"llvm.{name}", counter))
            except (TypeError, AttributeError):
                pass

    return counter


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------


def run_python_corpus(phase: str) -> int:
    """Run each Python source under tests/py_corpus/<phase>/ via the
    Python pipeline, emit IR only (don't link). Returns #files run."""
    from pcc.py_frontend.pipeline import compile_python, PyPipelineError

    phase_dir = REPO / "tests" / "py_corpus" / phase
    if not phase_dir.is_dir():
        return 0
    n = 0
    for sub in sorted(phase_dir.iterdir()):
        src = sub / "source.py"
        if not src.is_file():
            continue
        out_ll = f"/tmp/pcc-trace-{phase}-{sub.name}.ll"
        try:
            compile_python(str(src), out_ll, emit_llvm_only=True)
            n += 1
        except PyPipelineError:
            pass
        except Exception:
            traceback.print_exc(limit=1)
    return n


def run_c_inline() -> int:
    """A broader handful of C snippets through CEvaluator to cover
    the C frontend codegen path. β5.0 expanded to hit more of
    c_codegen.py's ir.* surface (struct access, pointer arith,
    typedef, switch, globals, function ptr, enum, string lit)."""
    from pcc.evaluater.c_evaluator import CEvaluator
    from pcc.codegen.c_codegen import LLVMCodeGenerator

    snippets = [
        # arithmetic
        "int main(){int a=3, b=5; return a*b+2;}",
        # control flow
        "int main(){int s=0; for(int i=0;i<10;i++) s+=i; return s;}",
        # function call + recursion
        """int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2);}
           int main(){ return fib(10); }""",
        # struct
        """struct P { int x; int y; };
           int main(){ struct P p = {3, 5}; return p.x + p.y; }""",
        # pointer + array
        """int main(){ int a[5] = {1,2,3,4,5}; int *p = a; return p[3]; }""",
        # typedef + nested struct
        """typedef struct Point { int x; int y; } Point;
           typedef struct Rect { Point tl; Point br; } Rect;
           int main(){ Rect r = {{1,2},{3,4}}; return r.br.x - r.tl.x; }""",
        # switch
        """int main(){ int x = 2; switch(x) {
             case 1: return 10;
             case 2: return 20;
             default: return 99;
           } }""",
        # global
        """static int g = 42;
           int main(){ return g; }""",
        # function pointer
        """int add(int a, int b){ return a+b; }
           int mul(int a, int b){ return a*b; }
           int main(){
             int (*fp)(int,int) = add;
             int s = fp(3, 4);
             fp = mul;
             return s + fp(3, 4);
           }""",
        # enum + bitwise
        """enum { F_A=1, F_B=2, F_C=4 };
           int main(){ return F_A | F_B | F_C; }""",
        # string literal + strlen
        """int strlen(const char *s);
           int main(){ return strlen("hello"); }""",
        # char type + ternary
        """int main(){ char c = 'A'; return (c > 'Z') ? 0 : c; }""",
        # nested control flow
        """int main(){
             int sum = 0;
             for (int i = 0; i < 5; i++) {
               if (i == 2) continue;
               if (i == 4) break;
               sum += i * 2;
             }
             return sum;
           }""",
        # sizeof
        """int main(){ return (int)sizeof(long long) + (int)sizeof(int); }""",
        # pointer arithmetic + cast
        """int main(){
             int arr[4] = {10, 20, 30, 40};
             int *p = arr + 2;
             return *p - *(p - 1);
           }""",
    ]
    cev = CEvaluator()
    n = 0
    for s in snippets:
        try:
            cev.evaluate(s)
            n += 1
        except Exception:
            pass

    # Also generate one with debug info to capture the DI* surface.
    try:
        from pcc.parse import make_c_parser
        parser = make_c_parser()
        ast = parser.parse(snippets[0])
        cg = LLVMCodeGenerator(emit_debug=True)
        cg.generate(ast)
        n += 1
    except Exception:
        traceback.print_exc(limit=1)
    return n


def run_ir_passes_tests() -> int:
    """Run a subset of IR-pass tests **in-process** via pytest.main
    so our monkey-patches stick. If we spawned a subprocess the child
    interpreter wouldn't inherit the instrumentation.

    pyproject has ``addopts = "-n auto"`` which activates pytest-xdist
    — that forks a worker pool, losing our monkey-patch. We override
    by re-passing ``-p no:xdist -n0 -o addopts=``.
    """
    import pytest as _pytest

    subset = [
        "tests/test_ir_passes_parity.py",
        "tests/test_ir_passes_adce.py",
        "tests/test_ir_passes_instcombine.py",
        "tests/test_ir_passes_mem2reg_real.py",
    ]
    try:
        _pytest.main([
            "-q", "-p", "no:xdist",
            "-o", "addopts=",   # clobber pyproject's `-n auto`
            *subset,
        ])
    except SystemExit:
        pass
    except Exception:
        traceback.print_exc(limit=1)
    return len(subset)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_markdown(counter: _Counter) -> str:
    """Emit a partitioned markdown report."""
    buckets = {
        "A_codegen_core": [],     # codegen/py_frontend_codegen hitters
        "B_passes_core": [],      # ir_passes hitters
        "C_metadata_dwarf": [],   # DI* classes
        "D_long_tail": [],        # < 10 calls
        "E_binding": [],          # llvm.* (binding)
    }
    for key, count in counter.counts.items():
        if count == 0:
            continue
        bmap = counter.buckets[key]
        is_binding = key.startswith("llvm.")
        is_metadata = any(tag in key for tag in ("DIBuilder", "DIToken",
                            "DIFile", "DISubprog", "DILocation",
                            "DICompileUnit", "Metadata", "NamedMeta"))
        cgcore = bmap.get("codegen", 0) + bmap.get("py_frontend_codegen", 0)
        passcore = bmap.get("ir_passes", 0) + bmap.get("passes", 0)

        if count < 10:
            bucket = "D_long_tail"
        elif is_metadata:
            bucket = "C_metadata_dwarf"
        elif is_binding:
            bucket = "E_binding"
        elif cgcore > passcore:
            bucket = "A_codegen_core"
        elif passcore > 0:
            bucket = "B_passes_core"
        else:
            bucket = "D_long_tail"
        buckets[bucket].append((count, key, bmap))

    for k in buckets:
        buckets[k].sort(key=lambda row: -row[0])

    lines: list[str] = []
    lines.append("# llvmlite API surface trace (β4.0)\n")
    lines.append(f"Total unique callables hit: **{len(counter.counts)}**\n")
    lines.append(f"Total call events: **{sum(counter.counts.values())}**\n")

    lines.append("## Partition legend\n")
    lines.append("- **A codegen-core**: >= 10 calls, hit from pcc.codegen / pcc.py_frontend.codegen\n")
    lines.append("- **B passes-core**: >= 10 calls, hit from pcc.ir_passes / pcc.passes\n")
    lines.append("- **C metadata/DWARF**: DIBuilder/DIFile/DIToken/Metadata APIs\n")
    lines.append("- **D long-tail**: < 10 calls total\n")
    lines.append("- **E binding**: llvmlite.binding (JIT, target, parse_assembly)\n")

    for title, key in [
        ("A — codegen-core (β4.1 priority)", "A_codegen_core"),
        ("B — passes-core (β4.2 priority)", "B_passes_core"),
        ("E — binding (β4.2 priority)", "E_binding"),
        ("C — metadata / DWARF (β4.3 priority)", "C_metadata_dwarf"),
        ("D — long-tail (β4.3 backlog)", "D_long_tail"),
    ]:
        rows = buckets[key]
        lines.append(f"\n## {title}\n")
        lines.append(f"{len(rows)} entries, {sum(r[0] for r in rows)} calls total.\n")
        lines.append("| API | Count | Hit from |")
        lines.append("|---|---|---|")
        for count, api, bmap in rows[:80]:
            hits = ", ".join(f"{b}:{n}" for b, n in sorted(bmap.items()) if n)
            lines.append(f"| `{api}` | {count} | {hits} |")
        if len(rows) > 80:
            lines.append(f"| ... (+{len(rows) - 80} more) | | |")

    lines.append("\n## Samples (caller file:line for each top-A entry)\n")
    for count, api, bmap in buckets["A_codegen_core"][:20]:
        samples = counter.samples.get(api, [])
        if samples:
            lines.append(f"- `{api}` ({count} calls):")
            for s in samples:
                rel = s
                try:
                    rel = str(Path(s.split(":")[0]).relative_to(REPO)) + ":" + s.split(":", 1)[1]
                except ValueError:
                    pass
                lines.append(f"    - `{rel}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/plans/llvmlite-api-surface.md")
    ap.add_argument("--skip-passes-tests", action="store_true",
                    help="Skip ir_passes pytest workload (faster iteration)")
    args = ap.parse_args()

    counter = install_hooks()

    print("running W1: py_corpus/phase1 ...")
    n1 = run_python_corpus("phase1")
    print(f"  {n1} files")

    print("running W2: py_corpus/phase3 ...")
    n2 = run_python_corpus("phase3")
    print(f"  {n2} files")

    print("running W3: C inline via CEvaluator ...")
    n3 = run_c_inline()
    print(f"  {n3} snippets")

    if not args.skip_passes_tests:
        print("running W4: ir_passes tests ...")
        n4 = run_ir_passes_tests()
        print(f"  {n4} test files")

    report = render_markdown(counter)
    out_path = REPO / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"\nwrote {out_path}")
    print(f"  unique callables: {len(counter.counts)}")
    print(f"  total events: {sum(counter.counts.values())}")
    # Also emit raw JSON alongside for programmatic consumers
    raw_path = out_path.with_suffix(".json")
    raw_path.write_text(json.dumps(
        {
            "counts": dict(counter.counts),
            "samples": {k: list(v) for k, v in counter.samples.items()},
            "buckets": {k: dict(v) for k, v in counter.buckets.items()},
        },
        indent=2, sort_keys=True,
    ))
    print(f"  wrote raw: {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
