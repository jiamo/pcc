from __future__ import annotations

import inspect

from llvmlite import binding as llvm
from llvmlite import ir

from pcc.codegen import c_codegen
from pcc.llvm_capi.compat import add_raw_function_attribute
from pcc.parse.c_parser import CParser


def test_postprocess_ir_text_dispatches_only_varargs_rewrite():
    postprocess_source = inspect.getsource(c_codegen.postprocess_ir_text)
    report_source = inspect.getsource(c_codegen.postprocess_ir_text_with_report)

    assert "_postprocess_varargs_ir" in postprocess_source
    assert "_postprocess_varargs_ir" in report_source
    assert "branch_protection" not in postprocess_source
    assert "branch_protection" not in report_source
    assert not hasattr(c_codegen, "_postprocess_aarch64_branch_protection_ir")


def test_aarch64_branch_protection_is_attached_during_c_ir_construction():
    generator = c_codegen.LLVMCodeGenerator()
    generator.module.triple = "arm64-apple-darwin23.6.0"
    generator.generate_code(CParser().parse("int f(int x) { return x + 1; }"))

    raw_ir = str(generator.module)
    assert '"branch-target-enforcement"' in raw_ir
    assert '"sign-return-address"="non-leaf"' in raw_ir
    assert '"sign-return-address-key"="a_key"' in raw_ir
    assert c_codegen.postprocess_ir_text(raw_ir) == raw_ir


def test_llvmlite_target_attribute_path_emits_pac_ret_instructions():
    llvm.initialize_all_targets()
    llvm.initialize_all_asmprinters()

    module = ir.Module(name="branch-protection")
    module.triple = "arm64-apple-macos13"
    i32 = ir.IntType(32)
    signature = ir.FunctionType(i32, [i32])
    callee = ir.Function(module, signature, name="callee")
    function = ir.Function(module, signature, name="caller")
    for attribute in c_codegen._AARCH64_BRANCH_PROTECTION_ATTRS:
        add_raw_function_attribute(function, attribute)
    builder = ir.IRBuilder(function.append_basic_block("entry"))
    builder.ret(builder.call(callee, [function.args[0]]))

    parsed = llvm.parse_assembly(str(module))
    parsed.verify()
    target_machine = llvm.Target.from_triple(module.triple).create_target_machine(
        cpu="apple-m1"
    )
    assembly = target_machine.emit_assembly(parsed)

    assert "paciasp" in assembly
    assert "retaa" in assembly or "autiasp" in assembly
