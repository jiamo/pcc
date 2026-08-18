#!/usr/bin/env python3
"""Per-operation cost table: pcc-compiled runtime versus CPython.

Each benchmark isolates ONE Python operation inside a counted loop.  The same
source runs (a) compiled by pcc (``--backend self --python-libpython=off``, the
pcc runtime that pcc1 itself executes) and (b) under CPython.  Both are timed
with ``/usr/bin/time -lp`` at two loop counts, N and 2N; the difference
removes process startup and any per-run fixed cost, so

    per_op = (measure(2N) - measure(N)) / N

for instructions retired and wall nanoseconds.  The output ranks operations by
the pcc/CPython instruction ratio, which is where the self-host Stage2 gap
lives (pcc1 spends ~98% of its samples in the runtime, not in compiler logic).

usage: pcc_per_op_cost_table.py --out-dir DIR [--n N] [--only op,op] [--json receipt]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = "import os\n\nN = int(os.environ.get('BENCH_N', '1000000'))\n\n"

# name -> program body.  Every program prints one checksum line so a wrong
# answer is visible; loops are written in the typed subset pcc lowers natively.
BENCHMARKS: dict[str, str] = {
    "int_add": (
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total += i\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "attr_load": (
        "class P:\n"
        "    def __init__(self, v: int):\n"
        "        self.v = v\n"
        "\n"
        "def main() -> None:\n"
        "    p = P(3)\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total += p.v\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "attr_store": (
        "class P:\n"
        "    def __init__(self, v: int):\n"
        "        self.v = v\n"
        "\n"
        "def main() -> None:\n"
        "    p = P(0)\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        p.v = i\n"
        "        i += 1\n"
        "    print(p.v)\n"
    ),
    "call_int2": (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total = add(total, i)\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "call_returns_obj": (
        "def pair(a: int) -> list:\n"
        "    return [a, a]\n"
        "\n"
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        x = pair(i)\n"
        "        total += len(x)\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "method_call": (
        "class Acc:\n"
        "    def __init__(self):\n"
        "        self.total = 0\n"
        "    def add(self, v: int) -> None:\n"
        "        self.total += v\n"
        "\n"
        "def main() -> None:\n"
        "    a = Acc()\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        a.add(i)\n"
        "        i += 1\n"
        "    print(a.total)\n"
    ),
    "dict_get_str": (
        "def main() -> None:\n"
        "    d = {}\n"
        "    keys = []\n"
        "    k = 0\n"
        "    while k < 64:\n"
        "        key = 'symbol_' + str(k)\n"
        "        d[key] = k\n"
        "        keys.append(key)\n"
        "        k += 1\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total += d[keys[i & 63]]\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "dict_set_str": (
        "def main() -> None:\n"
        "    keys = []\n"
        "    k = 0\n"
        "    while k < 64:\n"
        "        keys.append('symbol_' + str(k))\n"
        "        k += 1\n"
        "    d = {}\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        d[keys[i & 63]] = i\n"
        "        i += 1\n"
        "    print(len(d))\n"
    ),
    "list_append_pop": (
        "def main() -> None:\n"
        "    xs = []\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        xs.append(i)\n"
        "        if len(xs) > 1000:\n"
        "            xs.pop()\n"
        "        i += 1\n"
        "    print(len(xs))\n"
    ),
    "list_index": (
        "def main() -> None:\n"
        "    xs = []\n"
        "    k = 0\n"
        "    while k < 1024:\n"
        "        xs.append(k)\n"
        "        k += 1\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total += xs[i & 1023]\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "str_concat_small": (
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        s = 'ab' + 'cd'\n"
        "        total += len(s)\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "str_of_int": (
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        total += len(str(i))\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "str_strip_split": (
        "def main() -> None:\n"
        "    line = '  add x0, x1, #16  '\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        parts = line.strip().split(',')\n"
        "        total += len(parts)\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "str_eq_dispatch": (
        "def main() -> None:\n"
        "    names = ['add', 'sub', 'ldr', 'str', 'b', 'bl', 'ret', 'cmp']\n"
        "    hits = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        mn = names[i & 7]\n"
        "        if mn == 'ret':\n"
        "            hits += 1\n"
        "        elif mn == 'bl':\n"
        "            hits += 2\n"
        "        elif mn == 'cmp':\n"
        "            hits += 3\n"
        "        i += 1\n"
        "    print(hits)\n"
    ),
    "isinstance_class": (
        "class A:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "class B(A):\n"
        "    def __init__(self):\n"
        "        self.x = 2\n"
        "\n"
        "def main() -> None:\n"
        "    objs = [A(), B(), A(), B()]\n"
        "    hits = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        if isinstance(objs[i & 3], B):\n"
        "            hits += 1\n"
        "        i += 1\n"
        "    print(hits)\n"
    ),
    "tuple_pack_unpack": (
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        t = (i, i + 1)\n"
        "        a, b = t\n"
        "        total += a + b\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
    "for_over_list": (
        "def main() -> None:\n"
        "    xs = []\n"
        "    k = 0\n"
        "    while k < 1000:\n"
        "        xs.append(k)\n"
        "        k += 1\n"
        "    total = 0\n"
        "    rounds = 0\n"
        "    while rounds * 1000 < N:\n"
        "        for x in xs:\n"
        "            total += x\n"
        "        rounds += 1\n"
        "    print(total)\n"
    ),
    "try_except_raise": (
        "def boom(i: int) -> int:\n"
        "    if i >= 0:\n"
        "        raise ValueError('x')\n"
        "    return i\n"
        "\n"
        "def main() -> None:\n"
        "    caught = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        try:\n"
        "            boom(i)\n"
        "        except ValueError:\n"
        "            caught += 1\n"
        "        i += 1\n"
        "    print(caught)\n"
    ),
    "alloc_small_object": (
        "class Node:\n"
        "    def __init__(self, v: int):\n"
        "        self.v = v\n"
        "\n"
        "def main() -> None:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < N:\n"
        "        n = Node(i)\n"
        "        total += n.v\n"
        "        i += 1\n"
        "    print(total)\n"
    ),
}

_TIME_RE = {
    "real_s": re.compile(r"^real\s+([\d.]+)", re.M),
    "user_s": re.compile(r"^user\s+([\d.]+)", re.M),
    "instructions": re.compile(r"^\s*(\d+)\s+instructions retired", re.M),
    "max_rss": re.compile(r"^\s*(\d+)\s+maximum resident set size", re.M),
}


def parse_time_lp(stderr: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, rx in _TIME_RE.items():
        m = rx.search(stderr)
        if m:
            out[key] = float(m.group(1))
    return out


def per_op(m_n: dict[str, float], m_2n: dict[str, float], n: int) -> dict[str, float]:
    """Difference two measurements taken at N and 2N iterations."""
    result: dict[str, float] = {}
    if "instructions" in m_n and "instructions" in m_2n:
        result["instr_per_op"] = (m_2n["instructions"] - m_n["instructions"]) / n
    if "real_s" in m_n and "real_s" in m_2n:
        result["ns_per_op"] = (m_2n["real_s"] - m_n["real_s"]) * 1e9 / n
    return result


def _run_timed(cmd: list[str], env: dict[str, str], timeout: int) -> tuple[dict[str, float], str]:
    proc = subprocess.run(
        ["/usr/bin/time", "-lp", *cmd],
        capture_output=True, text=True, timeout=timeout, env=env, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} rc={proc.returncode}: {proc.stderr[-400:]}")
    return parse_time_lp(proc.stderr), proc.stdout.strip()


def compile_pcc(src: Path, exe: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO_ROOT), check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("pcc compile failed: " + proc.stderr[-600:])


def measure(name: str, body: str, out_dir: Path, n: int, python: str) -> dict:
    src = out_dir / f"{name}.py"
    src.write_text(PREAMBLE + body + "\nmain()\n", encoding="utf-8")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
    exe = out_dir / f"{name}.{digest}.bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    row: dict = {"op": name, "n": n, "source_sha256_12": digest}
    try:
        if not exe.exists():
            t0 = time.monotonic()
            compile_pcc(src, exe, env)
            row["pcc_compile_s"] = round(time.monotonic() - t0, 2)
    except RuntimeError as exc:
        row["pcc_error"] = str(exc)[-300:]
        return row
    arms = {"pcc": [str(exe)], "cpython": [python, str(src)]}
    for arm, cmd in arms.items():
        try:
            e1 = dict(env, BENCH_N=str(n))
            e2 = dict(env, BENCH_N=str(2 * n))
            m1, out1 = _run_timed(cmd, e1, 300)
            m2, out2 = _run_timed(cmd, e2, 300)
            row[arm] = {"n": m1, "2n": m2, "stdout_n": out1, "stdout_2n": out2, **per_op(m1, m2, n)}
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            row[arm] = {"error": str(exc)[-300:]}
    if "instr_per_op" in row.get("pcc", {}) and "instr_per_op" in row.get("cpython", {}):
        c = row["cpython"]["instr_per_op"]
        row["instr_ratio"] = row["pcc"]["instr_per_op"] / c if c else None
        cn = row["cpython"].get("ns_per_op")
        pn = row["pcc"].get("ns_per_op")
        row["ns_ratio"] = (pn / cn) if (cn and pn is not None) else None
        row["outputs_match"] = (
            row["pcc"]["stdout_n"] == row["cpython"]["stdout_n"]
            and row["pcc"]["stdout_2n"] == row["cpython"]["stdout_2n"]
        )
    return row


def render_table(rows: list[dict]) -> str:
    lines = ["op                  pcc instr/op  cpy instr/op  ratio   pcc ns/op  cpy ns/op  ratio  match"]
    ok = [r for r in rows if r.get("instr_ratio") is not None]
    ok.sort(key=lambda r: -r["instr_ratio"])
    for r in ok:
        lines.append(
            "%-19s %12.0f %13.0f %6.1fx %10.0f %10.0f %6.1fx  %s" % (
                r["op"], r["pcc"]["instr_per_op"], r["cpython"]["instr_per_op"], r["instr_ratio"],
                r["pcc"].get("ns_per_op", float("nan")), r["cpython"].get("ns_per_op", float("nan")),
                r["ns_ratio"] if r["ns_ratio"] is not None else float("nan"),
                "yes" if r.get("outputs_match") else "NO",
            )
        )
    for r in rows:
        if r.get("instr_ratio") is None:
            lines.append("%-19s UNAVAILABLE %s" % (r["op"], (r.get("pcc_error") or r.get("pcc", {}).get("error") or r.get("cpython", {}).get("error") or "")[:90]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--n", type=int, default=1_000_000)
    parser.add_argument("--only", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--python", default=str(REPO_ROOT / ".venv" / "bin" / "python"))
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [x for x in args.only.split(",") if x] or list(BENCHMARKS)
    rows = []
    for name in names:
        row = measure(name, BENCHMARKS[name], out_dir, args.n, args.python)
        rows.append(row)
        r = row.get("instr_ratio")
        print("[%s] %s" % (name, ("%.1fx instructions" % r) if r else "unavailable"), flush=True)
    table = render_table(rows)
    print(table)
    if args.json:
        Path(args.json).write_text(json.dumps({"schema": "pcc.per-op-cost-table.v1", "n": args.n, "python": args.python, "rows": rows, "table": table}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
