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


def test_stdarg_pointer_int_double_roundtrip():
    source = r"""
        #include <stdarg.h>

        int check(const char *fmt, ...) {
            va_list ap;
            va_start(ap, fmt);
            char *s = va_arg(ap, char *);
            int i = va_arg(ap, int);
            double d = va_arg(ap, double);
            void *p = va_arg(ap, void *);
            va_end(ap);
            return (s[0] == 'h' && s[1] == 'i' &&
                    i == 42 &&
                    d > 2.4 && d < 2.6 &&
                    p == s) ? 0 : 1;
        }

        int main() {
            char *s = "hi";
            return check("", s, 42, 2.5, s);
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

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

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_stdarg_helper_accepts_va_list_parameter():
    source = r"""
        #include <stdarg.h>

        int collect_from_list(va_list ap) {
            char *s = va_arg(ap, char *);
            int i = va_arg(ap, int);
            double d = va_arg(ap, double);
            void *p = va_arg(ap, void *);
            return (s[0] == 'o' && s[1] == 'k' &&
                    i == 7 &&
                    d > 1.2 && d < 1.3 &&
                    p == s) ? 0 : 1;
        }

        int check(const char *fmt, ...) {
            va_list ap;
            int rc;
            va_start(ap, fmt);
            rc = collect_from_list(ap);
            va_end(ap);
            return rc;
        }

        int main() {
            char *s = "ok";
            return check("", s, 7, 1.25, s);
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

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

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_variadic_string_literal_argument_decays_to_pointer():
    source = r"""
        #include <stdarg.h>

        int check(const char *fmt, ...) {
            va_list ap;
            char *first;
            char *second;
            va_start(ap, fmt);
            first = va_arg(ap, char *);
            second = va_arg(ap, char *);
            va_end(ap);
            return (first[0] == 'o' && first[1] == 'k' &&
                    second[0] == 'x' && second[1] == 'y' &&
                    second[2] == 0) ? 0 : 1;
        }

        int main() {
            char *s = "ok";
            return check("%s%s", s, "xy");
        }
    """

    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_vararg_") as tmpdir:
        ir_path = os.path.join(tmpdir, "vararg.ll")
        obj_path = os.path.join(tmpdir, "vararg.o")
        bin_path = os.path.join(tmpdir, "vararg_bin")

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

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
