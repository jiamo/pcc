"""Cross-module declarations must match definitions for every symbol.

An explicit ``-> None`` method encodes as a NoneType descriptor in the
class export schema; the extern-class declaration path lowered it to a
PyObject*-returning declaration while the defining module emitted
``define void``. The linker cannot see the mismatch, so callers rooted
leftover x0 as an owned object — latent under the pcc-Python port
runtime (leftover happened to be a heap object) and fatal under the
all-C runtime archives, where pcc_gc_frame_leave leaves a stack
frame-node address in x0 (libpy_runtime_pcc.a stage1 smoke abort; see
docs/investigations/libpy-runtime-pcc-archive-pure-c-chain-crashes.md).

The regression is generic: compile a two-module program and assert that
every ``declare``d symbol's return type matches its ``define``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

HELPER = """
class Helper:
    def __init__(self) -> None:
        self.n = 0

    def reset(self) -> None:
        xs = [1, 2, 3]
        xs.append(self.n)

    async def drain(self) -> None:
        return None
"""

MAIN = """
from helper_mod import Helper


def run() -> int:
    h = Helper()
    h.reset()
    return 0


run()
"""

_SIG = re.compile(r"^(declare|define)\s+(?:dso_local\s+)?(\S+)\s+@([\w.$]+)\(")


def _repo_root() -> Path:
    # tests/conftest.py monkeypatches Path.resolve for legacy paths, so
    # fixed parents[N] indexing is unreliable here; walk up to AGENTS.md.
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


def test_cross_module_declarations_match_definitions(tmp_path):
    (tmp_path / "helper_mod.py").write_text(HELPER, encoding="utf-8")
    (tmp_path / "main.py").write_text(MAIN, encoding="utf-8")
    out = tmp_path / "toy_ir"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv", "run", "python", "scripts/pcc_multi.py",
            "--entry", "main", "--emit-llvm",
            "--out", str(out),
            f"{tmp_path / 'main.py'}=main",
            f"{tmp_path / 'helper_mod.py'}=helper_mod",
        ],
        text=True, capture_output=True, timeout=280, env=env,
        cwd=str(_repo_root()),
    )
    assert build.returncode == 0, build.stderr[-2000:]

    defines: dict[str, str] = {}
    declares: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        m = _SIG.match(line.strip())
        if m is None:
            continue
        kind, ret, sym = m.groups()
        (defines if kind == "define" else declares)[sym] = ret
    assert defines, "combined IR listed no definitions"

    mismatched = {
        sym: (declares[sym], defines[sym])
        for sym in declares
        if sym in defines and declares[sym] != defines[sym]
    }
    assert not mismatched, f"declare/define return types diverge: {mismatched}"

    reset_sym = [s for s in defines if s.endswith("Helper_reset")]
    assert reset_sym and defines[reset_sym[0]] == "void"
