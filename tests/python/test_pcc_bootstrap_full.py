"""Automated full three-stage bootstrap verification.

This test executes the full bootstrap process (scripts/bootstrap.sh --backend self)
and verifies the results, including stage byte-identity.

WARNING: This test is slow as it performs a full bootstrap compilation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Reuse the utilities from the baseline test if possible, 
# but here we are TRIGGERING the build.
from tests.python.test_bootstrap_gate_baseline import (
    _REPO_ROOT,
    _byte_identical_after_normalize,
    _is_macos_arm64,
    _links_libpython
)

def _stage_bin(out_dir: Path, stage: int) -> Path:
    return out_dir / f"pcc{stage}"

def test_full_three_stage_bootstrap_self():
    """Execute and verify a full 3-stage bootstrap with --backend self."""
    if not _is_macos_arm64():
        pytest.skip("Full self-backend bootstrap is currently verified on macOS arm64 only")

    out_dir = _REPO_ROOT / "build" / "bootstrap-pytest-self"
    bootstrap_sh = _REPO_ROOT / "scripts" / "bootstrap.sh"
    
    # Run the bootstrap script
    # We use --backend self and --stage 3 (default)
    cmd = [
        "bash", str(bootstrap_sh),
        "--backend", "self",
        "--out-dir", str(out_dir),
        "--stage", "3"
    ]
    
    print(f"\nRunning: {' '.join(cmd)}")
    
    # Increase timeout to 10 minutes as bootstrap is slow
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600 
    )
    
    # Log output on failure
    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
    
    assert result.returncode == 0, f"Bootstrap script failed with exit code {result.returncode}"
    
    # Verify stages exist
    pcc1 = _stage_bin(out_dir, 1)
    pcc2 = _stage_bin(out_dir, 2)
    pcc3 = _stage_bin(out_dir, 3)
    
    assert pcc1.exists(), "pcc1 missing"
    assert pcc2.exists(), "pcc2 missing"
    assert pcc3.exists(), "pcc3 missing"
    
    # Verify no libpython linkage (Issue 1 criterion)
    assert not _links_libpython(pcc1), "pcc1 links libpython"
    assert not _links_libpython(pcc2), "pcc2 links libpython"
    assert not _links_libpython(pcc3), "pcc3 links libpython"
    
    # Verify byte-identity (The ultimate self-host gate)
    assert _byte_identical_after_normalize(pcc2, pcc3), (
        "Self-host determinism failed: pcc2 and pcc3 are not byte-identical after normalization"
    )
    print("\nBootstrap successful: pcc2 and pcc3 are byte-identical.")

if __name__ == "__main__":
    # Allow running this script directly
    try:
        test_full_three_stage_bootstrap_self()
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)
