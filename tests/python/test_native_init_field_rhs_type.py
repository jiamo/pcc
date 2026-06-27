from __future__ import annotations

import re
import subprocess
import textwrap

from pcc.py_frontend.pipeline import compile_python


def _function_body(ir_text: str, fn_name_suffix: str) -> str:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    match = pattern.search(ir_text)
    assert match is not None, ir_text
    return match.group(1)


def _source() -> str:
    return textwrap.dedent(
        """
        from copy import copy

        class Box:
            def __init__(self, xs: list[int]):
                self.xs = copy(xs)

            def first(self) -> int:
                return self.xs[0]

        def main() -> None:
            b = Box([41])
            print(b.first() + 1)

        main()
        """
    ).lstrip()


def test_init_copy_rhs_preserves_field_type_in_ir(tmp_path):
    src = tmp_path / "init_copy_field.py"
    ll = tmp_path / "init_copy_field.ll"
    src.write_text(_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(ll),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    body = _function_body(ll.read_text(encoding="utf-8"), "Box_first")

    assert "@py_list_get" in body
    assert "@py_obj_getitem" not in body


def test_init_copy_rhs_field_type_runs_no_libpython(tmp_path):
    src = tmp_path / "init_copy_field.py"
    exe = tmp_path / "init_copy_field.out"
    src.write_text(_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "42\n"


def _method_arg_source() -> str:
    return textwrap.dedent(
        """
        from copy import copy

        class Core:
            scratch: list[int]

            def __init__(self, scratch: list[int]):
                self.scratch = scratch

        class Machine:
            def __init__(self, cores: list[Core]):
                self.cores = copy(cores)

            def run(self) -> None:
                for core in self.cores:
                    self.step(core)

            def step(self, core) -> None:
                dispatch = {"alu": self.alu}
                dispatch["alu"](core, 1)

            def alu(self, core, index) -> None:
                print(core.scratch[index])

        def main() -> None:
            m = Machine([Core([10, 42])])
            m.run()

        main()
        """
    ).lstrip()


def test_class_self_call_argument_types_reach_literal_dispatch_target(tmp_path):
    src = tmp_path / "class_method_arg_flow.py"
    ll = tmp_path / "class_method_arg_flow.ll"
    src.write_text(_method_arg_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(ll),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    body = _function_body(ll.read_text(encoding="utf-8"), "Machine_alu")

    assert "@py_list_get" in body
    assert "@py_obj_getattr" not in body


def test_class_self_call_argument_types_run_no_libpython(tmp_path):
    src = tmp_path / "class_method_arg_flow.py"
    exe = tmp_path / "class_method_arg_flow.out"
    src.write_text(_method_arg_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "42\n"
