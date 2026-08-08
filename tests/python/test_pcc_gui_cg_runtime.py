from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_coregraphics_framework_cache_is_defined_in_its_runtime_object(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    source = REPO_ROOT / "pcc" / "py_runtime" / "py" / "pcc_gui_cg.py"
    output = tmp_path / "pcc_gui_cg.ll"
    compile_python(
        str(source),
        str(output),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
    )

    llvm_ir = output.read_text(encoding="utf-8")
    assert "@pcc_gui_cg_framework = global ptr null" in llvm_ir
    assert "load ptr, ptr @pcc_gui_cg_framework" in llvm_ir
    assert "store ptr" in llvm_ir and "ptr @pcc_gui_cg_framework" in llvm_ir
