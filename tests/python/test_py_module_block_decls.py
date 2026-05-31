from __future__ import annotations

import subprocess
import textwrap

from pcc.py_frontend.pipeline import compile_python


def test_module_scope_if_block_def_compiles_without_libpython(tmp_path):
    src = tmp_path / "block_def.py"
    src.write_text(
        textwrap.dedent(
            """
            if True:
                def value() -> int:
                    return 41

            print(value() + 1)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "block_def.out"

    compile_python(
        str(src),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "42\n"
