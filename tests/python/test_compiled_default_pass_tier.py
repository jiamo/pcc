from __future__ import annotations

import subprocess

from pcc.py_frontend import compiled_default_passes, pipeline


_SCALAR_IR = """\
define i64 @probe(i64 %arg) {
entry:
  %slot = alloca i64, align 8
  store i64 %arg, ptr %slot, align 8
  %value = load i64, ptr %slot, align 8
  ret i64 %value
}
"""

_STRUCT_IR = """\
define i64 @probe(i64 %arg) {
entry:
  %pair = alloca { i64, ptr }, align 8
  %first = getelementptr inbounds { i64, ptr }, ptr %pair, i32 0, i32 0
  store i64 %arg, ptr %first, align 8
  %value = load i64, ptr %first, align 8
  ret i64 %value
}
"""


def test_compiled_default_tier_promotes_straight_line_scalar_slot():
    out = compiled_default_passes.run_compiled_default_tier(
        _SCALAR_IR,
        ["mem2reg", "sroa"],
        strict_no_libpython=False,
    )

    assert "alloca i64" not in out
    assert "store i64" not in out
    assert "load i64" not in out
    assert "ret i64 %arg" in out


def test_compiled_default_tier_dangling_definition_guard_is_fail_closed():
    assert compiled_default_passes._references_removed_definitions(
        ["  ret i64 %loaded\n"], ["slot", "loaded"]
    ) is True
    assert compiled_default_passes._references_removed_definitions(
        ["  ret i64 %argument\n"], ["slot", "loaded"]
    ) is False


def test_compiled_default_tier_splits_bounded_literal_struct():
    out = compiled_default_passes.run_compiled_default_tier(
        _STRUCT_IR,
        ["mem2reg", "sroa"],
        strict_no_libpython=False,
    )

    assert "alloca { i64, ptr }" not in out
    assert "getelementptr" not in out
    assert "ret i64 %arg" in out


def test_compiled_default_tier_leaves_unproved_control_flow_unchanged():
    source = """\
define i64 @probe(i1 %cond, i64 %arg) {
entry:
  %slot = alloca i64
  store i64 %arg, ptr %slot
  br i1 %cond, label %read, label %read
read:
  %value = load i64, ptr %slot
  ret i64 %value
}
"""

    assert compiled_default_passes.run_compiled_default_tier(
        source,
        ["mem2reg", "sroa"],
        strict_no_libpython=False,
    ) == source


def test_compiled_default_tier_rejects_alloca_address_escape():
    source = """\
define ptr @probe() {
entry:
  %slot = alloca ptr
  store ptr %slot, ptr %slot
  %value = load ptr, ptr %slot
  ret ptr %value
}
"""

    assert compiled_default_passes.run_compiled_default_tier(
        source,
        ["mem2reg", "sroa"],
        strict_no_libpython=False,
    ) == source


def test_compiled_default_tier_strict_mode_skips_actual_cpython_call():
    source = (
        "declare ptr @py_cpy_import(ptr)\n\n"
        "define i64 @probe(ptr %name, i64 %arg) {\n"
        "entry:\n"
        "  %slot = alloca i64\n"
        "  store i64 %arg, ptr %slot\n"
        "  %module = call ptr @py_cpy_import(ptr %name)\n"
        "  %value = load i64, ptr %slot\n"
        "  ret i64 %value\n"
        "}\n"
    )

    assert compiled_default_passes.run_compiled_default_tier(
        source,
        ["mem2reg", "sroa"],
        strict_no_libpython=True,
    ) == source


def test_compiled_default_tier_strict_mode_allows_cpython_declaration_only():
    source = "declare ptr @py_cpy_import(ptr)\n\n" + _SCALAR_IR

    out = compiled_default_passes.run_compiled_default_tier(
        source,
        ["mem2reg", "sroa"],
        strict_no_libpython=True,
    )

    assert "declare ptr @py_cpy_import(ptr)" in out
    assert "alloca" not in out


def test_self_default_pass_tier_never_starts_host_subprocess(monkeypatch):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("bounded self pass tier escaped to host Python")

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    monkeypatch.setattr(pipeline.subprocess, "run", unexpected_run)

    out = pipeline._apply_python_ir_pass_pipeline(
        _SCALAR_IR,
        module_name="probe",
        default_raw="default",
        strict_no_libpython=True,
    )

    assert "alloca" not in out
    assert "ret i64 %arg" in out


def test_self_default_pass_batch_preserves_input_order_without_host(monkeypatch):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("bounded self pass batch escaped to host Python")

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    monkeypatch.setattr(pipeline.subprocess, "run", unexpected_run)

    out = pipeline._apply_python_ir_pass_pipeline_many(
        [("first", _SCALAR_IR), ("second", _STRUCT_IR)],
        default_raw="default",
        strict_no_libpython=True,
    )

    assert [name for name, _text in out] == ["first", "second"]
    assert all("alloca" not in text for _name, text in out)


def test_explicit_higher_pass_keeps_bounded_host_boundary(monkeypatch):
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "dce")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    try:
        pipeline._apply_python_ir_pass_pipeline(
            _SCALAR_IR,
            module_name="probe",
            default_raw="default",
        )
    except pipeline.PyPipelineError:
        pass

    assert seen


def _two_pass_reference(ir_text: str) -> str:
    """The historical shape: one full traversal per transform.

    Kept as an executable oracle so the fused single-traversal rewrite in
    ``_rewrite_functions`` cannot drift from ``sroa(mem2reg(module))``.
    """

    def one_pass(text, transform):
        out: list[str] = []
        function_lines: list[str] = []
        in_function = False
        for line in str(text).splitlines(keepends=True):
            if not in_function and line.lstrip().startswith("define "):
                in_function = True
                function_lines = [line]
                continue
            if in_function:
                function_lines.append(line)
                if line.strip() == "}":
                    out.extend(transform(function_lines))
                    function_lines = []
                    in_function = False
                continue
            out.append(line)
        if function_lines:
            out.extend(function_lines)
        return "".join(out)

    once = one_pass(ir_text, compiled_default_passes._mem2reg_function)
    return one_pass(once, compiled_default_passes._sroa_function)


def test_fused_traversal_matches_two_separate_passes():
    multi_function = _SCALAR_IR + "\n" + _STRUCT_IR + "\n" + _SCALAR_IR
    for text in (_SCALAR_IR, _STRUCT_IR, multi_function):
        assert compiled_default_passes._rewrite_functions(text) == (
            _two_pass_reference(text)
        )


def test_fused_traversal_preserves_non_function_and_truncated_text():
    preamble = 'target triple = "arm64-apple-macosx"\n@g = global i64 0\n'
    truncated = "define i64 @half(i64 %a) {\nentry:\n  ret i64 %a\n"
    for text in (preamble, preamble + truncated, ""):
        assert compiled_default_passes._rewrite_functions(text) == (
            _two_pass_reference(text)
        )


def test_rewrite_functions_is_called_directly_not_as_a_value():
    """A transform passed as a value lowers to the dynamic call path.

    ``py_obj_call`` plus a native adapter runs once per ``define``; keeping
    the calls direct is what removed ~65% of a self-hosted pcc1 compile.
    """
    source = compiled_default_passes.__file__
    with open(source, "r", encoding="utf-8") as stream:
        text = stream.read()
    assert "_rewrite_functions(text, _mem2reg_function)" not in text
    assert "_rewrite_functions(current, _sroa_function)" not in text
    assert "_sroa_function(_mem2reg_function(function_lines))" in text
