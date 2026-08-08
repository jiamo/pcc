"""Contract: `pcc.api.build` and the `pcc` CLI must emit the same object.

The two are different entry points onto the same pipeline — `api.build` calls
`_compile_translation_units` + `emit_compiled_units` in process, the CLI does
the same through `execute_cli` — but nothing pinned that they stay in step.
BUG-P1-API-VS-CLI-CODEGEN-DIVERGENCE recorded an api-built strtod corpus
producing wrong doubles while the CLI-built one was correct, so the in-process
path stopped being usable as evidence for what the runtime Makefile ships.

The source below exercises that failure class: doubles crossing helper calls,
a struct returned by value, and an internal static. Byte-identical objects are
a stronger contract than matching output, and they are what the recorded bug
would have broken.
"""

import hashlib
import os
import subprocess
import sys

import pytest

this_dir = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(this_dir))
sys.path.insert(0, REPO)

from pcc.api import build

SOURCE = r"""
#include <stdio.h>

double scale(double x, int n) {
    double r = x;
    for (int i = 0; i < n; i++) r *= 1.5;
    return r;
}

struct P { double a; int b; };

static struct P mk(double a, int b) {
    struct P p;
    p.a = a;
    p.b = b;
    return p;
}

int main(void) {
    struct P p = mk(scale(1.0, 5), 7);
    printf("%.17g %d %.17g\n", p.a, p.b, scale(0.1, 3));
    return 0;
}
"""


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cli_env():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


@pytest.mark.parametrize("opt", [0, 2])
def test_api_and_cli_emit_the_same_object(tmp_path, opt):
    src = tmp_path / "parity.c"
    src.write_text(SOURCE, encoding="utf-8")

    artifact = build(
        [str(src)], optimize=opt, kind="object",
        use_compile_cache=False, out_dir=str(tmp_path / f"api{opt}"),
    )
    cli_obj = tmp_path / f"cli{opt}.o"
    run = subprocess.run(
        ["uv", "run", "pcc", f"-O{opt}", "--emit-obj", str(cli_obj), str(src)],
        capture_output=True, text=True, timeout=560, env=_cli_env(), cwd=REPO,
    )
    assert run.returncode == 0, run.stderr[-2000:]

    assert _sha(artifact.output_path) == _sha(str(cli_obj)), (
        "pcc.api.build and the pcc CLI emitted different objects for the same "
        "source; an api-built artifact is then not evidence for what the CLI "
        "(and the runtime Makefile) ships"
    )


def test_api_built_executable_runs_correctly(tmp_path):
    """The object contract is only worth as much as the code being right."""
    src = tmp_path / "parity.c"
    src.write_text(SOURCE, encoding="utf-8")

    artifact = build(
        [str(src)], optimize=2, kind="exe",
        use_compile_cache=False, out_dir=str(tmp_path / "exe"),
    )
    ours = subprocess.run(
        [artifact.output_path], capture_output=True, text=True, timeout=60,
    ).stdout

    oracle_bin = tmp_path / "oracle"
    cc = subprocess.run(
        ["cc", "-O1", "-o", str(oracle_bin), str(src)],
        capture_output=True, text=True, timeout=120, env=_cli_env(),
    )
    assert cc.returncode == 0, cc.stderr
    expected = subprocess.run(
        [str(oracle_bin)], capture_output=True, text=True, timeout=60,
    ).stdout

    assert ours == expected, f"api={ours!r} cc={expected!r}"
