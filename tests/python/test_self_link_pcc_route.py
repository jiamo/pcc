"""Darwin arm64 defaults to pcc's subprocess self-link route.

LINK-P1-MACHO-LINK-SWITCH wiring, third attempt — the history is the spec:

1. In-process import of `pcc.backend.macho_*` pulled the Mach-O toolchain
   into the stage1 self-host closure (forbidden by AGENTS.md) and broke the
   fallback-baseline gate. Reverted.
2. The subprocess seam (`scripts/pcc_link_macho.py`) was then wired into only
   ONE of the two link sites in `pipeline.py`, and the probe program used the
   other — so `PCC_SELF_LINK=pcc` silently linked with cc and looked like it
   worked. Caught by checking the artifact for pcc's identifier.
3. Both sites now route through one shared `_run_self_link_command`.

What this suite pins:

- the unset/default route produces an artifact carrying pcc's exact
  CodeDirectory identifier;
- cc/ld remains available only through explicit ``PCC_SELF_LINK=cc`` and its
  artifact does not carry pcc's identity;
- the simple, rich-object/exception, and generator programs match that
  explicit cc oracle across repeated ASLR launches;
- the stage1 closure remains free of the host-only `pcc.backend.macho_*` and
  `pcc.backend.arm64_*` toolchain (the reason the seam is a subprocess).  The
  independent fallback ratchet owns closure growth and rejects new semantic
  actions; a raw file count would incorrectly reject source decomposition.

The route now links the real runtime. Successful default builds are identified
by parsing their CodeDirectory, rather than by output equality or a substring
search that a cc-linked binary could satisfy accidentally.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.backend.macho_codesign import parse_signature

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)

PROGRAM = (
    "def main() -> int:\n"
    "    total = 0\n"
    "    for i in range(5):\n"
    "        total += i\n"
    "    print(total)\n"
    "    return 0\n"
    "\n"
    "main()\n"
)

# Generators exercise coroutine frames, heap-allocated frame state, and
# suspend/resume — runtime paths distinct from classes/arithmetic.
GEN_PROGRAM = (
    "def squares(n: int):\n"
    "    for i in range(n):\n"
    "        yield i * i\n"
    "\n"
    "def fib_gen(n: int):\n"
    "    a = 0\n"
    "    b = 1\n"
    "    count = 0\n"
    "    while count < n:\n"
    "        yield a\n"
    "        a, b = b, a + b\n"
    "        count += 1\n"
    "\n"
    "def main() -> int:\n"
    "    total = 0\n"
    "    for s in squares(6):\n"
    "        total += s\n"
    "    fibs = []\n"
    "    for f in fib_gen(8):\n"
    "        fibs.append(f)\n"
    "    print('sq_total=' + str(total), fibs)\n"
    "    return 0\n"
    "\n"
    "main()\n"
)

PCC_IDENTIFIER = b"pcc-linked"

# A program exercising classes, exception handling, dict/list/string ops and
# integer division — many more runtime paths than the simple loop above, so
# the pcc linker is checked on real object allocation, not just arithmetic.
RICH_PROGRAM = (
    "class Point:\n"
    "    def __init__(self, x: int, y: int) -> None:\n"
    "        self.x = x\n"
    "        self.y = y\n"
    "    def dist_sq(self) -> int:\n"
    "        return self.x * self.x + self.y * self.y\n"
    "\n"
    "def risky(v: int) -> int:\n"
    "    try:\n"
    "        if v < 0:\n"
    "            raise ValueError('neg')\n"
    "        return 100 // v\n"
    "    except ValueError:\n"
    "        return -1\n"
    "    except ZeroDivisionError:\n"
    "        return -2\n"
    "\n"
    "def main() -> int:\n"
    "    p = Point(3, 4)\n"
    "    d = {'a': 1, 'b': 2}\n"
    "    xs = [risky(-1), risky(0), risky(5)]\n"
    "    s = 'pt=' + str(p.dist_sq()) + ' ' + str(d['a'] + d['b'])\n"
    "    print(s, xs)\n"
    "    return 0\n"
    "\n"
    "main()\n"
)


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


REPO = _repo_root()


def _assert_pcc_linked_artifact(path: Path) -> None:
    params = parse_signature(path.read_bytes())
    assert params.identifier == PCC_IDENTIFIER, (
        "default/pcc self-link returned success without a pcc-owned signature; "
        f"found CodeDirectory identifier {params.identifier!r}"
    )


def _build(tmp_path: Path, tag: str, env_extra: dict, program: str = PROGRAM):
    src = tmp_path / f"prog_{tag}.py"
    src.write_text(program, encoding="utf-8")
    out = tmp_path / f"bin_{tag}"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Route selection is controlled only by env_extra. Inheriting
    # PCC_SELF_LINK from pytest would make the explicit cc oracle and default
    # candidate use the same linker — the historical false-positive class.
    env.pop("PCC_SELF_LINK", None)
    env["PCC_DISABLE_PY_RUN_CACHE"] = "1"
    env.update(env_extra)
    run = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(out)],
        capture_output=True, text=True, timeout=560, env=env, cwd=str(REPO),
    )
    return run, out


def test_the_darwin_arm64_default_route_is_pcc_owned(tmp_path):
    run, out = _build(tmp_path, "default", {})
    assert run.returncode == 0, run.stderr[-3000:]
    _assert_pcc_linked_artifact(out)
    ran = subprocess.run([str(out)], capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, (ran.returncode, ran.stderr[-500:])
    assert ran.stdout.strip() == "10", (ran.stdout, ran.stderr)


def test_explicit_cc_oracle_remains_available(tmp_path):
    run, out = _build(tmp_path, "cc", {"PCC_SELF_LINK": "cc"})
    assert run.returncode == 0, run.stderr[-3000:]
    ran = subprocess.run([str(out)], capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, (ran.returncode, ran.stderr[-500:])
    assert ran.stdout.strip() == "10", (ran.stdout, ran.stderr)
    assert PCC_IDENTIFIER not in out.read_bytes(), (
        "PCC_SELF_LINK=cc produced a pcc-linker artifact instead of the "
        "explicit cc/ld oracle"
    )


def test_the_default_route_links_a_working_real_program(tmp_path):
    """The default pcc route links a real Python program into an executable.

    Compiled through the full runtime (which uses GC, thread-locals, GOT
    imports, __bss, and thousands of in-image data pointers), linked entirely
    by pcc's own Mach-O toolchain, and required to produce the SAME output as
    the cc/ld route — running standalone under ASLR (the rebases must be
    right, or a slid load reads absolute pointers at the wrong address).
    """
    run, out = _build(tmp_path, "pcc", {})
    assert run.returncode == 0, (
        "pcc route failed to link a real program:\n" + run.stderr[-3000:]
    )
    assert out.exists()
    _assert_pcc_linked_artifact(out)

    cc_run, cc_out = _build(
        tmp_path, "cc", {"PCC_SELF_LINK": "cc"}
    )
    assert cc_run.returncode == 0, cc_run.stderr[-3000:]
    expected = subprocess.run(
        [str(cc_out)], capture_output=True, text=True, timeout=60
    )
    assert expected.returncode == 0, expected.stderr[-500:]
    ours = subprocess.run(
        [str(out)], capture_output=True, text=True, timeout=60
    )
    assert ours.returncode == 0, (
        f"pcc-linked binary did not exit cleanly (rc={ours.returncode}); "
        "a slid load likely read an in-image pointer at the wrong address "
        f"(missing/wrong rebase). stderr={ours.stderr[-500:]}"
    )
    assert ours.stdout == expected.stdout, (ours.stdout, expected.stdout)


def test_the_default_route_matches_cc_across_aslr_launches(tmp_path):
    """Repeated default launches must match the explicit cc oracle."""
    run, out = _build(tmp_path, "pcc_aslr", {})
    assert run.returncode == 0, run.stderr[-2000:]
    _assert_pcc_linked_artifact(out)
    cc_run, cc_out = _build(
        tmp_path, "cc_aslr", {"PCC_SELF_LINK": "cc"}
    )
    assert cc_run.returncode == 0, cc_run.stderr[-2000:]
    expected = subprocess.run(
        [str(cc_out)], capture_output=True, text=True, timeout=60
    )
    assert expected.returncode == 0, expected.stderr[-500:]
    for _ in range(5):
        r = subprocess.run([str(out)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (r.returncode, r.stderr[-300:])
        assert r.stdout == expected.stdout, (r.stdout, expected.stdout)


def test_the_stage1_closure_stays_free_of_the_macho_toolchain():
    """Keep the host-only Mach-O toolchain behind the subprocess boundary."""
    import importlib.util as imputil
    import sys as _sys

    _sys.path.insert(0, str(REPO))
    _sys.path.insert(0, str(REPO / "scripts"))
    spec = imputil.spec_from_file_location(
        "_probe_stage1_closure", str(REPO / "scripts" / "probe_stage1_closure.py")
    )
    probe = imputil.module_from_spec(spec)
    spec.loader.exec_module(probe)
    srcs, _mods = probe._tightened_closure(str(REPO / "pcc" / "__main__.py"))
    assert srcs, "stage1 closure probe returned no sources"
    offenders = [
        s for s in srcs
        if "macho_" in os.path.basename(s) or "arm64_" in os.path.basename(s)
    ]
    assert not offenders, (
        "the Mach-O toolchain leaked into the stage1 self-host closure "
        "(host-only linker/assembler ownership must stay in the subprocess):\n  "
        + "\n  ".join(offenders)
    )


def test_the_default_route_links_a_class_and_exception_program(tmp_path):
    """Classes, exception handling, dict/list/string ops through pcc's linker.

    Exercises real object allocation (the GC-index path whose PAGEOFF12 bug
    this work fixed), not just arithmetic, and must match the cc route while
    running standalone under ASLR.
    """
    pcc_run, pcc_out = _build(
        tmp_path, "rich_pcc", {}, program=RICH_PROGRAM)
    assert pcc_run.returncode == 0, pcc_run.stderr[-3000:]
    _assert_pcc_linked_artifact(pcc_out)
    cc_run, cc_out = _build(
        tmp_path,
        "rich_cc",
        {"PCC_SELF_LINK": "cc"},
        program=RICH_PROGRAM,
    )
    assert cc_run.returncode == 0, cc_run.stderr[-3000:]

    expected_run = subprocess.run(
        [str(cc_out)], capture_output=True, text=True, timeout=60)
    assert expected_run.returncode == 0, expected_run.stderr[-500:]
    expected = expected_run.stdout
    for _ in range(3):
        r = subprocess.run(
            [str(pcc_out)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (r.returncode, r.stderr[-300:])
        assert r.stdout == expected, (r.stdout, expected)
    assert "pt=25 3" in expected, expected


def test_the_default_route_links_a_generator_program(tmp_path):
    """Generators (coroutine frames, heap frame state, suspend/resume) through
    pcc's linker, matching cc and robust under ASLR."""
    pcc_run, pcc_out = _build(
        tmp_path, "gen_pcc", {}, program=GEN_PROGRAM)
    assert pcc_run.returncode == 0, pcc_run.stderr[-3000:]
    _assert_pcc_linked_artifact(pcc_out)
    cc_run, cc_out = _build(
        tmp_path,
        "gen_cc",
        {"PCC_SELF_LINK": "cc"},
        program=GEN_PROGRAM,
    )
    assert cc_run.returncode == 0, cc_run.stderr[-3000:]
    expected_run = subprocess.run(
        [str(cc_out)], capture_output=True, text=True, timeout=60)
    assert expected_run.returncode == 0, expected_run.stderr[-500:]
    expected = expected_run.stdout
    for _ in range(3):
        r = subprocess.run(
            [str(pcc_out)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (r.returncode, r.stderr[-300:])
        assert r.stdout == expected, (r.stdout, expected)
    assert "sq_total=55" in expected, expected
