import os
import sys
import subprocess
import tempfile

this_dir = os.path.dirname(os.path.abspath(__file__))
# tests/{c,python}/<file>.py -> repo root is two levels up. This used to
# rely on tests/conftest.py's global Path.resolve/dirname shim.
parent_dir = os.path.dirname(os.path.dirname(this_dir))
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.parse.c_parser import CParser
from pcc.codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text


def _generate_ir(source):
    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)
    return postprocess_ir_text(str(cg.module))


def _compile_and_run(source):
    with tempfile.TemporaryDirectory(prefix="pcc_float_semantics_") as tmpdir:
        ir_path = os.path.join(tmpdir, "float_semantics.ll")
        obj_path = os.path.join(tmpdir, "float_semantics.o")
        bin_path = os.path.join(tmpdir, "float_semantics_bin")

        with open(ir_path, "w") as f:
            f.write(_generate_ir(source))

        r = subprocess.run(
            ["cc", "-c", "-w", ir_path, "-o", obj_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            ["cc", obj_path, "-o", bin_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        return subprocess.run([bin_path], capture_output=True, text=True, timeout=30)


def test_float_size_matches_native_abi():
    source = r"""
        int main() {
            return (sizeof(float) == 4 && sizeof(double) == 8) ? 0 : 1;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr


def test_nan_comparisons_follow_c_semantics():
    source = r"""
        int main() {
            double zero = 0.0;
            double nan = zero / zero;
            if (!(nan != nan))
                return 1;
            if (nan == nan)
                return 2;
            if (!nan)
                return 3;
            return 0;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr


def test_huge_val_macros_match_infinity():
    source = r"""
        #include <math.h>

        int main() {
            double inf = 1.0 / 0.0;
            float inf_f = 1.0f / 0.0f;
            if (!(HUGE_VAL == inf))
                return 1;
            if (!(HUGE_VALF == inf_f))
                return 2;
            return 0;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr


def test_float_arithmetic_emits_contract_flags():
    source = r"""
        double mix(double a, double b, double c) {
            return a * b + c;
        }
    """

    ir_text = _generate_ir(source)
    assert "fmul contract double" in ir_text
    assert "fadd contract double" in ir_text


def test_implicit_float_to_unsigned_local_uses_fptoui():
    source = r"""
        unsigned int convert(double x) {
            unsigned int y = x;
            return y;
        }
    """

    ir_text = _generate_ir(source)
    assert "fptoui double" in ir_text
    assert "fptosi double" not in ir_text


def test_implicit_float_to_unsigned_return_uses_fptoui():
    source = r"""
        unsigned int convert(double x) {
            return x;
        }
    """

    ir_text = _generate_ir(source)
    assert "fptoui double" in ir_text
    assert "fptosi double" not in ir_text


def test_implicit_float_to_unsigned_assignment_uses_fptoui():
    source = r"""
        unsigned int convert(double x) {
            unsigned int y;
            y = x;
            return y;
        }
    """

    ir_text = _generate_ir(source)
    assert "fptoui double" in ir_text
    assert "fptosi double" not in ir_text


def test_implicit_float_to_signed_return_still_uses_fptosi():
    source = r"""
        int convert(double x) {
            return x;
        }
    """

    ir_text = _generate_ir(source)
    assert "fptosi double" in ir_text
    assert "fptoui double" not in ir_text
