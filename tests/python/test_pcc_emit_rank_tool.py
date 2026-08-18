from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_emit_rank_records_fresh_worker_metrics_and_slowest_first(tmp_path):
    compiler = tmp_path / "fake-pcc1"
    compiler.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys
import time

flag, input_path, result_path, assembly_path, cc = sys.argv[1:]
assert flag == "--pcc-self-backend-emit-worker"
assert cc == ""
text = pathlib.Path(input_path).read_text()
time.sleep(0.50 if "slow" in text else 0.01)
pathlib.Path(assembly_path).write_text("asm:" + text)
pathlib.Path(result_path).write_text(
    "self-aarch64-darwin-v0\\n" + str(pathlib.Path(assembly_path).resolve())
)
""",
        encoding="utf-8",
    )
    compiler.chmod(0o755)
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    items = []
    for index, text in enumerate(("fast-a", "slow-b", "fast-c")):
        path = input_dir / f"item_{index:03d}.ll"
        path.write_text(text, encoding="utf-8")
        items.append(
            {
                "index": index,
                "module_index": index,
                "module_name": f"module_{index}",
                "module_shard_index": 0,
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    input_manifest = input_dir / "manifest.json"
    input_manifest.write_text(
        json.dumps(
            {
                "schema": "pcc.stage2-object-inputs.v1",
                "source_bundle_sha256": "bundle-hash",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "rank"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pcc_emit_rank.py",
            "--compiler",
            str(compiler),
            "--input-manifest",
            str(input_manifest),
            "--output-dir",
            str(output_dir),
            "--lane",
            "all",
            "--jobs",
            "2",
            "--timeout",
            "5",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["status"] == "COMPLETE"
    assert manifest["selected_count"] == manifest["completed_count"] == 3
    assert manifest["source_bundle_sha256"] == "bundle-hash"
    assert manifest["ranking"][0]["index"] == 1
    assert manifest["ranking"][0]["wall_s"] >= 0.08
    assert all(item["assembly_sha256"] for item in manifest["items"])

    selected_dir = tmp_path / "rank-selected"
    selected = subprocess.run(
        [
            sys.executable,
            "scripts/pcc_emit_rank.py",
            "--compiler",
            str(compiler),
            "--input-manifest",
            str(input_manifest),
            "--output-dir",
            str(selected_dir),
            "--lane",
            "all",
            "--item-index",
            "1",
            "--jobs",
            "1",
            "--timeout",
            "5",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert selected.returncode == 0, selected.stderr
    selected_manifest = json.loads(
        (selected_dir / "manifest.json").read_text()
    )
    assert selected_manifest["requested_item_indices"] == [1]
    assert [item["index"] for item in selected_manifest["items"]] == [1]
