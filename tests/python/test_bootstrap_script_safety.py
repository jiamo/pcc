from __future__ import annotations

from pathlib import Path


def test_bootstrap_stage_outputs_do_not_reuse_stale_artifacts():
    repo = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "bootstrap.sh").exists()
    )
    script = (repo / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'rm -f "${out_exe}" "${out_exe}.tmp"' in script
    assert "refusing stale stage artifact" in script


def test_bootstrap_supports_stage_boundary_restart():
    repo = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "bootstrap.sh").exists()
    )
    script = (repo / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert "START_STAGE=1" in script
    assert "--from-stage|--start-stage" in script
    assert "START_STAGE} -le 2 && ${STAGE_LIMIT} -ge 2" in script
    assert "START_STAGE} -le 3 && ${STAGE_LIMIT} -ge 3" in script
