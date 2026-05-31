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

    with tempfile.TemporaryDirectory(prefix="pcc_param_decay_") as tmpdir:
        ir_path = os.path.join(tmpdir, "param.ll")
        obj_path = os.path.join(tmpdir, "param.o")
        bin_path = os.path.join(tmpdir, "param_bin")

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


def test_array_of_pointer_parameter_decays_to_pointer_to_element():
    source = r"""
        const char *pick(int idx, const char *const opts[]) {
            return opts[idx] ? opts[idx] : "(null)";
        }

        int main() {
            static const char *const catnames[] = {
                "all", "collate", "ctype", "monetary", "numeric", "time", 0
            };
            const char *a = pick(0, catnames);
            const char *b = pick(5, catnames);
            const char *c = pick(6, catnames);
            return (a[0] == 'a' && b[0] == 't' && c[0] == '(') ? 0 : 1;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr


def test_same_width_signed_cast_drops_unsigned_comparison_semantics():
    source = r"""
        #include <stdio.h>

        size_t getend(long pos, size_t len) {
            if (pos > (long)len)
                return len;
            else if (pos >= 0)
                return (size_t)pos;
            else if (pos < -(long)len)
                return 0;
            else
                return len + (size_t)pos + 1;
        }

        int main() {
            return (getend(-20, 9) == 0 && getend(-4, 9) == 6) ? 0 : 1;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr
