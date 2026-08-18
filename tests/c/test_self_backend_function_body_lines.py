from __future__ import annotations

import textwrap

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_parse import (
    _iter_function_defs,
    parse_self_backend_module,
)


_TWO_FUNCTION_IR = textwrap.dedent(
    """
    target triple = "arm64-apple-macosx13.0.0"

    define i64 @first(
        i64 %value
    ) {
    entry:

      ; retained comment line
      %next = add i64 %value, 1
      ret i64 %next
    }

    define void @second() {
    entry:
      ret void
    }
    """
)


def test_function_definition_scan_preserves_multiline_body_text() -> None:
    definitions = _iter_function_defs(_TWO_FUNCTION_IR)

    assert len(definitions) == 2
    first_header, first_body = definitions[0]
    second_header, second_body = definitions[1]
    assert first_header.startswith("define i64 @first(")
    assert second_header == "define void @second() {"
    assert isinstance(first_body, str)
    assert isinstance(second_body, str)
    assert first_body.splitlines() == [
        "entry:",
        "",
        "  ; retained comment line",
        "  %next = add i64 %value, 1",
        "  ret i64 %next",
    ]
    assert second_body.splitlines() == ["entry:", "  ret void"]


def test_function_body_handoff_filters_empty_and_comment_lines() -> None:
    module = parse_self_backend_module(_TWO_FUNCTION_IR)
    first = module.functions[0]
    kernel = first.indexed_kernel

    assert kernel.block_names == ["entry"]
    assert kernel.block_fact(0).second == 1


def test_transferred_body_lines_preserve_parser_output() -> None:
    module = parse_self_backend_module(_TWO_FUNCTION_IR)

    assert [function.name for function in module.functions] == ["first", "second"]
    assert [len(function.indexed_kernel.block_names) for function in module.functions] == [
        1,
        1,
    ]


def test_function_definition_scan_keeps_unterminated_diagnostic() -> None:
    with pytest.raises(BackendUnavailable, match="unterminated function body"):
        _iter_function_defs("define void @broken() {\nentry:\n  ret void\n")
