from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PY_SOURCE = REPO_ROOT / "benchmarks/python/scenarios/value_array_c_like.py"
C_SOURCE = REPO_ROOT / "benchmarks/c/value_array_c_like.c"
RESULT = REPO_ROOT / "benchmarks/results/m3_value_array_c_like.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict:
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_value_array_c_like_manifest_is_exactly_source_and_ir_bound(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    manifest = _manifest()
    identity = manifest["source_identity"]
    assert re.fullmatch(r"[0-9a-f]{40}", identity["repository_base_commit"])
    assert identity["worktree_dirty"] is True
    assert identity["binding"] == "base commit plus exact content and emitted-IR hashes"
    assert identity["python_sha256"] == _sha256(PY_SOURCE)
    assert identity["native_c_sha256"] == _sha256(C_SOURCE)

    emitted = tmp_path / "value_array_c_like.ll"
    compile_python(
        str(PY_SOURCE),
        str(emitted),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert identity["frontend_ir_sha256"] == _sha256(emitted)


def test_value_array_c_like_manifest_proves_bounded_mode_labeled_claim():
    manifest = _manifest()
    assert manifest["schema"] == "pcc.m3_c_like.value_array.v1"
    policy = manifest["claim"]["measured_policy"]
    modes = manifest["modes"]
    assert {mode["label"] for mode in modes.values()} == {
        "CPython-host",
        "LLVM/no-libpython",
        "self/no-libpython",
        "native-C/clang-O3",
    }
    for key, mode in modes.items():
        assert len(mode["samples_ns"]) == manifest["workload"]["runtime_samples"]
        assert mode["median_ns"] == int(statistics.median(mode["samples_ns"]))
        assert all(sample > 0 for sample in mode["samples_ns"]), key
    assert modes["llvm_no_libpython"]["links_libpython"] is False
    assert modes["self_no_libpython"]["links_libpython"] is False
    assert modes["llvm_no_libpython"]["ratio_vs_native_c"] <= policy[
        "llvm_no_libpython_ratio_vs_native_c_max"
    ]
    assert modes["llvm_no_libpython"]["ratio_vs_cpython"] <= policy[
        "llvm_no_libpython_ratio_vs_cpython_max"
    ]
    assert "no C-like ratio threshold claimed" in policy["self_no_libpython"]


def test_value_array_c_like_manifest_keeps_ir_and_semantic_slow_paths_together():
    manifest = _manifest()
    shape = manifest["ir_shape"]
    assert shape["direct_aggregate_abi"] is True
    assert shape["object_allocation_calls"] == []
    assert shape["slow_path_python_int_add_retained"] is True
    assert shape["instruction_counts"] == {
        "extractvalue": 54,
        "fadd": 16,
        "fmul": 16,
        "fsub": 16,
    }
    assert manifest["correctness"]["host_llvm_self_exact_match"] is True
    assert manifest["correctness"]["native_c_hot_checksum_match"] is True
    assert manifest["correctness"]["slow_path_lines"] == [
        "0.25",
        "index-error",
        "overflow-error",
        "True",
    ]
    allocation = manifest["allocation_probe"]
    assert allocation["mode"] == "self/no-libpython"
    assert allocation["hot_loop_alloc_object_delta"] == 0
    assert allocation["observations"]["0"] == allocation["observations"]["1000"]


def test_value_array_c_like_source_matches_host_llvm_and_self(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    host = subprocess.run(
        [sys.executable, str(PY_SOURCE)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    outputs = [host.stdout]
    for backend in ("llvm", "self"):
        executable = tmp_path / f"value_array_c_like_{backend}"
        compile_python(
            str(PY_SOURCE),
            str(executable),
            libpython_mode="off",
            ir_scaffold_mode="on",
            backend=backend,
        )
        result = subprocess.run(
            [str(executable)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        outputs.append(result.stdout)
    assert outputs == [manifest_output := _manifest()["correctness"]["python_stdout"]] * 3
    assert manifest_output.endswith("index-error\noverflow-error\nTrue\n")
