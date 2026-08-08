"""Focused contracts for the structural self-backend LLVM type parser."""

from __future__ import annotations

import ast
import inspect

import pytest

import pcc.backend.self_backend_parse as parser
from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_parse import (
    _tokenize_ir_type,
    extract_leading_type_token,
    parse_ir_type,
    parse_self_backend_module,
    strip_typed_initializer,
)


def _direct_call_names(function) -> set[str]:
    tree = ast.parse(inspect.getsource(function))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_type_surfaces_share_the_token_parser_source_contract() -> None:
    assert "_parse_ir_type_tokens" in _direct_call_names(parser._parse_type)
    assert "_next_ir_type_token" in _direct_call_names(
        parser._parse_ir_type_tokens
    )
    assert "_next_ir_type_token" in _direct_call_names(parser._tokenize_ir_type)
    assert "split_top_level" not in _direct_call_names(parser._parse_type)
    assert "_parse_ir_type_prefix" in _direct_call_names(
        parser._extract_leading_type_token
    )
    assert "_parse_ir_type_list" in _direct_call_names(
        parser._parse_call_signature
    )
    assert "split_top_level" not in _direct_call_names(
        parser._parse_call_signature
    )


def test_ir_type_tokenizer_keeps_quoted_names_and_nesting_structural() -> None:
    tokens = _tokenize_ir_type(
        '{ [2 x { i8, <4 x i16> }], %"pair, quoted"* } trailing'
    )
    spellings = [token[1] for token in tokens]

    assert '%"pair, quoted"' in spellings
    assert spellings[:6] == ["{", "[", "2", "x", "{", "i8"]
    assert spellings[-4:] == ['%"pair, quoted"', "*", "}", "trailing"]
    quoted = next(token for token in tokens if token[1] == '%"pair, quoted"')
    assert quoted[0] == "atom"
    assert quoted[3] - quoted[2] == len('%"pair, quoted"')


def test_parse_ir_type_recurses_through_supported_nested_shapes() -> None:
    i8 = TypeDesc("int", 8)
    i16 = TypeDesc("int", 16)
    pair = TypeDesc(
        "struct",
        fields=(TypeDesc("int", 64), TypeDesc("int", 64)),
    )
    nested = TypeDesc(
        "struct",
        fields=(i8, TypeDesc("array", count=4, elem=i16)),
    )
    expected = TypeDesc(
        "struct",
        fields=(
            TypeDesc("array", count=2, elem=nested),
            TypeDesc("ptr", pointee=pair),
        ),
    )

    assert (
        parse_ir_type("{ [2 x { i8, <4 x i16> }], { i64, i64 }* }")
        == expected
    )


def test_extract_leading_type_uses_structural_parser_boundary() -> None:
    source = (
        "{ [2 x { i8, i16 }], <4 x i32> } "
        "zeroinitializer, align 16"
    )

    type_text, remainder = extract_leading_type_token(source)

    assert type_text == "{ [2 x { i8, i16 }], <4 x i32> }"
    assert remainder == "zeroinitializer, align 16"
    assert parse_ir_type(type_text) == TypeDesc(
        "struct",
        fields=(
            TypeDesc(
                "array",
                count=2,
                elem=TypeDesc(
                    "struct",
                    fields=(TypeDesc("int", 8), TypeDesc("int", 16)),
                ),
            ),
            TypeDesc("array", count=4, elem=TypeDesc("int", 32)),
        ),
    )


def test_leading_type_parser_does_not_materialize_initializer_tokens() -> None:
    source = '[4096 x i8] c"unterminated initializer is value-layer syntax'

    assert extract_leading_type_token(source) == (
        "[4096 x i8]",
        'c"unterminated initializer is value-layer syntax',
    )


def test_typed_initializer_stripping_uses_the_same_nested_type_boundary() -> None:
    initializer = "{ { i64 1, i64 2 }, [i8 3, i8 4] }"
    typed = "{ { i64, i64 }, [2 x i8] } " + initializer

    assert strip_typed_initializer(typed) == initializer
    assert strip_typed_initializer(initializer) == initializer


def test_module_parser_shares_structural_types_across_incident_surfaces() -> None:
    ir_text = r'''
target triple = "arm64-apple-darwin23.6.0"
%unused_opaque = type opaque
%unused_packed = type <{ i8, i32 }>
%pair = type { i64, i64 }
%envelope = type { [2 x %pair], { i8, <4 x i16> } }
@nested = internal global { [2 x { i8, i16 }], <4 x i32> } zeroinitializer, align 16
@vector = internal global <4 x i32> zeroinitializer

define %envelope @identity(%envelope %value) {
entry:
  ret %envelope %value
}

define i64 @caller({ [2 x { i8, i16 }], <4 x i32> } %payload) {
entry:
  %r = call i64 ({ [2 x { i8, i16 }], <4 x i32> }, i64, ...) @consume({ [2 x { i8, i16 }], <4 x i32> } %payload, i64 9)
  ret i64 %r
}
'''.strip()

    module = parse_self_backend_module(ir_text)
    literal = parse_ir_type("{ [2 x { i8, i16 }], <4 x i32> }")
    pair = TypeDesc(
        "struct",
        name="%pair",
        fields=(TypeDesc("int", 64), TypeDesc("int", 64)),
    )
    envelope = TypeDesc(
        "struct",
        name="%envelope",
        fields=(
            TypeDesc("array", count=2, elem=pair),
            TypeDesc(
                "struct",
                fields=(
                    TypeDesc("int", 8),
                    TypeDesc("array", count=4, elem=TypeDesc("int", 16)),
                ),
            ),
        ),
    )

    assert module.globals_[0].type == literal
    assert module.globals_[0].alignment == 16
    assert module.globals_[1].type == TypeDesc(
        "array",
        count=4,
        elem=TypeDesc("int", 32),
    )
    assert module.functions[0].ret_type == envelope
    assert module.functions[0].args[0].type == envelope
    call = module.functions[1].blocks[0].instructions[0]
    assert call.kind == "call"
    assert call.data[4] == ((literal, "payload"), (TypeDesc("int", 64), "9"))
    assert call.data[5:] == (2, True, (0, 0))


def test_call_parser_preserves_exact_pointer_argument_alignments() -> None:
    ir_text = '''
target triple = "arm64-apple-darwin23.6.0"

declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

define void @copy(ptr %dst, ptr %src) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr align 16 %dst, ptr align 8 %src, i64 32, i1 false)
  ret void
}
'''.strip()

    call = parse_self_backend_module(ir_text).functions[0].blocks[0].instructions[0]

    assert call.kind == "call"
    assert call.data[4] == (
        (TypeDesc("ptr", pointee=TypeDesc("void")), "dst"),
        (TypeDesc("ptr", pointee=TypeDesc("void")), "src"),
        (TypeDesc("int", 64), "32"),
        (TypeDesc("int", 1), "0"),
    )
    assert call.data[7] == (16, 8, 0, 0)


def test_call_parser_canonicalizes_boolean_argument_aliases() -> None:
    ir_text = '''
target triple = "arm64-apple-darwin23.6.0"

declare void @consume_bools(i1, i1)

define void @caller() {
entry:
  call void @consume_bools(i1 false, i1 true)
  ret void
}
'''.strip()

    call = parse_self_backend_module(ir_text).functions[0].blocks[0].instructions[0]

    assert call.kind == "call"
    assert call.data[4] == (
        (TypeDesc("int", 1), "0"),
        (TypeDesc("int", 1), "1"),
    )


def test_call_parser_rejects_non_power_of_two_argument_alignment() -> None:
    ir_text = '''
target triple = "arm64-apple-darwin23.6.0"

declare void @sink(ptr)

define void @caller(ptr %value) {
entry:
  call void @sink(ptr align 3 %value)
  ret void
}
'''.strip()

    with pytest.raises(BackendUnavailable, match="invalid alignment 3"):
        parse_self_backend_module(ir_text)


@pytest.mark.parametrize(
    "type_text",
    [
        "{ i64, [2 x i8]",
        "{ i64,, i8 }",
        "[two x i8]",
        "<{ i64, i64 }>",
        "ptr addrspace(1)",
        "i64 (i32)*",
    ],
)
def test_type_parser_rejects_unsupported_or_malformed_shapes(type_text: str) -> None:
    with pytest.raises(BackendUnavailable):
        parse_ir_type(type_text)


@pytest.mark.parametrize(
    "typed_value",
    [
        "ptr addrspace(1) %value",
        "i64 (i32)* %callback",
    ],
)
def test_leading_type_boundary_does_not_hide_unsupported_suffixes(
    typed_value: str,
) -> None:
    with pytest.raises(BackendUnavailable):
        extract_leading_type_token(typed_value)


def test_named_type_parser_rejects_malformed_nested_definition() -> None:
    ir_text = '''
target triple = "arm64-apple-darwin23.6.0"
%broken = type { i64,, { i8, i8 } }

define void @main() {
entry:
  ret void
}
'''.strip()

    with pytest.raises(BackendUnavailable):
        parse_self_backend_module(ir_text)


@pytest.mark.parametrize(
    "definition",
    [
        "opaque",
        "<{ i8, i32 }>",
    ],
)
def test_unsupported_named_type_declarations_fail_closed_when_referenced(
    definition: str,
) -> None:
    ir_text = f'''
target triple = "arm64-apple-darwin23.6.0"
%unsupported = type {definition}

define void @consume(%unsupported %value) {{
entry:
  ret void
}}
'''.strip()

    with pytest.raises(BackendUnavailable):
        parse_self_backend_module(ir_text)
