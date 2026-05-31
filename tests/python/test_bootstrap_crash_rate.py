from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "bootstrap_crash_rate.py").exists():
            return parent
    raise RuntimeError("could not locate pcc repository root")


REPO_ROOT = _repo_root()


def test_bootstrap_crash_rate_dry_run_writes_summary(tmp_path):
    script = REPO_ROOT / "scripts" / "bootstrap_crash_rate.py"
    proc = subprocess.run(
        [
            "python3",
            str(script),
            "--dry-run",
            "--runs",
            "2",
            "--out-root",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "pcc.bootstrap_crash_rate.v1"
    assert summary["runs"] == 2
    assert summary["passes"] == 2
    assert summary["failures"] == 0
    assert summary["run_results"][0]["dry_run"] is True
