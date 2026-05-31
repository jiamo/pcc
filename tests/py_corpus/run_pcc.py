#!/usr/bin/env python3
"""Phase 5 benchmark + acceptance harness for pcc's Python frontend.

Walks ``tests/py_corpus/phase[1-4]/*/`` and for each test:

  1. compiles ``source.py`` with pcc → native exe,
  2. runs the exe, captures stdout + exit code,
  3. diffs against the recorded ``expected.stdout`` / ``expected.status``,
  4. (optional, with ``--bench``) measures compile time, exe size, and
     wall-time of 10 runs via ``time``.

Unlike :mod:`run_phase1`, this harness goes through the real pcc
pipeline — it's the gate Phase 1–5 acceptance criteria ultimately
measure against.

Usage::

    python tests/py_corpus/run_pcc.py              # run everything
    python tests/py_corpus/run_pcc.py --phase phase4
    python tests/py_corpus/run_pcc.py --bench      # add timing / size
    python tests/py_corpus/run_pcc.py --filter math
    python tests/py_corpus/run_pcc.py -v

Exit code: 0 if every enabled test matches; 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import re
from pathlib import Path


CORPUS_ROOT = Path(__file__).resolve().parent
OVERRIDE_TARGET_TRIPLE_WARNING = re.compile(
    r"warning: overriding the module target triple with "
)


def load_expected(test_dir: Path) -> tuple[bytes, int] | None:
    stdout_path = test_dir / "expected.stdout"
    status_path = test_dir / "expected.status"
    source_path = test_dir / "source.py"
    if not (
        stdout_path.is_file()
        and status_path.is_file()
        and source_path.is_file()
    ):
        return None
    stdout_bytes = stdout_path.read_bytes()
    status_text = status_path.read_text().strip()
    try:
        status = int(status_text)
    except ValueError:
        raise SystemExit(
            f"{status_path}: expected.status must be an integer, got "
            f"{status_text!r}"
        )
    return stdout_bytes, status


def compile_one(src: Path, out: Path, verbose: bool) -> tuple[bool, float]:
    t0 = time.monotonic()
    cmd = [sys.executable, "-m", "pcc", str(src), "-o", str(out)]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"{' '.join(cmd)}: {exc}") from exc
    output = ""
    if completed.stdout:
        output += completed.stdout
    if completed.stderr:
        output += completed.stderr
    if verbose and output:
        filtered = [
            line
            for line in output.splitlines()
            if not OVERRIDE_TARGET_TRIPLE_WARNING.search(line)
        ]
        if filtered:
            print("\n".join(filtered))

    if completed.returncode != 0:
        # Keep all non-filtered compiler output visible even when not verbose.
        if not verbose and output:
            filtered = [
                line
                for line in output.splitlines()
                if not OVERRIDE_TARGET_TRIPLE_WARNING.search(line)
            ]
            if filtered:
                print("\n".join(filtered))
        return False, time.monotonic() - t0

    return True, time.monotonic() - t0


def run_one(exe: Path) -> tuple[bytes, int, float]:
    t0 = time.monotonic()
    r = subprocess.run([str(exe)], capture_output=True, timeout=30)
    dt = time.monotonic() - t0
    return r.stdout, r.returncode, dt


def run_case(
    test_dir: Path, verbose: bool, bench: bool, tmp: Path,
) -> tuple[bool, dict]:
    exp = load_expected(test_dir)
    if exp is None:
        return False, {"reason": "missing expected.*"}
    exp_out, exp_status = exp

    name = test_dir.name
    src = test_dir / "source.py"
    exe = tmp / name

    ok, compile_dt = compile_one(src, exe, verbose)
    if not ok:
        return False, {"reason": "compile-failed", "compile_s": compile_dt}
    if not exe.exists():
        return False, {"reason": "no-exe", "compile_s": compile_dt}

    size = exe.stat().st_size
    try:
        got_out, got_status, run_dt = run_one(exe)
    except subprocess.TimeoutExpired:
        return False, {
            "reason": "run-timeout",
            "compile_s": compile_dt,
            "size": size,
        }

    info = {
        "compile_s": compile_dt,
        "run_s": run_dt,
        "size": size,
        "status": got_status,
    }
    if got_out != exp_out or got_status != exp_status:
        info["reason"] = "output-mismatch"
        return False, info

    if bench:
        # Best-of-3 wall time for the run phase.
        best = run_dt
        for _ in range(2):
            _, _, dt = run_one(exe)
            if dt < best:
                best = dt
        info["best_run_s"] = best
    return True, info


def main() -> int:
    p = argparse.ArgumentParser(
        description="pcc Phase-1..4 end-to-end acceptance / bench runner"
    )
    p.add_argument("--phase", default=None,
                   help="limit to phase1 / phase2 / phase3 / phase4")
    p.add_argument("--filter", default="",
                   help="substring match on test directory name")
    p.add_argument("--bench", action="store_true",
                   help="report compile/run time and exe size")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.phase:
        phases = [CORPUS_ROOT / args.phase]
    else:
        phases = sorted(
            d for d in CORPUS_ROOT.iterdir()
            if d.is_dir() and d.name.startswith("phase")
        )

    total = 0
    passed = 0
    fails: list[tuple[str, dict]] = []
    perf_rows: list[tuple[str, dict]] = []

    with tempfile.TemporaryDirectory(prefix="pcc_corpus_") as tmpdir:
        tmp = Path(tmpdir)
        for ph in phases:
            tests = sorted(
                d for d in ph.iterdir() if d.is_dir()
            )
            for t in tests:
                if args.filter and args.filter not in t.name:
                    continue
                if not (t / "source.py").exists():
                    continue
                total += 1
                ok, info = run_case(t, args.verbose, args.bench, tmp)
                if ok:
                    passed += 1
                    if args.bench:
                        perf_rows.append((f"{ph.name}/{t.name}", info))
                    if args.verbose:
                        print(f"PASS {ph.name}/{t.name}")
                else:
                    fails.append((f"{ph.name}/{t.name}", info))
                    if args.verbose:
                        print(f"FAIL {ph.name}/{t.name}: {info.get('reason')}")

    print(f"\n{passed}/{total} tests passed")

    if args.bench and perf_rows:
        print("\nPerf summary:")
        print(f"  {'test':<40} {'compile s':>10} {'run s':>8} {'size KB':>10}")
        for name, info in perf_rows:
            print(
                f"  {name:<40} "
                f"{info['compile_s']:>10.3f} "
                f"{info['best_run_s']:>8.3f} "
                f"{info['size']/1024.0:>10.1f}"
            )

    if fails:
        print("\nFailures:")
        for name, info in fails[:40]:
            print(f"  {name}: {info.get('reason', 'unknown')}")
        if len(fails) > 40:
            print(f"  ... ({len(fails) - 40} more)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
