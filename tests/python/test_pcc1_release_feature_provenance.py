"""Release feature gates reject stale compiler evidence before execution."""

import json

import pytest

from tests.host_pcc_pcc1_parity import pcc1_receipt_path, write_pcc1_receipt
from tests.integration import test_pcc1_release_features as gates


@pytest.fixture
def release_candidate(tmp_path, monkeypatch):
    compiler = tmp_path / "pcc1"
    compiler.write_text("#!/bin/sh\nexit 0\n")
    compiler.chmod(0o755)
    monkeypatch.setenv("PCC1_BINARY", str(compiler))
    monkeypatch.delenv("PCC1_RECEIPT", raising=False)
    monkeypatch.setattr(gates, "self_host_source_key", lambda: "current-source")
    monkeypatch.setattr(
        gates, "self_backend_object_cache_key", lambda: "current-emitter"
    )
    return compiler


def receipt_for(compiler, receipt):
    return write_pcc1_receipt(
        compiler,
        receipt,
        source_key="current-source",
        object_cache_identity="current-emitter",
    )


@pytest.mark.parametrize("fault", ["missing", "source", "emitter", "binary"])
def test_release_gate_rejects_unbound_compiler_before_launch(
    release_candidate, tmp_path, monkeypatch, fault
):
    compiler = release_candidate
    receipt = pcc1_receipt_path(compiler)
    if fault != "missing":
        payload = receipt_for(compiler, receipt)
        if fault == "binary":
            compiler.write_text("#!/bin/sh\nexit 7\n")
        else:
            key = "source_key" if fault == "source" else "object_cache_identity"
            payload[key] = "old-build"
            receipt.write_text(json.dumps(payload))

    def unexpected_launch(*args, **kwargs):
        pytest.fail("release gate launched a process before checking compiler evidence")

    monkeypatch.setattr(gates.subprocess, "run", unexpected_launch)
    with pytest.raises(
        pytest.fail.Exception, match="fresh release pcc1 is not receipt-verified"
    ):
        gates.compile_and_run(tmp_path / "feature.py", compiler)


def test_release_gate_accepts_matching_current_receipt(release_candidate):
    compiler = release_candidate
    receipt_for(compiler, pcc1_receipt_path(compiler))
    gates.verify_release_compiler(compiler)


def test_release_gate_honors_explicit_receipt_path(
    release_candidate, tmp_path, monkeypatch
):
    compiler = release_candidate
    receipt = tmp_path / "candidate-evidence.json"
    receipt_for(compiler, receipt)
    monkeypatch.setenv("PCC1_RECEIPT", str(receipt))
    gates.verify_release_compiler(compiler)
