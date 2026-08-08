from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from pcc.py_frontend.pipeline import compile_python


REPO = Path(__file__).resolve().parents[2]
CONTEXTUAL_PCC_GUI_FIXTURE = (
    REPO / "tests" / "fixtures" / "contextual_pcc_gui_class_method_failure.py"
)


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


def _contextual_pcc_gui_method_source() -> str:
    """Load the immutable, executable full-context failure source."""
    return CONTEXTUAL_PCC_GUI_FIXTURE.read_text(encoding="utf-8")


def _method_argument_provenance_source() -> str:
    return textwrap.dedent(
        """
        from pcc.unsafe import load_i64, stack_alloc, store_i64

        class ProvenanceProbe:
            def record(self, raw, boxed: object, allocating: list[int]) -> None:
                print(boxed)
                store_i64(raw, 8, len(allocating))

            def run(self) -> None:
                raw = stack_alloc(16)
                self.record(raw, 100, [10, 20, 30])
                print(load_i64(raw, 8))

        ProvenanceProbe().run()
        """
    ).lstrip()


def test_full_pcc_gui_context_method_ints_follow_emitted_abi(
    tmp_path,
):
    src = tmp_path / "contextual_pcc_gui_method.py"
    ll = tmp_path / "contextual_pcc_gui_method.ll"
    src.write_text(_contextual_pcc_gui_method_source(), encoding="utf-8")
    compile_python(
        str(src),
        str(ll),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    ir_text = ll.read_text(encoding="utf-8")
    body = _function_body(
        ir_text,
        "ContextualAnimApp_anim_start",
    )
    extern_call = next(
        line for line in body.splitlines() if "@pcc_gui_anim_start" in line
    )

    # The emitted method ABI itself is the scalar projection, so the three
    # business values never become tagged pointers and need no box/unbox
    # round-trip before the extern call.  Passing pointer bits as i64 is the
    # historical 0x4000000000 leak.
    assert body.count("@py_int_to_i64") == 0
    assert "call i32 @pcc_gui_anim_start" in extern_call
    assert re.search(
        r"@pcc_gui_anim_start\([^,]+,\s+i64\s+[^,]+,\s+"
        r"i64\s+[^,]+,\s+i64\s+[^)]+\)",
        extern_call,
    ), extern_call

    caller = _function_body(ir_text, "ContextualAnimApp_exercise")
    method_call = next(
        line
        for line in caller.splitlines()
        if "ContextualAnimApp_anim_start" in line and "call" in line
    )
    assert re.search(
        r"ContextualAnimApp_anim_start\(ptr\s+[^,]+,\s*"
        r"i64\s+[^,]+,\s*i64\s+[^,]+,\s*i64\s+[^)]+\)",
        method_call,
    ), method_call


@pytest.mark.parametrize("backend", ["llvm", "self"])
def test_saved_full_context_class_method_never_exposes_boxed_int_tag(
    backend,
    tmp_path,
    monkeypatch,
    pcc_py_runtime_archive,
):
    src = tmp_path / f"contextual_pcc_gui_method_{backend}.py"
    exe = tmp_path / f"contextual_pcc_gui_method_{backend}.out"
    src.write_text(_contextual_pcc_gui_method_source(), encoding="utf-8")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(pcc_py_runtime_archive))

    compile_python(
        str(src),
        str(exe),
        backend=backend,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "0\n0\n100\n2000\n0\n1\n"


def test_method_argument_provenance_pins_managed_but_not_raw_pointer(tmp_path):
    src = tmp_path / "method_argument_provenance.py"
    ll = tmp_path / "method_argument_provenance.ll"
    src.write_text(_method_argument_provenance_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(ll),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    body = _function_body(ll.read_text(encoding="utf-8"), "ProvenanceProbe_run")
    call_match = re.search(
        r"call\s+(?:void|ptr)\s+@[^(\n]*ProvenanceProbe_record\("
        r"ptr\s+(?P<receiver>%[^, ]+),\s*"
        r"ptr\s+(?P<raw>%[^, ]+),\s*"
        r"ptr\s+(?P<boxed>%[^, ]+),\s*"
        r"ptr\s+(?P<allocating>%[^) ]+)\)",
        body,
    )

    assert call_match is not None, body
    raw = call_match.group("raw")
    for operation in ("pin", "unpin", "release"):
        assert f"@pcc_gc_{operation}(ptr {raw})" not in body
    # Receiver + boxed-int object + allocating list are managed values.  The
    # latter two must stay pinned while subsequent arguments and the call run.
    for group in ("receiver", "boxed", "allocating"):
        managed = call_match.group(group)
        assert f"call void @pcc_gc_pin(ptr {managed})" in body
        # One unpin is the success edge and another is the call-error edge.
        assert body.count(f"call void @pcc_gc_unpin(ptr {managed})") >= 2
    for group in ("boxed", "allocating"):
        owned = call_match.group(group)
        assert body.count(f"call void @pcc_gc_release(ptr {owned})") >= 2
    receiver = call_match.group("receiver")
    assert f"call void @pcc_gc_release(ptr {receiver})" not in body


@pytest.mark.parametrize("backend", ["llvm", "self"])
def test_method_argument_provenance_runs_no_libpython(backend, tmp_path):
    src = tmp_path / f"method_argument_provenance_{backend}.py"
    exe = tmp_path / f"method_argument_provenance_{backend}.out"
    src.write_text(_method_argument_provenance_source(), encoding="utf-8")

    compile_python(
        str(src),
        str(exe),
        backend=backend,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "100\n3\n"
