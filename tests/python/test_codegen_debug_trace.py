from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1
from pcc.parse.py_lift import parse_and_lift
from pcc.py_frontend import type_infer
from pcc.py_frontend.codegen import layer1


REPO = Path(__file__).absolute().parents[2]
_PCC1 = find_current_pcc1(REPO)

_UNSUPPORTED_SOURCE = """\
from pcc.llvm_capi.compat import ir

def f():
    x = ir.Argument(1, 2)
"""


def _find_current_pcc1() -> Path:
    if _PCC1 is None:
        skip_or_fail_no_current_pcc1(
            "no pcc1 binary available for no-host codegen diagnostics gate"
        )
    return _PCC1


def _build_codegen(source: str) -> layer1.L1CodeGen:
    ast_module = parse_and_lift(source, "<test>", "test_module")
    typed_module = type_infer.infer_module(ast_module)
    return layer1.L1CodeGen(typed_module, ir_scaffold_mode="on")


def test_codegen_trace_breadcrumb_and_boundary_context(monkeypatch, capfd):
    monkeypatch.setenv("PCC_DEBUG_CODEGEN_PHASES", "1")
    cg = _build_codegen(_UNSUPPORTED_SOURCE)
    with pytest.raises(Exception):
        cg.generate(cg.ast_module)
    err = capfd.readouterr().err

    assert "PCC_CODEGEN_EXCEPTION type=ScaffoldUnsupportedError" in err
    assert "PCC_CODEGEN_EXCEPTION_CONTEXT" in err
    assert "expr_kind=Call" in err
    assert "stmt_index=0" in err
    assert "stmt_kind=Assign" in err
    assert "PCC_CODEGEN_BREADCRUMB" in err
    assert "module=test_module" in err
    assert "function=f" in err


def test_codegen_trace_disabled_is_quiet(monkeypatch, capfd):
    monkeypatch.delenv("PCC_DEBUG_CODEGEN_PHASES", raising=False)
    cg = _build_codegen(_UNSUPPORTED_SOURCE)
    with pytest.raises(Exception):
        cg.generate(cg.ast_module)
    err = capfd.readouterr().err

    assert "PCC_CODEGEN_EXCEPTION" not in err
    assert "PCC_CODEGEN_BREADCRUMB" not in err


def test_codegen_trace_no_host_pcc1_enabled(tmp_path):
    pcc1 = _find_current_pcc1()
    source = tmp_path / "bad_codegen.py"
    source.write_text(_UNSUPPORTED_SOURCE)
    exe = tmp_path / "bad_codegen.out"
    env = os.environ.copy()
    env["PCC_HOST_PYTHON"] = "/bin/false"
    env["PCC_DEBUG_CODEGEN_PHASES"] = "1"

    proc = subprocess.run(
        [
            str(pcc1),
            str(source),
            "-o",
            str(exe),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180.0,
    )

    assert proc.returncode != 0
    err = proc.stderr
    assert "PCC_CODEGEN_EXCEPTION type=ScaffoldUnsupportedError" in err
    assert "PCC_CODEGEN_EXCEPTION_CONTEXT" in err
    assert "PCC_CODEGEN_BREADCRUMB" in err
    assert "expr_kind=Call" in err
    assert "stmt_index=0" in err
    assert "function=f" in err
