from __future__ import annotations

from pcc.parse import py_parse


def test_match_soft_keyword_can_be_plain_assignment():
    mod = py_parse.parse(
        "match = 1\n"
        "case = match + 2\n",
        filename="<soft-keyword>",
    )

    assert len(mod.body) == 2
    assert all(type(stmt).__name__ == "_Assign" for stmt in mod.body)


def test_match_statement_still_parses_as_compound_statement():
    mod = py_parse.parse(
        "value = 1\n"
        "match value:\n"
        "    case 1:\n"
        "        result = 10\n",
        filename="<match-stmt>",
    )

    assert any(type(stmt).__name__ == "_If" for stmt in mod.body)
