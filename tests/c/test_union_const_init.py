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

    with tempfile.TemporaryDirectory(prefix="pcc_union_init_") as tmpdir:
        ir_path = os.path.join(tmpdir, "union.ll")
        obj_path = os.path.join(tmpdir, "union.o")
        bin_path = os.path.join(tmpdir, "union_bin")

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


def test_static_const_union_scalar_initializer():
    source = r"""
        union U { int i; double d; };
        static const union U u = { 42 };

        int main() {
            return u.i == 42 ? 0 : 1;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr


def test_static_const_union_scalar_initializer_via_evaluator():
    source = r"""
        union U {
            int i;
            unsigned char bytes[4];
        };

        static const union U nativeendian = {1};

        int main() {
            return nativeendian.i == 1 && nativeendian.bytes[0] == 1 ? 0 : 1;
        }
    """

    ret = CEvaluator().evaluate(source, optimize=False)
    assert ret == 0


def test_static_const_union_nested_struct_initializer():
    source = r"""
        typedef union Value {
            void *p;
            long long raw;
            unsigned char ub;
        } Value;

        typedef union Node {
            struct {
                Value value_;
                unsigned char value_tt;
                unsigned char key_tt;
                int next;
                Value key_val;
            } u;
            struct {
                Value value_;
                unsigned char tt_;
            } i_val;
        } Node;

        static const Node dummynode = {
            {{0}, 16, 9, 0, {0}}
        };

        int main() {
            return dummynode.u.value_.p == 0 &&
                   dummynode.u.value_tt == 16 &&
                   dummynode.u.key_tt == 9 &&
                   dummynode.u.next == 0 &&
                   dummynode.u.key_val.p == 0 ? 0 : 1;
        }
    """

    r = _compile_and_run(source)
    assert r.returncode == 0, r.stderr
