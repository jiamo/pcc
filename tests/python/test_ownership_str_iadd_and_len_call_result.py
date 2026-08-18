"""Two codegen ownership leaks measured through max RSS of a compiled binary.

Found while attributing a pcc1 codegen worker's 3.27 GiB peak
(docs/investigations/pcc-codegen-ownership-leaks-str-iadd-and-call-result.md):

- ``cur += ch`` on a str local dropped the previous value on the floor while
  ``cur = cur + ch`` released it (299 MB vs 3 MB for 20k characters).
- ``len(f())`` with ``f`` returning an exact list never released the owned
  call result (116 MB vs 3 MB for 300k calls); ``x = f(); len(x)`` was fine.

Both programs would use a few MB under CPython.  The threshold leaves room
above the ~36 MB runtime baseline and far below the leaking numbers.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

LIMIT = 90 * 1024 * 1024

STR_IADD = (
    "def main() -> None:\n"
    "    s = 'abcdefghij' * 2000\n"
    "    cur = ''\n"
    "    for ch in s:\n"
    "        cur += ch\n"
    "    print(len(cur))\n"
    "\n"
    "main()\n"
)

LEN_OF_LIST_CALL = (
    "def f(i: int) -> list:\n"
    "    out = []\n"
    "    out.append(i)\n"
    "    out.append(i + 1)\n"
    "    return out\n"
    "\n"
    "def main() -> None:\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 300000:\n"
    "        total += len(f(i))\n"
    "        i += 1\n"
    "    print(total)\n"
    "\n"
    "main()\n"
)


LEN_OF_STR_CALL = (
    "def f(i: int) -> str:\n"
    "    return str(i) + 'abc'\n"
    "\n"
    "def main() -> None:\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 300000:\n"
    "        total += len(f(i))\n"
    "        i += 1\n"
    "    print(total)\n"
    "\n"
    "main()\n"
)


def _max_rss_of(tmp_path: Path, name: str, source: str) -> tuple[int, str]:
    src = tmp_path / f"{name}.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / f"{name}.bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        ["/usr/bin/time", "-l", str(exe)],
        text=True, capture_output=True, timeout=120, env=env,
    )
    assert run.returncode == 0, run.stderr
    max_rss = 0
    for line in run.stderr.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "maximum resident set size" in line:
            max_rss = int(parts[0])
    assert max_rss > 0, run.stderr
    return max_rss, run.stdout.strip()


@pytest.mark.parametrize(
    "name, source, expected",
    [
        ("str_iadd", STR_IADD, "20000"),
        ("len_of_list_call", LEN_OF_LIST_CALL, "600000"),
        # Control: a str call result was already released; the list fix must
        # not turn this into a double release.
        ("len_of_str_call", LEN_OF_STR_CALL, "2588890"),
    ],
)
def test_no_per_iteration_leak(tmp_path, name, source, expected):
    max_rss, out = _max_rss_of(tmp_path, name, source)
    assert out == expected
    assert max_rss < LIMIT, f"{name} leaked: max RSS {max_rss / 1e6:.0f} MB"
