import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit


def _compile_units(source: str, tmp_path):
    unit = TranslationUnit(
        name="main.c",
        path=str(tmp_path / "main.c"),
        source=source,
    )
    ev = CEvaluator(backend="self", allow_unimplemented_backend=True)
    compiled_units = ev.compile_translation_units(
        [unit],
        use_system_cpp=False,
        frontend_opt_level=0,
    )
    return ev, compiled_units


def _assemble_and_run(asm_path, tmp_path):
    exe_path = tmp_path / "a.out"
    subprocess.run(
        ["cc", str(asm_path), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run([str(exe_path)], capture_output=True, text=True)


def _link_object_and_run(obj_path, tmp_path):
    exe_path = tmp_path / "obj.out"
    subprocess.run(
        ["cc", str(obj_path), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run([str(exe_path)], capture_output=True, text=True)


def _compile_native_helper(source: str, obj_path):
    src_path = obj_path.with_suffix(".c")
    src_path.write_text(source)
    subprocess.run(
        ["cc", "-target", "arm64-apple-macos", "-c", str(src_path), "-o", str(obj_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_self_backend_emits_aarch64_darwin_asm_for_const_return(tmp_path):
    ev, compiled_units = _compile_units(
        "int main(void) { return 42; }\n",
        tmp_path,
    )
    asm_path = tmp_path / "main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_main:" in asm_text
    assert "movz w0, #42" in asm_text
    assert "ret" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 42


def test_self_backend_emits_direct_call_subset(tmp_path):
    source = (
        "int helper(void) { return 5; }\n"
        "int main(void) { return helper(); }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "call_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_helper:" in asm_text
    assert "_main:" in asm_text
    assert "bl _helper" in asm_text
    assert "stp x29, x30, [sp, #-16]!" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 5


def test_self_backend_emits_i32_arg_arithmetic_and_call(tmp_path):
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main(void) { return add(2, 3); }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "add_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_add:" in asm_text
    assert "add w11, w9, w10" in asm_text
    assert "bl _add" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 5


def test_self_backend_emits_branch_phi_subset(tmp_path):
    source = (
        "int max2(int a, int b) { if (a < b) return b; return a; }\n"
        "int main(void) { return max2(2, 7); }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "max2_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "cset w11, lt" in asm_text
    assert "cbz w9" in asm_text
    assert "bl _max2" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 7


def test_self_backend_emits_object_file(tmp_path):
    ev, compiled_units = _compile_units(
        "int main(void) { return 11; }\n",
        tmp_path,
    )
    obj_path = tmp_path / "main.o"

    ev.emit_compiled_units(compiled_units, emit_obj=str(obj_path), optimize=0)

    assert obj_path.is_file()
    run = _link_object_and_run(obj_path, tmp_path)
    assert run.returncode == 11


def test_self_backend_can_execute_via_evaluate(tmp_path):
    source = (
        "int max2(int a, int b) { if (a < b) return b; return a; }\n"
        "int main(void) { return max2(3, 9) - 9; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_i64_call_and_casts(tmp_path):
    source = (
        "long addl(long a, long b) { return a + b; }\n"
        "int main(void) { return (int)(addl(40, 2) - 42); }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_void_call_and_pointer_store(tmp_path):
    source = (
        "void setp(int *p) { *p = 9; }\n"
        "int main(void) { int x = 0; setp(&x); return x - 9; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_pointer_return_and_load(tmp_path):
    source = (
        "int *id(int *p) { return p; }\n"
        "int main(void) { int x = 3; return *id(&x) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_simple_global_scalar(tmp_path):
    source = "int g = 7; int main(void) { return g - 7; }\n"

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_local_array_indexing(tmp_path):
    source = "int main(void) { int a[2]; a[1] = 4; return a[1] - 4; }\n"

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_simple_struct_field(tmp_path):
    source = "struct S { int x; }; int main(void) { struct S s; s.x = 3; return s.x - 3; }\n"

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_local_string_literal(tmp_path):
    source = 'int main(void) { char *s = "hi"; return s[0] - 104; }\n'

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_global_string_pointer_init(tmp_path):
    source = 'char *s = "hi"; int main(void) { return s[1] - 105; }\n'

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_double_call_and_int_casts(tmp_path):
    source = (
        "double sum2(int a, int b) { return (double)a + (double)b; }\n"
        "int main(void) { return (int)sum2(1, 3) - 4; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_float_call_and_int_casts(tmp_path):
    source = (
        "float sum2f(int a, int b) { return (float)a + (float)b; }\n"
        "int main(void) { return (int)sum2f(1, 3) - 4; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_double_compare(tmp_path):
    source = (
        "int gt2(int a, int b) { double da = (double)a; double db = (double)b; return da > db; }\n"
        "int main(void) { return gt2(5, 2) - 1; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_small_struct_argument_by_value(tmp_path):
    source = (
        "struct Pair { int a; int b; };\n"
        "int sum(struct Pair p) { return p.a + p.b; }\n"
        "int main(void) { struct Pair p; p.a = 1; p.b = 2; return sum(p) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_small_struct_return_by_value(tmp_path):
    source = (
        "struct Pair { int a; int b; };\n"
        "struct Pair mk(void) { struct Pair p; p.a = 1; p.b = 2; return p; }\n"
        "int main(void) { struct Pair p = mk(); return p.a + p.b - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_single_word_struct_return_by_value(tmp_path):
    source = (
        "struct One { int a; };\n"
        "struct One mk(void) { struct One p; p.a = 7; return p; }\n"
        "int main(void) { struct One p = mk(); return p.a - 7; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_two_register_struct_argument_by_value(tmp_path):
    source = (
        "struct Pair64 { long a; long b; };\n"
        "int sum(struct Pair64 p) { return (int)(p.a + p.b); }\n"
        "int main(void) { struct Pair64 p; p.a = 1; p.b = 2; return sum(p) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_two_register_struct_return_by_value(tmp_path):
    source = (
        "struct Quad { int a; int b; int c; int d; };\n"
        "struct Quad mk(void) { struct Quad p; p.a = 1; p.b = 2; p.c = 3; p.d = 4; return p; }\n"
        "int main(void) { struct Quad p = mk(); return p.a + p.b + p.c + p.d - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_partial_tail_two_register_struct_argument_by_value(tmp_path):
    source = (
        "struct Triple { int a; int b; int c; };\n"
        "int sum(struct Triple p) { return p.a + p.b + p.c; }\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return sum(p) - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_partial_tail_two_register_struct_return_by_value(tmp_path):
    source = (
        "struct Triple { int a; int b; int c; };\n"
        "struct Triple mk(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return p; }\n"
        "int main(void) { struct Triple p = mk(); return p.a + p.b + p.c - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_external_mixed_aggregate_and_scalar_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return sum_plus(p, 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_mixed_aggregate_and_fp_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_fp.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "double sum_plus_fp(struct Triple p, double extra) { return (double)(p.a + p.b + p.c) + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern double sum_plus_fp(struct Triple p, double extra);\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return (int)sum_plus_fp(p, 4.0) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_two_aggregate_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_two_agg.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_two(struct Triple a, struct Triple b) { return a.a + a.b + a.c + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_two(struct Triple a, struct Triple b);\n"
        "int main(void) { struct Triple a; struct Triple b; a.a = 1; a.b = 2; a.c = 3; b.a = 4; b.b = 5; b.c = 6; return sum_two(a, b) - 21; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_aggregate_then_pointer_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_agg_ptr.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_ptr(struct Triple a, int *p) { return a.a + a.b + a.c + *p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_ptr(struct Triple a, int *p);\n"
        "int main(void) { struct Triple a; int x = 4; a.a = 1; a.b = 2; a.c = 3; return sum_ptr(a, &x) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_partial_tail_aggregate_return_boundary(tmp_path):
    helper_obj = tmp_path / "helper_ret_triple.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "int main(void) { struct Triple p = mk_triple(); return p.a + p.b + p.c - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_two_register_aggregate_return_boundary(tmp_path):
    helper_obj = tmp_path / "helper_ret_pair.o"
    _compile_native_helper(
        (
            "struct Pair64 { long a; long b; };\n"
            "struct Pair64 mk_pair(void) { struct Pair64 p = {1, 2}; return p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Pair64 { long a; long b; };\n"
        "extern struct Pair64 mk_pair(void);\n"
        "int main(void) { struct Pair64 p = mk_pair(); return (int)(p.a + p.b) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_heterogeneous_aggregate_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_pair_triple.o"
    _compile_native_helper(
        (
            "struct Pair64 { long a; long b; };\n"
            "struct Triple { int a; int b; int c; };\n"
            "int sum_pair_triple(struct Pair64 a, struct Triple b) { return (int)(a.a + a.b) + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Pair64 { long a; long b; };\n"
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_pair_triple(struct Pair64 a, struct Triple b);\n"
        "int main(void) { struct Pair64 a; struct Triple b; a.a = 1; a.b = 2; b.a = 3; b.b = 4; b.c = 5; return sum_pair_triple(a, b) - 15; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_aggregate_return_to_external_call_chain(tmp_path):
    helper_obj = tmp_path / "helper_chain.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { struct Triple p = mk_triple(); return sum_plus(p, 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_external_call(tmp_path):
    helper_obj = tmp_path / "helper_nested_same.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { return sum_plus(mk_triple(), 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_mixed_shape_aggregate_transition(tmp_path):
    helper_obj = tmp_path / "helper_nested_hetero.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Pair64 { long a; long b; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "struct Pair64 mk_pair(void) { struct Pair64 p = {4, 5}; return p; }\n"
            "int sum_pair_triple(struct Pair64 a, struct Triple b) { return (int)(a.a + a.b) + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "struct Pair64 { long a; long b; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern struct Pair64 mk_pair(void);\n"
        "extern int sum_pair_triple(struct Pair64 a, struct Triple b);\n"
        "int main(void) { return sum_pair_triple(mk_pair(), mk_triple()) - 15; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_fp_boundary(tmp_path):
    helper_obj = tmp_path / "helper_nested_fp.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "double sum_plus_fp(struct Triple p, double extra) { return (double)(p.a + p.b + p.c) + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern double sum_plus_fp(struct Triple p, double extra);\n"
        "int main(void) { return (int)sum_plus_fp(mk_triple(), 4.0) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_pointer_boundary(tmp_path):
    helper_obj = tmp_path / "helper_nested_ptr.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_ptr(struct Triple p, int *x) { return p.a + p.b + p.c + *x; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_ptr(struct Triple p, int *x);\n"
        "int main(void) { int x = 4; return sum_ptr(mk_triple(), &x) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_rejects_unsupported_ir_shapes(tmp_path):
    source = (
        "struct Blob { char a[11]; };\n"
        "struct Blob mk(void) { struct Blob b; return b; }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)

    with pytest.raises(BackendUnavailable, match="aggregate register ABI currently only supports aggregate sizes"):
        ev.emit_compiled_units(compiled_units, emit_asm=str(tmp_path / "bad.s"), optimize=0)
