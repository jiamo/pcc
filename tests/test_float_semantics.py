import os
import sys
import subprocess
import tempfile

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.parse.c_parser import CParser
from pcc.codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text


def _compile_and_run(source):
    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_float_semantics_") as tmpdir:
        ir_path = os.path.join(tmpdir, "float_semantics.ll")
        obj_path = os.path.join(tmpdir, "float_semantics.o")
        bin_path = os.path.join(tmpdir, "float_semantics_bin")

        with open(ir_path, "w") as f:
            f.write(postprocess_ir_text(str(cg.module)))

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
