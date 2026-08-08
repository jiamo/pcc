from __future__ import annotations

import pytest

from pcc.tools.text_filecheck import check_text, parse_check_directives


def test_text_filecheck_enforces_order_next_and_absence():
    check_text(
        "header\nalpha\nbeta\ngamma\ntail\n",
        """
        CHECK: alpha
        CHECK-NEXT: beta
        CHECK-NOT: forbidden
        CHECK: gamma
        CHECK-NEXT: tail
        """,
        label="sample asm",
    )


def test_text_filecheck_not_covers_prefix_between_matches_and_suffix():
    with pytest.raises(AssertionError, match="matched forbidden input line 1"):
        check_text("bad\nanchor\n", "CHECK-NOT: bad\nCHECK: anchor")
    with pytest.raises(AssertionError, match="matched forbidden input line 3"):
        check_text("anchor\nok\nbad\n", "CHECK: anchor\nCHECK-NOT: bad")


def test_text_filecheck_next_reports_the_actual_adjacent_line():
    with pytest.raises(AssertionError, match="required input line 2"):
        check_text(
            "first\nintervening\nsecond\n",
            "CHECK: first\nCHECK-NEXT: second",
        )


def test_text_filecheck_rejects_unknown_empty_and_next_without_anchor():
    with pytest.raises(AssertionError, match="no supported directive"):
        parse_check_directives("CHECK-SAME: value")
    with pytest.raises(AssertionError, match="empty CHECK pattern"):
        parse_check_directives("CHECK:")
    with pytest.raises(AssertionError, match="no preceding CHECK"):
        check_text("value\n", "CHECK-NEXT: value")
