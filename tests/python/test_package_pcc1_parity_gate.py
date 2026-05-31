from __future__ import annotations

from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, require_current_pcc1_enabled


REPO = Path(__file__).resolve().parents[2]


def test_current_pcc1_exists_when_package_parity_is_required():
    if not require_current_pcc1_enabled():
        pytest.skip("set PCC_REQUIRE_CURRENT_PCC1=1 in the pcc1 package parity CI job")
    pcc1 = find_current_pcc1(REPO)
    assert pcc1 is not None, (
        "pcc1 package parity was required, but no pcc1 binary newer than "
        "pcc/cli_bootstrap.py was found"
    )
