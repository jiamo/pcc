from __future__ import annotations


def test_c_varargs_split_rewrites_and_reports():
    from pcc.codegen.c_varargs import build_report, postprocess_varargs_ir

    ir = '''declare i32 @"__pcc_va_arg_1"(ptr %ap)
define i32 @f(ptr %ap) {
entry:
  %x = call i32 @"__pcc_va_arg_1"(ptr %ap)
  ret i32 %x
}
'''
    rewrites = []
    out = postprocess_varargs_ir(ir, report=rewrites)
    assert "__pcc_va_arg" not in out
    assert "%x = va_arg ptr %ap, i32" in out
    report = build_report(rewrites).to_json()
    assert report["count"] == 1
    assert report["rewrites"][0]["result_type"] == "i32"


def test_c_codegen_exposes_postprocess_report():
    from pcc.codegen.c_codegen import postprocess_ir_text_with_report

    ir = '''declare i64 @__pcc_va_arg_2(ptr %ap)
define i64 @f(ptr %ap) {
entry:
  %x = call i64 @__pcc_va_arg_2(ptr %ap)
  ret i64 %x
}
'''
    out, report = postprocess_ir_text_with_report(ir)
    assert "va_arg ptr %ap, i64" in out
    assert report.to_json()["count"] == 1
