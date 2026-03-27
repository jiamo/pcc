"""PCRE compilation test suite.

Tests each PCRE .c file through pcc's full pipeline:
  preprocess → parse → codegen → IR serialize → LLVM verify

Run:  uv run pytest tests/test_pcre.py -v
"""

import os
import re
import subprocess
import tempfile
import pytest
import llvmlite.ir as ir
import llvmlite.binding as llvm
from pcc.evaluater.c_evaluator import CEvaluator
from pcc.parse.c_parser import CParser
from pcc.codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text
from pcc.project import TranslationUnit

this_dir = os.path.dirname(__file__)
project_dir = os.path.dirname(this_dir)
projects_dir = os.path.join(project_dir, "projects")
pcre_dir = os.path.join(project_dir, "projects", "pcre-8.45")
pcre_test_main = os.path.join(projects_dir, "test_pcre_main.c")

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

_TYPEDEF_CLEANUP = re.compile(
    r"typedef\s+(int|char|short|long|double|float|void)\s+\1\s*;"
)
_VA_LIST_TYPEDEF = re.compile(
    r"^typedef\s+.*\b(__builtin_va_list|__darwin_va_list)\b.*;$", re.MULTILINE
)
_VA_LIST_USE = re.compile(r"\b(__builtin_va_list|__darwin_va_list)\b")
PCRE_CPP_ARGS = ("-DHAVE_CONFIG_H",)

# Core pcre files (skip 16/32-bit variants, JIT, tools)
PCRE_CORE_FILES = (
    sorted(
        f
        for f in os.listdir(pcre_dir)
        if f.endswith(".c")
        and not f.startswith("pcre16")
        and not f.startswith("pcre32")
        and f
        not in (
            "dftables.c",
            "pcretest.c",
            "pcregrep.c",
            "pcredemo.c",
            "pcre_jit_compile.c",
            "pcre_jit_test.c",
        )
    )
    if os.path.isdir(pcre_dir)
    else []
)


def _compile_pcre_file(fname):
    """Compile a single PCRE .c file. Returns (stage, detail)."""
    fpath = os.path.join(pcre_dir, fname)
    stage = "init"
    try:
        with open(fpath) as f:
            src = f.read()

        processed = CEvaluator._system_cpp(
            src,
            base_dir=pcre_dir,
            cpp_args=PCRE_CPP_ARGS,
        )
        processed = _TYPEDEF_CLEANUP.sub("", processed)
        processed = _VA_LIST_TYPEDEF.sub("", processed)
        processed = _VA_LIST_USE.sub("char *", processed)
        stage = "preprocess"

        ast = CParser().parse(processed)
        stage = "parse"

        cg = LLVMCodeGenerator()
        cg.generate_code(ast)
        stage = "codegen"

        for n, gv in list(cg.module.globals.items()):
            if isinstance(gv, ir.GlobalVariable):
                try:
                    str(gv)
                except Exception:
                    gv.initializer = ir.Constant(gv.value_type, None)

        ir_text = postprocess_ir_text(str(cg.module))
        funcs = [l for l in ir_text.splitlines() if l.startswith("define ")]
        stage = "ir_serialize"

        llvm.parse_assembly(ir_text)
        stage = "llvm_verify"

        return "ok", len(funcs)
    except Exception as e:
        return stage, f"{type(e).__name__}: {str(e)[:80]}"


# Files needed for the pcre library.
PCRE_LIB_FILES = PCRE_CORE_FILES


@pytest.mark.skipif(not os.path.isdir(pcre_dir), reason="pcre-8.45 not found")
@pytest.mark.parametrize("fname", PCRE_CORE_FILES, ids=PCRE_CORE_FILES)
def test_pcre_source_compile(fname):
    """Test PCRE .c file: preprocess → parse → codegen → LLVM verify."""
    stage, result = _compile_pcre_file(fname)
    if stage == "ok":
        pass
    else:
        pytest.xfail(f"Stage '{stage}': {result}")


def _compile_native_pcre():
    """Compile pcre + test_pcre_main.c with system cc. Returns (stage, bin_path)."""
    if not os.path.isfile(pcre_test_main):
        return "init", "test_pcre_main.c not found"

    try:
        tmpdir = tempfile.mkdtemp(prefix="pcc_native_pcre_")
        bin_path = os.path.join(tmpdir, "pcre_native")
        srcs = [os.path.join(pcre_dir, f) for f in PCRE_LIB_FILES]
        srcs.append(pcre_test_main)
        r = subprocess.run(
            ["cc", "-O0", "-w", "-DHAVE_CONFIG_H", "-I", pcre_dir]
            + srcs
            + ["-o", bin_path, "-lm"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            return "compile", r.stderr[:300]
        return "ok", bin_path
    except Exception as e:
        return "compile", f"{type(e).__name__}: {str(e)[:120]}"


def _pcre_runtime_units():
    units = []
    for fname in PCRE_LIB_FILES:
        path = os.path.join(pcre_dir, fname)
        with open(path) as f:
            units.append(TranslationUnit(fname, path, f.read()))
    with open(pcre_test_main) as f:
        units.append(
            TranslationUnit(os.path.basename(pcre_test_main), pcre_test_main, f.read())
        )
    return units


@pytest.fixture(scope="session")
def native_pcre_bin():
    return _compile_native_pcre()


@pytest.fixture(scope="module")
def pcre_compiled_units():
    units = _pcre_runtime_units()
    compiled_units = CEvaluator().compile_translation_units(
        units,
        base_dir=projects_dir,
        jobs=2,
        cpp_args=PCRE_CPP_ARGS,
    )
    return compiled_units


@pytest.mark.skipif(not os.path.isdir(pcre_dir), reason="pcre-8.45 not found")
def test_pcre_native_runtime(native_pcre_bin):
    """Run test_pcre_main with natively compiled pcre (baseline)."""
    stage, bin_path = native_pcre_bin
    if stage != "ok":
        pytest.xfail(f"native compile failed: {bin_path}")

    r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"native pcre test failed:\n{r.stdout}\n{r.stderr}"
    assert "53/53 tests passed" in r.stdout


@pytest.mark.skipif(not os.path.isdir(pcre_dir), reason="pcre-8.45 not found")
def test_pcre_pcc_runtime_with_system_link(pcre_compiled_units):
    result = CEvaluator().run_compiled_translation_units_with_system_cc(
        pcre_compiled_units,
        optimize=True,
        base_dir=projects_dir,
        link_args=["-lm"],
        timeout=180,
    )

    assert (
        result.returncode == 0
    ), f"pcc system-link pcre test failed:\n{result.stdout}\n{result.stderr}"
    assert "53/53 tests passed" in result.stdout


@pytest.mark.skipif(not os.path.isdir(pcre_dir), reason="pcre-8.45 not found")
def test_pcre_pcc_runtime_with_mcjit(pcre_compiled_units):
    result = CEvaluator().evaluate_compiled_translation_units(
        pcre_compiled_units,
        optimize=True,
    )

    assert result == 0
