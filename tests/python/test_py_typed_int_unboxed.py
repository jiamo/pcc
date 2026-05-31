"""Typed-int user functions should use native scalar lowering when safe."""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


def _compile_to_ll(tmp_path: Path, source: str, name: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / f"{name}.py"
    out = tmp_path / f"{name}.ll"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
    )
    return out.read_text(encoding="utf-8")


def _fn_body(ir_text: str, fn_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


_TYPED_LOOP = textwrap.dedent("""
    def bench(n: int) -> int:
        acc: int = 0
        i: int = 0
        while i < n:
            acc = acc + (i % 7) + (i // 13)
            i = i + 1
        return acc

    print(bench(10000))
    """)


_TYPED_LIST_LOOP = textwrap.dedent("""
    def sum_ints(xs: list[int]) -> int:
        s: int = 0
        for x in xs:
            s = s + x
        return s

    print(sum_ints([1, 2, 3, 4]))
    """)


_TYPED_FLOAT_LOOP = textwrap.dedent("""
    def bench(n: int) -> float:
        acc: float = 0.0
        i: int = 0
        while i < n:
            acc = (acc + 1.0) * 4.0 / 2.0
            i = i + 1
        return acc

    print(bench(6))
    """)


_TYPED_FUNCTION_CALL_LOOP = textwrap.dedent("""
    def bump(x: int) -> int:
        return x + 2

    def step(i: int) -> int:
        return bump(i % 7)

    def bench(n: int) -> int:
        total: int = 0
        i: int = 0
        while i < n:
            total = total + step(i)
            i = i + 1
        return total

    print(bench(2100000))
    """)


def test_typed_int_loop_uses_unboxed_function_abi(tmp_path):
    ir_text = _compile_to_ll(tmp_path, _TYPED_LOOP, "typed_loop_unboxed")
    assert re.search(
        r"define\s+i64\s+@user_[A-Za-z0-9_]*_bench\s*\(i64\s+%n\)",
        ir_text,
    ), ir_text
    body = _fn_body(ir_text, "bench")
    assert body is not None, ir_text
    assert "@py_int_cmp" not in body, body
    assert "@py_int_mod" not in body, body
    assert "@py_int_floordiv" not in body, body
    assert "@py_int_from_i64" not in body, body
    assert "@py_obj_call" not in body, body
    assert "@py_obj_truthy" not in body, body
    assert "srem" in body, body
    assert "sdiv" in body, body
    assert "low.while.cond" in body, body


def test_typed_list_int_loop_keeps_accumulator_unboxed(tmp_path):
    ir_text = _compile_to_ll(tmp_path, _TYPED_LIST_LOOP, "typed_list_int_loop")
    assert re.search(
        r"define\s+i64\s+@user_[A-Za-z0-9_]*_sum_ints\s*\(ptr\s+%xs\)",
        ir_text,
    ), ir_text
    body = _fn_body(ir_text, "sum_ints")
    assert body is not None, ir_text
    assert "@py_list_len" in body, body
    assert "@py_list_get_i64_nonnegative" in body, body
    assert "@py_list_get(" not in body, body
    assert "@py_list_get_i64(" not in body, body
    assert "@py_int_to_i64" not in body, body
    assert "@py_int_add" not in body, body
    assert "@py_obj_getitem" not in body, body
    assert "@py_obj_call" not in body, body
    assert "@py_cpy_" not in body, body


def test_typed_function_call_loop_uses_unboxed_direct_calls(tmp_path):
    ir_text = _compile_to_ll(
        tmp_path,
        _TYPED_FUNCTION_CALL_LOOP,
        "typed_function_call_loop_unboxed",
    )
    for fn_name, arg_name in (
        ("bump", "x"),
        ("step", "i"),
        ("bench", "n"),
    ):
        assert re.search(
            rf"define\s+i64\s+@user_[A-Za-z0-9_]*_{fn_name}\s*\(i64\s+%{arg_name}\)",
            ir_text,
        ), ir_text
    bump_body = _fn_body(ir_text, "bump")
    step_body = _fn_body(ir_text, "step")
    bench_body = _fn_body(ir_text, "bench")
    assert bump_body is not None, ir_text
    assert step_body is not None, ir_text
    assert bench_body is not None, ir_text
    assert re.search(
        r"call\s+i64\s+@user_[A-Za-z0-9_]*_bump\s*\(i64\s+%",
        step_body,
    ), step_body
    assert re.search(
        r"call\s+i64\s+@user_[A-Za-z0-9_]*_step\s*\(i64\s+%",
        bench_body,
    ), bench_body
    for body in (bump_body, step_body, bench_body):
        assert "@py_obj_call" not in body, body
        assert "@py_int_" not in body, body
        assert "@py_cpy_" not in body, body


def test_typed_list_i64_runtime_helpers_match_c_fast_path():
    import pcc

    repo_root = Path(pcc.__file__).resolve().parents[1]
    py_list_src = repo_root / "pcc" / "py_runtime" / "py" / "py_list.py"
    text = py_list_src.read_text(encoding="utf-8")
    for helper in (
        "py_list_get_i64",
        "py_list_get_i64_nonnegative",
        "py_list_len",
    ):
        match = re.search(
            rf'def {helper}\([^)]*\).*?(?=\n\n@c_abi_export|\n\n@c_abi_export|\Z)',
            text,
            re.DOTALL,
        )
        assert match is not None, helper
        body = match.group(0)
        assert "_list_is_sane" not in body, body


def test_typed_list_int_loop_runs_without_libpython(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "typed_list_int_loop_run.py"
    exe = tmp_path / "typed_list_int_loop_run.out"
    src.write_text(_TYPED_LIST_LOOP, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert run.stdout == "10\n"


def test_typed_list_int_loop_falls_back_for_heap_int_elements(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "typed_list_heap_int_loop_run.py"
    exe = tmp_path / "typed_list_heap_int_loop_run.out"
    src.write_text(
        textwrap.dedent("""
        def sum_ints(xs: list[int]) -> int:
            s: int = 0
            for x in xs:
                s = s + x
            return s

        print(sum_ints([4611686018427387904, 1]))
        """),
        encoding="utf-8",
    )
    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert run.stdout == "4611686018427387905\n"


def test_typed_float_loop_uses_unboxed_low_ir_function_abi(tmp_path):
    ir_text = _compile_to_ll(tmp_path, _TYPED_FLOAT_LOOP, "typed_float_loop_unboxed")
    assert re.search(
        r"define\s+double\s+@user_[A-Za-z0-9_]*_bench\s*\(i64\s+%n\)",
        ir_text,
    ), ir_text
    body = _fn_body(ir_text, "bench")
    assert body is not None, ir_text
    assert "low.while.cond" in body, body
    assert "fadd" in body, body
    assert "fmul" in body, body
    assert "fdiv" in body, body
    assert "@py_float_" not in body, body
    assert "@py_int_" not in body, body
    assert "@py_obj_call" not in body, body
    assert "@py_obj_truthy" not in body, body
    assert "@py_cpy_" not in body, body


def test_typed_float_loop_low_ir_can_be_disabled_as_layer1_oracle(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_PYTHON_LOW_IR", "off")
    ir_text = _compile_to_ll(tmp_path, _TYPED_FLOAT_LOOP, "typed_float_loop_legacy")
    body = _fn_body(ir_text, "bench")
    assert body is not None, ir_text
    assert "low.while.cond" not in body, body
    assert "fadd" in body, body
    assert "@py_float_" not in body, body


def test_typed_float_unboxed_loop_runs_without_libpython(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "typed_float_loop_run.py"
    exe = tmp_path / "typed_float_loop_run.out"
    src.write_text(_TYPED_FLOAT_LOOP, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    expected = 0.0
    for _i in range(6):
        expected = (expected + 1.0) * 4.0 / 2.0
    assert run.stdout == f"{expected}\n"


def test_typed_int_unboxed_abi_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_TYPED_INT_ABI", "off")
    ir_text = _compile_to_ll(tmp_path, _TYPED_LOOP, "typed_loop_boxed")
    assert re.search(
        r"define\s+ptr\s+@user_[A-Za-z0-9_]*_bench\s*\(ptr\s+%n\)",
        ir_text,
    ), ir_text
    body = _fn_body(ir_text, "bench")
    assert body is not None, ir_text
    assert "@py_int_cmp" in body, body
    assert "@py_int_mod" in body, body
    assert "@py_int_floordiv" in body, body


def test_typed_int_low_ir_can_be_disabled_as_layer1_oracle(tmp_path, monkeypatch):
    low_ir_text = _compile_to_ll(tmp_path, _TYPED_LOOP, "typed_loop_low_ir")
    low_body = _fn_body(low_ir_text, "bench")
    assert low_body is not None, low_ir_text
    assert "low.while.cond" in low_body, low_body

    monkeypatch.setenv("PCC_PYTHON_LOW_IR", "off")
    legacy_text = _compile_to_ll(tmp_path, _TYPED_LOOP, "typed_loop_legacy")
    legacy_body = _fn_body(legacy_text, "bench")
    assert legacy_body is not None, legacy_text
    assert "low.while.cond" not in legacy_body, legacy_body
    assert "@py_int_mod" not in legacy_body, legacy_body
    assert "@py_int_floordiv" not in legacy_body, legacy_body
    assert "srem" in legacy_body, legacy_body
    assert "sdiv" in legacy_body, legacy_body


def test_typed_int_unboxed_loop_runs_without_libpython(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "typed_loop_run.py"
    exe = tmp_path / "typed_loop_run.out"
    src.write_text(_TYPED_LOOP, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        backend="self",
    )
    expected = str(sum((i % 7) + (i // 13) for i in range(10000))) + "\n"
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert run.stdout == expected


_TYPED_DIRECT_CALL = textwrap.dedent("""
    def add(a: int, b: int) -> int:
        return a + b

    def bench(n: int) -> int:
        return add(n, 1)

    print(bench(41))
    """)


def test_typed_int_direct_call_uses_low_ir_and_runs_without_libpython(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    ir_text = _compile_to_ll(tmp_path, _TYPED_DIRECT_CALL, "typed_direct_call")
    body = _fn_body(ir_text, "bench")
    assert body is not None, ir_text
    assert "low.call" in body, body
    assert re.search(r"call\s+i64\s+@user_[A-Za-z0-9_]*_add", body), body
    assert "@py_int_add" not in body, body

    src = tmp_path / "typed_direct_call.py"
    exe = tmp_path / "typed_direct_call.out"
    src.write_text(_TYPED_DIRECT_CALL, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert run.stdout == "42\n"


def test_typed_int_abi_decision_stays_off_runtime_cache():
    root = Path(__file__).absolute().parents[2]
    source = (root / "pcc/py_frontend/codegen/typed_int_abi.py").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"\n    def _funcdef_uses_unboxed_typed_int_abi"
        r"\(self, fd: FuncDef\).*?"
        r"\n    def _typed_int_func_for_name",
        source,
        re.DOTALL,
    )
    assert match is not None
    method_source = match.group(0)
    assert "_unboxed_typed_int_abi_cache" not in method_source
    assert "dict.get" not in method_source
