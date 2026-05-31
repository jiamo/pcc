from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).absolute().parents[2]


def test_all_ir_pass_modules_emit_llvm_in_strict_frontend_mode(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    # ``parity.py`` is a *harness* module — it invokes upstream LLVM
    # ``opt`` via ``shutil.which`` + ``subprocess.run`` for parity
    # diagnostics, not an IR pass itself. The strict no-libpython gate
    # is about core IR-pass modules being self-compilable; the parity
    # harness deliberately depends on advanced stdlib functions that
    # pcc-Python has no native lowering for yet. Skip it here.
    SKIP_MODULES = {"parity.py"}

    pass_dir = _REPO_ROOT / "pcc" / "ir_passes"
    failures: list[str] = []
    for src in sorted(pass_dir.glob("*.py")):
        if src.name in SKIP_MODULES:
            continue
        out = tmp_path / f"{src.stem}.ll"
        try:
            compile_python(
                str(src),
                str(out),
                emit_llvm_only=True,
                libpython_mode="off",
                ir_scaffold_mode="on",
            )
        except Exception as exc:
            failures.append(f"{src.name}: {type(exc).__name__}: {exc}")
            continue
        if not out.exists():
            failures.append(f"{src.name}: did not write LLVM IR")

    assert not failures, "\n".join(failures)
