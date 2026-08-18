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


def test_bootstrap_default_stage_execution_is_memory_guarded():
    repo = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "bootstrap.sh").exists()
    )
    script = (repo / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'PCC_BOOTSTRAP_EXTERNAL_MEMORY_GUARD:-0' in script
    assert 'stage${stage}.process.XXXXXX' in script
    assert 'run_process_tree_sample.py' in script
    assert '--max-tree-rss-bytes' in script
    assert '"${BOOTSTRAP_MAX_TREE_RSS_BYTES}"' in script
    assert '--timeout' in script
    assert '"${BOOTSTRAP_STAGE_TIMEOUT}"' in script
    assert '"${target_cmd[@]}"' in script
    assert 'run_pcc_deferred_link.py' in script
