from __future__ import annotations

import subprocess
import textwrap


def test_owned_object_locals_are_registered_as_gc_frame_roots(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "owned_root.py"
    out = tmp_path / "owned_root.ll"
    src.write_text(textwrap.dedent("""
        def make_value() -> int:
            xs = []
            ys = [xs]
            return len(ys)

        print(make_value())
        """).lstrip())

    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    ir_text = out.read_text(encoding="utf-8")

    assert "call void @pcc_gc_release" in ir_text
    assert ".pcc.gc.frame.map.1" in ir_text
    assert "gc.frame.map = alloca" not in ir_text
    assert "call void @pcc_gc_frame_enter" in ir_text
    assert "call void @pcc_gc_frame_leave" in ir_text


def test_owned_object_roots_are_left_on_function_error_exit(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "owned_root_err_exit.py"
    out = tmp_path / "owned_root_err_exit.ll"
    src.write_text(textwrap.dedent("""
        def callee() -> int:
            return 1

        def make_value() -> int:
            xs = []
            return callee()

        print(make_value())
        """).lstrip())

    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    ir_text = out.read_text(encoding="utf-8")

    err_pos = ir_text.find("err.exit:")
    assert err_pos >= 0, ir_text
    next_block = ir_text.find("\n\n", err_pos)
    err_block = ir_text[err_pos:] if next_block < 0 else ir_text[err_pos:next_block]
    assert "call void @pcc_gc_frame_leave" in err_block


def test_incremental_collect_preserves_live_owned_object_local(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "owned_root_runtime.py"
    exe = tmp_path / "owned_root_runtime.out"
    src.write_text(textwrap.dedent("""
        from pcc.extern import extern, c_int32, c_int64

        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)

        def live_collect() -> int:
            xs = [41, 1]
            pcc_gc_collect(0)
            return xs[0] + xs[1]

        def main() -> None:
            pcc_gc_set_backend(1)
            print(live_collect())

        if __name__ == "__main__":
            main()
        """).lstrip())

    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"
