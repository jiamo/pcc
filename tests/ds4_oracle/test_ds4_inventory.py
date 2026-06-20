"""DS4-P0-INVENTORY-ORACLE gate.

This test proves ONLY that the checked-in ds4 inventory/oracle manifest is
present, well-formed, and honestly labelled as inventory-only. It asserts NO
pcc ds4 support: the manifest must declare ``pcc_support_claimed == False`` and
``stage == "inventory-oracle-only"`` so no later reader can mistake this slice
for a compile / runtime / GPU-execution claim.

The core assertions run against the checked-in JSON manifest and DO NOT require
the external ds4 reference tree at ``~/pcc_refs/antirez-ds4-depth1`` to exist,
so this passes in CI. An OPTIONAL cross-check against the live tree is guarded
and skips cleanly when the tree (or its pinned commit) is absent.
"""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path

import pytest

PINNED_COMMIT = "80ebbc396aee40eedc1d829222f3362d10fa4c6c"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("could not locate repo root (AGENTS.md not found walking up)")


REPO_ROOT = _repo_root()
MANIFEST_PATH = REPO_ROOT / "tests" / "ds4_oracle" / "ds4_inventory.json"
GOLDEN_DIR = REPO_ROOT / "tests" / "ds4_oracle" / "golden"
GOLDEN_MANIFEST_PATH = GOLDEN_DIR / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.is_file(), f"missing inventory manifest: {MANIFEST_PATH}"
    with MANIFEST_PATH.open("r", encoding="utf-8") as fp:
        return json.load(fp)


# --------------------------------------------------------------------------
# Claim-boundary assertions (the whole point of this slice).
# --------------------------------------------------------------------------

def test_manifest_declares_no_pcc_support(manifest: dict) -> None:
    assert manifest["pcc_support_claimed"] is False
    assert manifest["stage"] == "inventory-oracle-only"
    assert manifest["task_row"] == "DS4-P0-INVENTORY-ORACLE"
    # The reference note must keep the "reference/oracle only" framing so the
    # manifest cannot be re-read as a support claim later.
    note = manifest["reference"]["note"].lower()
    assert "no pcc ds4 support is claimed" in note
    assert "oracle" in note


def test_pinned_commit(manifest: dict) -> None:
    ref = manifest["reference"]
    assert ref["pinned_commit"] == PINNED_COMMIT
    assert ref["local_path"] == "~/pcc_refs/antirez-ds4-depth1"


# --------------------------------------------------------------------------
# Surface inventory assertions.
# --------------------------------------------------------------------------

EXPECTED_SURFACES = {
    "c_runtime_core",
    "gpu_api",
    "metal_host",
    "metal_kernels",
    "cuda_rocm",
    "gguf_tools",
    "distributed",
}


def _all_inventoried_paths(manifest: dict) -> set[str]:
    paths: set[str] = set()
    for surface in manifest["surfaces"].values():
        for f in surface.get("files", []):
            paths.add(f["path"])
    return paths


def test_expected_surface_categories_present(manifest: dict) -> None:
    surfaces = manifest["surfaces"]
    missing = EXPECTED_SURFACES - set(surfaces)
    assert not missing, f"inventory missing surface categories: {sorted(missing)}"
    # Every surface must carry a role and at least one file entry.
    for name, surface in surfaces.items():
        assert surface.get("role"), f"surface {name} missing role"
        files = surface.get("files", [])
        assert files, f"surface {name} has no files"
        for f in files:
            assert f.get("path"), f"file entry in {name} missing path"
            assert f.get("role"), f"file entry {f.get('path')} missing role"


def test_key_filenames_present(manifest: dict) -> None:
    paths = _all_inventoried_paths(manifest)
    for expected in (
        "ds4.c",
        "ds4.h",
        "ds4_gpu.h",
        "ds4_metal.m",
        "ds4_cuda.cu",
        "ds4_rocm.cu",
        "ds4_distributed.c",
        "ds4_distributed.h",
        "ds4_kvstore.c",
        "ds4_ssd.c",
        "gguf-tools/deepseek4-quantize.c",
    ):
        assert expected in paths, f"key file missing from inventory: {expected}"
    # Metal kernel rows named in the design doc must be inventoried.
    for kernel in ("metal/cpy.metal", "metal/moe.metal", "metal/flash_attn.metal",
                   "metal/norm.metal", "metal/softmax.metal", "metal/dsv4_rope.metal"):
        assert kernel in paths, f"key metal kernel missing: {kernel}"


def test_metal_kernel_count(manifest: dict) -> None:
    kernels = manifest["surfaces"]["metal_kernels"]["files"]
    assert len(kernels) == 19
    assert manifest["surfaces"]["cuda_rocm"]["rocm_cuh_count"] == 22


# --------------------------------------------------------------------------
# GPU API surface.
# --------------------------------------------------------------------------

def test_gpu_api_detail(manifest: dict) -> None:
    detail = manifest["gpu_api_detail"]
    assert detail["opaque_type"] == "ds4_gpu_tensor"
    assert detail["entrypoint_count"] == 86
    life = detail["lifecycle_entrypoints"]
    assert "ds4_gpu_tensor_alloc" in life
    assert "ds4_gpu_begin_commands" in life
    assert "ds4_gpu_flush_commands" in life
    assert "ds4_gpu_tensor_free" in life
    assert detail["primitive_entrypoint_examples"], "no primitive entrypoints listed"
    assert detail["streaming_entrypoint_examples"], "no streaming entrypoints listed"


# --------------------------------------------------------------------------
# Distributed protocol surface.
# --------------------------------------------------------------------------

def test_distributed_protocol(manifest: dict) -> None:
    proto = manifest["distributed_protocol"]
    assert "DS4D" in proto["magic"]
    for op in ("DS4_DIST_MSG_HELLO", "DS4_DIST_MSG_WORK", "DS4_DIST_MSG_RESULT",
               "DS4_DIST_MSG_SNAPSHOT_BEGIN"):
        assert op in proto["message_ops"], f"missing dist message op: {op}"
    for fn in ("ds4_dist_run", "ds4_dist_session_create", "ds4_dist_session_eval"):
        assert fn in proto["public_functions"], f"missing dist fn: {fn}"


# --------------------------------------------------------------------------
# KV / SSD surface.
# --------------------------------------------------------------------------

def test_kv_ssd_detail(manifest: dict) -> None:
    detail = manifest["kv_ssd_detail"]
    assert "ds4_kvstore_open" in detail["kvstore_functions"]
    assert "ds4_kvstore" in detail["kvstore_structs"]
    assert "ds4_ssd_auto_cache_plan" in detail["ssd_functions"]
    assert "ds4_ssd_cache_plan" in detail["ssd_structs"]


# --------------------------------------------------------------------------
# External oracle vectors (ds4's own tests; pcc does not run them here).
# --------------------------------------------------------------------------

def test_oracle_vectors_defined(manifest: dict) -> None:
    vectors = manifest["oracle_vectors"]
    assert vectors, "oracle vector list must be non-empty"
    paths = {v["path"] for v in vectors}
    for expected in ("tests/ds4_test.c", "tests/test_q4k_dot.c",
                     "tests/cuda_long_context_smoke.c"):
        assert expected in paths, f"missing oracle vector: {expected}"
    for v in vectors:
        assert v.get("kind"), f"oracle vector {v.get('path')} missing kind"
        assert v.get("role"), f"oracle vector {v.get('path')} missing role"


def _data_lines(path: Path) -> list[list[str]]:
    return [
        line.split()
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


def test_captured_official_golden_values_are_complete_and_auditable(
    manifest: dict,
) -> None:
    capture = manifest["oracle_golden_capture"]
    assert capture["manifest"] == "tests/ds4_oracle/golden/manifest.json"
    assert capture["pinned_commit"] == PINNED_COMMIT
    assert "no pcc ds4" in capture["claim"].lower()
    golden_manifest = json.loads(GOLDEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert golden_manifest["pinned_commit"] == PINNED_COMMIT
    assert golden_manifest["pcc_support_claimed"] is False
    entry = golden_manifest["files"][0]
    path = REPO_ROOT / entry["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]

    lines = _data_lines(path)
    case_count = 0
    step_count = 0
    expected_steps = 0
    current_step = -1
    for index, fields in enumerate(lines):
        if fields[0] == "case":
            assert len(fields) == 5
            bytes(fields[1], "utf-8")
            int(fields[2])
            expected_steps = int(fields[3])
            current_step = -1
            case_count += 1
        elif fields[0] == "step":
            assert len(fields) == 4
            current_step += 1
            assert int(fields[1]) == current_step
            bytes.fromhex(fields[2])
            assert int(fields[3]) == 1
            assert index + 1 < len(lines)
            top = lines[index + 1]
            assert top[0] == "top" and top[1] == fields[2]
            assert math.isfinite(float(top[2]))
            step_count += 1
        elif fields[0] == "top":
            assert current_step >= 0
        elif fields[0] == "end":
            assert current_step + 1 == expected_steps
        else:
            raise AssertionError(f"unknown official golden row: {fields}")
    assert case_count == entry["case_count"] == 5
    assert step_count == entry["step_count"] == 17


def test_captured_local_golden_values_preserve_ranked_logits() -> None:
    golden_manifest = json.loads(GOLDEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = golden_manifest["files"][1]
    path = REPO_ROOT / entry["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    lines = _data_lines(path)
    cases = [fields for fields in lines if fields[0] == "case"]
    tops = [fields for fields in lines if fields[0] == "top"]
    assert len(cases) == entry["case_count"] == 1
    assert cases[0][1:5] == ["long_story_4096", "text", "5000", "4096"]
    assert int(cases[0][-1]) == entry["top_count"] == 64
    assert [int(fields[1]) for fields in tops] == list(range(64))
    token_ids = [int(fields[2]) for fields in tops]
    logits = [float(fields[3]) for fields in tops]
    assert len(set(token_ids)) == 64
    assert all(math.isfinite(value) for value in logits)
    assert logits == sorted(logits, reverse=True)
    assert (token_ids[0], logits[0]) == (4371, 36.5096703)
    assert (token_ids[-1], logits[-1]) == (59485, 9.95218468)


def _read_checkout_head(root: Path) -> str:
    head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    ref_path = root / ".git" / head.removeprefix("ref: ")
    return ref_path.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------
# OPTIONAL live cross-check. Skips cleanly when the external tree is absent so
# CI stays green without ~/pcc_refs. Never a source of the core PASS.
# --------------------------------------------------------------------------

def test_live_tree_cross_check_optional(manifest: dict) -> None:
    ds4_root = Path("~/pcc_refs/antirez-ds4-depth1").expanduser()
    if not ds4_root.is_dir():
        pytest.skip("external ds4 reference tree absent; core assertions cover the manifest")
    if not (ds4_root / ".git").exists():
        pytest.skip("external ds4 tree present but not a git checkout")
    try:
        head = _read_checkout_head(ds4_root)
    except OSError:
        pytest.skip("could not read ds4 HEAD commit")
    assert head == PINNED_COMMIT, (
        f"live ds4 tree HEAD {head} != pinned {PINNED_COMMIT}; "
        "re-pin the reference before trusting the inventory"
    )
    # Spot-check that a few inventoried files really exist in the live tree.
    for rel in ("ds4.c", "ds4_gpu.h", "ds4_metal.m", "ds4_distributed.c",
                "metal/cpy.metal", "gguf-tools/deepseek4-quantize.c"):
        assert (ds4_root / rel).is_file(), f"inventoried file missing in live tree: {rel}"
    golden_manifest = json.loads(GOLDEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in golden_manifest["files"]:
        external = ds4_root / entry["source_path"]
        captured = REPO_ROOT / entry["path"]
        assert external.read_bytes() == captured.read_bytes(), (
            f"captured golden drifted from pinned source: {entry['source_path']}"
        )
    source_manifest = ds4_root / "tests/test-vectors/manifest.json"
    assert hashlib.sha256(source_manifest.read_bytes()).hexdigest() == (
        golden_manifest["source_manifest_sha256"]
    )
