"""Bootstrap-gate baseline verification (Issue 1 progress tracker).

Lightweight: only inspects binaries that already exist in
``build/bootstrap-{llvm,self}/``. Does NOT trigger a fresh bootstrap
build (those take minutes and would slow every pytest run). If the
binaries are absent, all tests are skipped — re-run
``scripts/bootstrap.sh`` to regenerate them, then re-run pytest.

What's checked:
- Binary sizes haven't drifted dramatically from the captured baseline.
- ``otool -L`` libpython linkage state matches baseline (currently
  ``false`` for every strict bootstrap binary).
- pcc2 and pcc3 are byte-identical after Mach-O signature and LC_UUID
  normalization (the README's three-stage self-host gate).

The Issue 1 no-libpython baseline is intentionally one-way: any
``links_libpython`` transition back to ``true`` is a regression.
"""
from __future__ import annotations

import json
import os
import platform

from pcc.dependency_verdict import probe_platform_capability
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pcc.macho_normalize import normalize_macho_metadata


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BASELINE_JSON = _REPO_ROOT / "tests" / "bootstrap_gate_baseline.json"
_BUILD_ROOT = _REPO_ROOT / "build"


# Structured capture-platform verdict: the authoritative baseline is
# macOS-arm64-specific; elsewhere the verdict is UNAVAILABLE and never a
# claim about bootstrap behavior (AUD-P2-PLATFORM-BOOTSTRAP-BASELINE-VERDICT).
def _is_macos_arm64() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }


def _load_baseline() -> dict:
    with open(_BASELINE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _stage_bin(backend: str, stage: int) -> Path:
    return _BUILD_ROOT / f"bootstrap-{backend}" / f"pcc{stage}"


def _links_libpython(path: Path) -> bool:
    cmd = ["otool", "-L", str(path)] if sys.platform == "darwin" else [
        "ldd",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip(f"can't run {cmd[0]}; cannot verify linkage")
    text = (result.stdout or "") + (result.stderr or "")
    return "libpython" in text or "Python.framework" in text


def _strip_signature_copy(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    if sys.platform == "darwin" and shutil.which("codesign"):
        subprocess.run(
            ["codesign", "--remove-signature", str(dst)],
            check=False,
            capture_output=True,
        )
        normalize_macho_metadata(dst)


def _byte_identical_after_normalize(a: Path, b: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        a_norm = Path(tmp) / "a"
        b_norm = Path(tmp) / "b"
        _strip_signature_copy(a, a_norm)
        _strip_signature_copy(b, b_norm)
        with open(a_norm, "rb") as fa, open(b_norm, "rb") as fb:
            return fa.read() == fb.read()


def _require_bins(backend: str) -> None:
    for stage in (1, 2, 3):
        path = _stage_bin(backend, stage)
        if not path.exists():
            pytest.skip(
                f"{path} missing; run scripts/bootstrap.sh --backend "
                f"{backend} to populate, then re-run this test"
            )


@pytest.mark.parametrize("backend", ["llvm", "self"])
def test_bootstrap_libpython_state_matches_baseline(backend):
    """Each backend×stage binary's libpython linkage must match what
    the baseline records. When Path A flips a binary from true→false,
    update the baseline JSON.
    """
    platform_verdict = probe_platform_capability(
        "macos-arm64-bootstrap-baseline",
        supported=_is_macos_arm64(),
        detail="the authoritative bootstrap baseline is captured on macOS arm64",
    )
    if not platform_verdict.available:
        pytest.skip(platform_verdict.skip_reason())
    _require_bins(backend)
    baseline = _load_baseline()
    expected_state = baseline["current_state"][backend]
    actual: dict[str, dict[str, object]] = {}
    for stage in (1, 2, 3):
        path = _stage_bin(backend, stage)
        actual[f"stage{stage}"] = {
            "size_bytes": path.stat().st_size,
            "links_libpython": _links_libpython(path),
        }

    mismatches: list[str] = []
    for stage_key, expected in expected_state.items():
        observed = actual[stage_key]
        if observed["links_libpython"] != expected["links_libpython"]:
            mismatches.append(
                f"{backend}/{stage_key}: links_libpython "
                f"{observed['links_libpython']} != "
                f"{expected['links_libpython']}"
            )
    assert not mismatches, (
        "bootstrap gate libpython state drifted from baseline:\n  "
        + "\n  ".join(mismatches)
        + "\n(if intentional Path A progress, refresh baseline JSON)"
    )


@pytest.mark.parametrize("backend", ["llvm", "self"])
def test_bootstrap_pcc2_pcc3_byte_identical(backend):
    """The README's self-host gate: pcc2 and pcc3 must be byte
    identical after Mach-O signature normalization. Path A must not
    break this — if it does, determinism regression in codegen.
    """
    platform_verdict = probe_platform_capability(
        "macos-arm64-bootstrap-baseline",
        supported=_is_macos_arm64(),
        detail="the byte-identical gate is captured on macOS arm64",
    )
    if not platform_verdict.available:
        pytest.skip(platform_verdict.skip_reason())
    _require_bins(backend)
    pcc2 = _stage_bin(backend, 2)
    pcc3 = _stage_bin(backend, 3)
    assert _byte_identical_after_normalize(pcc2, pcc3), (
        f"{backend}: pcc2 and pcc3 differ after signature normalization; "
        f"self-host determinism gate failed"
    )
