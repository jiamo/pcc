"""Parametrised tests covering all simple-shape IRBuilder methods
implemented through ``_IR_SCAFFOLD_SIMPLE_METHODS`` (Phase 3 Tasks 9–14).

Each method is verified for the same three properties:
- ON mode emits ``call <ret> @user_pcc_llvm_capi_ir_IRBuilder_<method>(<receiver>, ...)``
  with the right number of pointer args.
- ON mode function body has ZERO ``py_cpy_*`` calls.
- OFF mode still routes through ``py_cpy_*`` (regression guard).

Methods that need bespoke handlers (``call``, ``gep``, ``phi``,
``landingpad``) live in their own files and are not tested here.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)
_PTR = r"(?:ptr|i8\s*\*)"


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text()


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


def _gen_program(method: str, arg_count: int) -> str:
    """Build a minimal user-source program that calls ``builder.<method>``
    with ``arg_count`` positional args (all DynType local variables)."""
    args = ", ".join([f"a{i}" for i in range(arg_count)])
    params = "builder" + ("" if arg_count == 0 else ", " + args)
    return textwrap.dedent(
        f"""
        def use_method({params}):
            return builder.{method}({args})
        """
    )


# Subset of _IR_SCAFFOLD_SIMPLE_METHODS used for the parametrised
# verification. Mirrors the upstream table; if you add a new simple
# method there, add it here too.
_PARAM_METHODS = [
    # void-returning
    ("store", 2, "void"),
    ("ret_void", 0, "void"),
    ("unreachable", 0, "void"),
    ("branch", 1, "void"),
    ("position_at_end", 1, "void"),
    ("position_at_start", 1, "void"),
    ("position_before", 1, "void"),
    ("ret", 1, "void"),
    ("cbranch", 3, "void"),
    ("resume", 1, "void"),
    ("fence", 1, "void"),
    ("add_incoming", 2, "void"),
    # ptr-returning — loads/casts/arithmetic
    ("load", 1, "ptr"),
    ("alloca", 1, "ptr"),
    ("bitcast", 2, "ptr"),
    ("inttoptr", 2, "ptr"),
    ("ptrtoint", 2, "ptr"),
    ("sext", 2, "ptr"),
    ("zext", 2, "ptr"),
    ("trunc", 2, "ptr"),
    ("sitofp", 2, "ptr"),
    ("uitofp", 2, "ptr"),
    ("fpext", 2, "ptr"),
    ("fptosi", 2, "ptr"),
    ("fptoui", 2, "ptr"),
    ("fptrunc", 2, "ptr"),
    ("add", 2, "ptr"),
    ("sub", 2, "ptr"),
    ("mul", 2, "ptr"),
    ("sdiv", 2, "ptr"),
    ("srem", 2, "ptr"),
    ("and_", 2, "ptr"),
    ("or_", 2, "ptr"),
    ("xor", 2, "ptr"),
    ("ashr", 2, "ptr"),
    ("lshr", 2, "ptr"),
    ("fadd", 2, "ptr"),
    ("fsub", 2, "ptr"),
    ("fmul", 2, "ptr"),
    ("fdiv", 2, "ptr"),
    ("frem", 2, "ptr"),
    ("not_", 1, "ptr"),
    ("neg", 1, "ptr"),
    ("fneg", 1, "ptr"),
    ("select", 3, "ptr"),
    ("extract_value", 2, "ptr"),
    ("insert_value", 3, "ptr"),
    ("icmp_signed", 3, "ptr"),
    ("icmp_unsigned", 3, "ptr"),
    ("fcmp_ordered", 3, "ptr"),
    ("fcmp_unordered", 3, "ptr"),
    # append_basic_block is now bespoke (variable arity); skip simple-table test
    ("atomic_rmw", 3, "ptr"),
    ("cmpxchg", 3, "ptr"),
    ("invoke", 3, "ptr"),
]


@pytest.mark.parametrize("method,arg_count,ret", _PARAM_METHODS)
def test_simple_method_on_emits_extern(method, arg_count, ret):
    program = _gen_program(method, arg_count)
    ir_text = _compile_to_ll(program, f"sm_{method}_on", mode="on")
    sym = f"@user_pcc_llvm_capi_ir_IRBuilder_{method}"
    assert sym in ir_text, (
        f"ON mode must emit {sym}; got:\n" + ir_text
    )
    ret_pat = "void" if ret == "void" else _PTR
    expect_argcount = arg_count + 1
    arg_pat = ",".join([rf"\s*{_PTR}\s*"] * expect_argcount)
    decl = re.compile(
        rf"declare[^\n]+{ret_pat}\s+{re.escape(sym)}\s*\(\s*"
        + arg_pat.lstrip(",") + r"\s*\)"
    )
    assert decl.search(ir_text), (
        f"declaration for {sym} must take {expect_argcount} ptr args:\n"
        + ir_text
    )


@pytest.mark.parametrize("method,arg_count,_ret", _PARAM_METHODS)
def test_simple_method_on_body_has_no_py_cpy(method, arg_count, _ret):
    program = _gen_program(method, arg_count)
    ir_text = _compile_to_ll(program, f"sm_{method}_clean", mode="on")
    body = _function_body(ir_text, "use_method")
    assert body is not None, ir_text
    assert f"@user_pcc_llvm_capi_ir_IRBuilder_{method}" in body, body
    assert "py_cpy_" not in body, (
        f"ON mode use_method body for {method} must have ZERO "
        f"py_cpy_*; got:\n" + body
    )


@pytest.mark.parametrize("method,arg_count,_ret", _PARAM_METHODS)
def test_simple_method_off_routes_to_py_cpy(method, arg_count, _ret):
    program = _gen_program(method, arg_count)
    ir_text = _compile_to_ll(program, f"sm_{method}_off", mode="off")
    assert f"@user_pcc_llvm_capi_ir_IRBuilder_{method}" not in ir_text, (
        f"OFF mode must NOT emit scaffold extern for {method}"
    )
    body = _function_body(ir_text, "use_method")
    assert body is not None, ir_text
    assert "py_cpy_" in body, (
        f"OFF mode use_method body for {method} must keep py_cpy_*; "
        f"got:\n" + body
    )
