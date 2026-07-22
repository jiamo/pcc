"""Libc import ratchet: pcc1 must not grow new libc/libSystem imports.

The LIBC track replaces pcc's own libc closure with pcc-Python
implementations (translation references pinned in the baseline JSON:
musl primary, llvm-libc for the overlay model and math, apple-libc for
Darwin semantics). This gate is the ratchet: the stage1 binary's undefined
symbols must stay a subset of the recorded baseline. Shrinking is progress
and is recorded by deliberately regenerating the baseline with the smaller
set; growth fails here and must be argued, not slipped in.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from pcc1_gate import find_current_pcc1, repo_root

REPO = repo_root()
BASELINE = REPO / "tests" / "libc_import_baseline.json"

_PLATFORM_REASON = (
    None
    if sys.platform == "darwin"
    else "baseline is recorded for darwin-arm64 mach-o imports"
)


@pytest.mark.pcc_gate(unavailable=_PLATFORM_REASON)
@pytest.mark.pcc_gate(probe="pcc1")
def test_pcc1_libc_imports_stay_within_baseline():
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail(
            "no fresh pcc1 even after auto-provisioning; "
            "run scripts/bootstrap.sh --stage 1 and read its output"
        )
    out = subprocess.run(
        ["nm", "-u", str(pcc1)], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    imports = {
        line.strip()[1:] if line.strip().startswith("_") else line.strip()
        for line in out.stdout.splitlines()
        if line.strip()
    }
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    allowed = set(baseline["symbols"])
    new = sorted(imports - allowed)
    assert not new, (
        "pcc1 grew NEW libc imports not present in the ratchet baseline "
        f"({BASELINE.name}):\n  " + "\n  ".join(new) + "\n"
        "Either implement them in pcc-Python (LIBC track) or argue the "
        "addition and regenerate the baseline deliberately."
    )
    if len(imports) < len(allowed):
        sys.stderr.write(
            f"[libc-ratchet] imports shrank: {len(imports)} < baseline "
            f"{len(allowed)} — tighten {BASELINE.name}\n"
        )
