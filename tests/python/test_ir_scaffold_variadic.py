"""Phase 3 Tasks 11/13/15/16: bespoke handlers for variadic IRBuilder
methods.

Covered:
- ``builder.call(fn, [args])`` → ``user_pcc_llvm_capi_ir_IRBuilder_call<N>`` per arity
- ``builder.gep(ptr, [indices])`` → ``user_pcc_llvm_capi_ir_IRBuilder_gep<N>``
- ``builder.phi(ty)`` → ``user_pcc_llvm_capi_ir_IRBuilder_phi(builder, ty)``
- ``builder.landingpad(ty)`` → ``user_pcc_llvm_capi_ir_IRBuilder_landingpad(builder, ty)``

Negative cases:
- Non-literal args list to ``call`` / ``gep`` raises with a clear
  message (per-file migration tells you to inline the list).
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)
_PTR = r"(?:ptr|i8\s*\*)"


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text(encoding="utf-8")


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


# -- call --------------------------------------------------------------

def test_call_zero_args():
    program = textwrap.dedent(
        """
        def f(builder, fn):
            return builder.call(fn, [])
        """
    )
    ir_text = _compile_to_ll(program, "v_call0", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_call0" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body


def test_call_three_args():
    program = textwrap.dedent(
        """
        def f(builder, fn, a, b, c):
            return builder.call(fn, [a, b, c])
        """
    )
    ir_text = _compile_to_ll(program, "v_call3", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_call3" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body


def test_call_eight_args_has_runtime_provider():
    """The largest owned C-ABI shim currently emits eight fixed args."""
    program = textwrap.dedent(
        """
        def f(builder, fn, a, b, c, d, e, f_arg, g, h):
            return builder.call(fn, [a, b, c, d, e, f_arg, g, h])
        """
    )
    ir_text = _compile_to_ll(program, "v_call8", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_call8" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body
    assert "def IRBuilder_call8(" in (
        _REPO_ROOT / "pcc" / "llvm_capi" / "ir.py"
    ).read_text(encoding="utf-8")


def test_call_with_name_kwarg_still_lowers():
    program = textwrap.dedent(
        """
        def f(builder, fn, a):
            return builder.call(fn, [a], name="result")
        """
    )
    ir_text = _compile_to_ll(program, "v_call_named", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_call1" in ir_text


def test_call_dynamic_args_list_uses_dyn_fallback():
    """Non-literal args list lowers via the ``call_dyn`` fallback —
    the dispatch is still static (extern call), but the args list
    construction may still use ``py_cpy_*``.

    This is the per-file migration relief valve: a function with a
    runtime-built args list still gets static dispatch, even though
    full elimination requires inlining the list at the call site.
    """
    program = textwrap.dedent(
        """
        def f(builder, fn, args):
            return builder.call(fn, args)
        """
    )
    ir_text = _compile_to_ll(program, "v_call_dyn", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_call_dyn" in ir_text, (
        "non-literal args must lower via call_dyn fallback:\n"
        + ir_text
    )


# -- gep ---------------------------------------------------------------

def test_gep_two_indices():
    program = textwrap.dedent(
        """
        def f(builder, ptr, i0, i1):
            return builder.gep(ptr, [i0, i1])
        """
    )
    ir_text = _compile_to_ll(program, "v_gep2", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_gep2" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body


def test_gep_zero_indices():
    program = textwrap.dedent(
        """
        def f(builder, ptr):
            return builder.gep(ptr, [])
        """
    )
    ir_text = _compile_to_ll(program, "v_gep0", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_gep0" in ir_text


def test_gep_dynamic_indices_inbounds_uses_exported_helper():
    program = textwrap.dedent(
        """
        def f(builder, ptr, indices):
            return builder.gep(ptr, indices, inbounds=True)
        """
    )
    ir_text = _compile_to_ll(program, "v_gep_dyn_inbounds", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@user_pcc_llvm_capi_ir_IRBuilder_gep_dyn_inbounds" in body, body
    assert "py_cpy_" not in body, body

    helper_source = (_REPO_ROOT / "pcc" / "llvm_capi" / "ir.py").read_text(
        encoding="utf-8"
    )
    assert "def IRBuilder_gep_dyn_inbounds" in helper_source


# -- phi ---------------------------------------------------------------

def test_phi_one_arg():
    program = textwrap.dedent(
        """
        def f(builder, ty):
            return builder.phi(ty)
        """
    )
    ir_text = _compile_to_ll(program, "v_phi", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_phi" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body


def test_switch_lowers_to_irbuilder_scaffold():
    program = textwrap.dedent(
        """
        def f(builder, value, default_block):
            return builder.switch(value, default_block)
        """
    )
    ir_text = _compile_to_ll(program, "v_switch", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@user_pcc_llvm_capi_ir_IRBuilder_switch" in body, body
    assert "py_cpy_" not in body, body


def test_switch_add_case_lowers_to_switchinstr_scaffold():
    program = textwrap.dedent(
        """
        def f(switch_inst, value, target):
            switch_inst.add_case(value, target)
        """
    )
    ir_text = _compile_to_ll(program, "v_switch_add_case", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@user_pcc_llvm_capi_ir_SwitchInstr_add_case" in body, body
    assert "py_cpy_" not in body, body


def test_switch_add_case_constant_i64_uses_i64_scaffold():
    program = textwrap.dedent(
        """
        from pcc.llvm_capi.compat import ir

        _I64 = ir.IntType(64)

        def f(switch_inst, value: int, target):
            switch_inst.add_case(ir.Constant(_I64, value), target)
        """
    )
    ir_text = _compile_to_ll(program, "v_switch_add_case_i64", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert (
        "@user_pcc_llvm_capi_ir_scaffold_SwitchInstr_add_case_i64" in body
    ), body
    assert "py_cpy_" not in body, body


# -- append_basic_block (variable arity, unambiguous receiver) -----

def test_append_basic_block_zero_args_with_builder():
    program = textwrap.dedent(
        """
        def f(builder):
            return builder.append_basic_block()
        """
    )
    ir_text = _compile_to_ll(program, "v_abb_0_b", mode="on")
    assert (
        "@user_pcc_llvm_capi_ir_scaffold_IRBuilder_append_basic_block"
        in ir_text
    )
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body


def test_append_basic_block_with_name_arg():
    program = textwrap.dedent(
        """
        def f(builder, name: str):
            return builder.append_basic_block(name)
        """
    )
    ir_text = _compile_to_ll(program, "v_abb_named_b", mode="on")
    assert (
        "@user_pcc_llvm_capi_ir_scaffold_IRBuilder_append_basic_block"
        in ir_text
    )


def test_append_basic_block_on_function_receiver():
    """Issue 11.B: receiver may be a Function instance, not just a
    builder. The unambiguous-method match catches this."""
    program = textwrap.dedent(
        """
        def f(some_function):
            return some_function.append_basic_block()
        """
    )
    ir_text = _compile_to_ll(program, "v_abb_func_recv", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert (
        "@user_pcc_llvm_capi_ir_scaffold_Function_append_basic_block"
        in body
    ), body
    assert "py_cpy_" not in body, body


def test_add_incoming_on_phi_receiver():
    program = textwrap.dedent(
        """
        def f(phi, val, blk):
            phi.add_incoming(val, blk)
        """
    )
    ir_text = _compile_to_ll(program, "v_addinc_phi", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@user_pcc_llvm_capi_ir_IRBuilder_add_incoming" in body, body
    assert "py_cpy_" not in body, body


def test_as_pointer_on_type_receiver():
    program = textwrap.dedent(
        """
        def f(ty):
            return ty.as_pointer()
        """
    )
    ir_text = _compile_to_ll(program, "v_aspt", mode="on")
    body = _function_body(ir_text, "f")
    assert body is not None
    assert "@user_pcc_llvm_capi_ir_IRBuilder_as_pointer" in body, body
    assert "py_cpy_" not in body, body


# -- landingpad --------------------------------------------------------

def test_landingpad_one_arg():
    program = textwrap.dedent(
        """
        def f(builder, ty):
            return builder.landingpad(ty)
        """
    )
    ir_text = _compile_to_ll(program, "v_landingpad", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_landingpad" in ir_text
    body = _function_body(ir_text, "f")
    assert body is not None and "py_cpy_" not in body
