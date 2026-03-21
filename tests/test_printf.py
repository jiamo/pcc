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
import unittest


def test_printf():
    pcc = CEvaluator()

    ret = pcc.evaluate(
        """
        int main(){
            printf("helloworld");
            return 0;
        }
        """,
        llvmdump=True,
    )
    # printf output goes to native stdout, not capturable by Python
    # Just verify the program compiles and returns 0
    assert ret == 0


def test_stdio_globals_link_and_run():
    source = """
        int main(){
            fwrite("x", 1, 1, stdout);
            fflush(stdout);
            return 0;
        }
        """
    processed = CEvaluator._system_cpp(source, base_dir=parent_dir)
    ast = CParser().parse(processed)
    cg = LLVMCodeGenerator()
    cg.generate_code(ast)

    with tempfile.TemporaryDirectory(prefix="pcc_stdio_") as tmpdir:
        ir_path = os.path.join(tmpdir, "stdio.ll")
        obj_path = os.path.join(tmpdir, "stdio.o")
        bin_path = os.path.join(tmpdir, "stdio_bin")

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
            ["cc", obj_path, "-o", bin_path], capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert r.stdout == "x"


if __name__ == "__main__":
    unittest.main()
