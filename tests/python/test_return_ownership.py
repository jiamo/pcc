from __future__ import annotations

import os
import re
import subprocess
import textwrap


def test_returning_borrowed_parameter_retains_for_owned_call_result(tmp_path):
    """A Python function call returns a new reference to its result.

    Returning a parameter does not allocate a new object in the callee, so the
    return lowering must retain it before the caller stores and later releases
    the call result as an owned local.
    """
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "return_borrowed_param.py"
    src.write_text(
        textwrap.dedent(
            """
            def identity(xs: list) -> list:
                return xs

            def main() -> None:
                xs = [1]
                ys = identity(xs)
                print(len(xs))
                print(len(ys))

            if __name__ == "__main__":
                main()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    ll = tmp_path / "return_borrowed_param.ll"
    compile_python(str(src), str(ll), emit_llvm_only=True, libpython_mode="off")
    ir_text = ll.read_text(encoding="utf-8")
    identity_ir = re.search(
        r"define\s+[^@]*@user_[^(]*identity[^{]*\{.*?\n\}",
        ir_text,
        re.S,
    )
    assert identity_ir is not None
    assert "pcc_gc_retain" in identity_ir.group(0)

    exe = tmp_path / "return_borrowed_param.out"
    compile_python(str(src), str(exe), libpython_mode="off")

    env = os.environ.copy()
    env["PCC_DEBUG_RUNTIME"] = "1"
    proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "1\n1\n"
