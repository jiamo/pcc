"""Real pcc1 DLPack/Metal ownership parity across all five GC backends."""

from __future__ import annotations

import pytest

from tests.gpu_hardware.test_metal_dlpack_pcc1 import (
    run_pcc1_dlpack_capsule_gate,
)


pytestmark = pytest.mark.pcc_gate(env="PCC_CURRENT_PCC1")


def test_pcc1_dlpack_real_metal_device_lifetime_under_gc0_through_gc4(
    tmp_path,
    monkeypatch,
):
    for backend in range(5):
        backend_dir = tmp_path / f"gc{backend}"
        backend_dir.mkdir()
        monkeypatch.setenv("PCC_GC_BACKEND", str(backend))
        run_pcc1_dlpack_capsule_gate(backend_dir)

