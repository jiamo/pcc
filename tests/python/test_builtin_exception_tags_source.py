from pathlib import Path
import re


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file() and (parent / "pcc").is_dir():
            return parent
    raise RuntimeError(f"cannot locate pcc repo root from {here}")


_REPO_ROOT = _find_repo_root()
_CODEGEN_DIR = _REPO_ROOT / "pcc" / "py_frontend" / "codegen"


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def test_builtin_exception_tag_metadata_has_one_authoritative_source():
    codegen_files = sorted(_CODEGEN_DIR.glob("*.py"))
    tag_defs = []
    for path in codegen_files:
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^_?BUILTIN_EXC_TAG\s*=\s*\{", source):
            tag_defs.append(str(path.relative_to(_REPO_ROOT)))

    assert tag_defs == ["pcc/py_frontend/codegen/builtin_exceptions.py"]

    for rel in (
        "pcc/py_frontend/codegen/call_expression_lowering.py",
        "pcc/py_frontend/codegen/class_gen.py",
        "pcc/py_frontend/codegen/comprehension_lowering.py",
        "pcc/py_frontend/codegen/exception_lowering.py",
        "pcc/py_frontend/codegen/for_loop_lowering.py",
        "pcc/py_frontend/codegen/isinstance_lowering.py",
    ):
        source = _read(rel)
        assert "from .builtin_exceptions import" in source


def test_builtin_exception_tag_lookup_covers_runtime_tags():
    from pcc.py_frontend.codegen.builtin_exceptions import (
        BUILTIN_EXC_TAG,
        builtin_exc_tag_or_missing,
    )

    assert BUILTIN_EXC_TAG["BaseException"] == 0
    assert BUILTIN_EXC_TAG["StopIteration"] == 8
    assert BUILTIN_EXC_TAG["StopAsyncIteration"] == 17
    assert BUILTIN_EXC_TAG["ReferenceError"] == 18
    assert BUILTIN_EXC_TAG["MemoryError"] == 19
    assert BUILTIN_EXC_TAG["ImportError"] == 20
    assert BUILTIN_EXC_TAG["ModuleNotFoundError"] == 21
    assert builtin_exc_tag_or_missing("FileNotFoundError") == BUILTIN_EXC_TAG["OSError"]
    assert builtin_exc_tag_or_missing("NotABuiltinException") == -1


def test_class_base_exception_lookup_uses_shared_tags():
    from pcc.py_frontend.codegen.class_gen import _builtin_exception_tag_for_base_name
    from pcc.py_frontend.codegen.builtin_exceptions import BUILTIN_EXC_TAG

    assert (
        _builtin_exception_tag_for_base_name("Exception")
        == BUILTIN_EXC_TAG["Exception"]
    )
    assert (
        _builtin_exception_tag_for_base_name("FileNotFoundError")
        == BUILTIN_EXC_TAG["OSError"]
    )
    assert _builtin_exception_tag_for_base_name("NotABuiltinException") is None


def test_memory_error_runtime_tables_match_c_and_pcc_python():
    c_header = _read("pcc/py_runtime/include/py_runtime.h")
    c_substrate = _read("pcc/py_runtime/src/py_substrate.c")
    py_substrate = _read("pcc/py_runtime/py/py_substrate.py")
    py_gc = _read("pcc/py_runtime/py/py_gc_backend.py")

    assert "PY_EXC_MEMORYERROR       = 19" in c_header
    assert "PY_EXC_IMPORTERROR       = 20" in c_header
    assert "PY_EXC_MODULENOTFOUNDERROR = 21" in c_header
    assert "PY_EXC_N_BUILTIN         = 22" in c_header
    assert '"MemoryError",' in c_substrate
    assert "[PY_EXC_MEMORYERROR]       = PY_EXC_EXCEPTION" in c_substrate
    assert 'define_global_cstr("PY_EXC_NAME_19", "MemoryError")' in py_substrate
    assert 'define_global_null_ptr_array("py_exc_classes", 22)' in py_substrate
    assert "def py_subs_exc_n_builtin() -> int:\n    return 22" in py_substrate
    assert "def _py_visit_builtin_exception_cache_slots" in py_gc
    assert "_py_visit_mapped_root_slots(\n        22," in py_gc
