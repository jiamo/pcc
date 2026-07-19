from __future__ import annotations

from pathlib import Path

from scripts import goal_state
from scripts.head_truth_manifest import REQUIRED_GATE_IDS, gate_specs


ROOT = Path(__file__).resolve().parents[1]


def test_historical_ledgers_are_preserved_and_startup_docs_are_compact() -> None:
    protocol_archive = (
        ROOT / "docs/archive/goal/codex-goal-prompt-through-2026-07-09.md"
    )
    state_archive = (
        ROOT / "docs/archive/goal/current-goal-state-through-2026-07-09.md"
    )
    protocol = ROOT / "docs/goal/goal-prompt.md"
    legacy_entrypoint = ROOT / ("codex" + "-goal-prompt.md")
    state = ROOT / "docs/current-goal-state.md"

    assert protocol_archive.stat().st_size > 1_000_000
    assert state_archive.stat().st_size > 1_900_000
    assert protocol.stat().st_size < 20_000
    assert not legacy_entrypoint.exists()
    assert state.stat().st_size < 20_000


def test_protocol_keeps_routed_claim_contracts() -> None:
    protocol = (ROOT / "docs/goal/goal-prompt.md").read_text(encoding="utf-8")

    for heading in (
        "### 0.10 Claim hygiene",
        "### 9.1 Generic mechanisms first",
        "### 9.2 Mode boundaries",
        "## 10. Self-backend rules",
        "## 11. Value-model rules",
        "## 12. Five-GC rules",
        "## 13. Virtual-thread rules",
        "## 14. Package and ecosystem rules",
        "## 16. Performance proof",
        "## 19.2 Fixed-point classification",
    ):
        assert heading in protocol
    assert "docs/archive/goal/codex-goal-prompt-through-2026-07-09.md" in protocol
    assert "DONE_WEAK" in protocol
    assert "No other document may own an executable task list" in protocol

def test_repository_has_one_goal_protocol_entrypoint() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    board = goal_state.load_task_board()

    assert board["source_protocol"] == "docs/goal/goal-prompt.md"
    assert "Read `docs/goal/goal-prompt.md` for the single goal contract" in agents
    assert not (ROOT / ("codex" + "-goal-prompt.md")).exists()


def test_maintained_sources_do_not_route_through_legacy_goal_entrypoint() -> None:
    legacy_name = "codex" + "-goal-prompt.md"
    roots = (
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "scripts",
        ROOT / "tests",
        ROOT / "docs/goal",
        ROOT / "docs/design",
        ROOT / "docs/issues",
        ROOT / "docs/plans",
        ROOT / "docs/investigations",
    )

    offenders: list[str] = []
    for root in roots:
        candidates = (root,) if root.is_file() else root.rglob("*")
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix not in {".md", ".py", ".sh"}:
                continue
            if "archive" in candidate.parts or "evidence" in candidate.parts:
                continue
            if legacy_name in candidate.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(candidate.relative_to(ROOT)))

    assert offenders == []


def test_current_goal_state_matches_structured_sources() -> None:
    board = goal_state.load_task_board()
    manifest = goal_state.load_head_truth_manifest()
    expected = goal_state.render_startup_markdown(board, manifest)
    current = (ROOT / "docs/current-goal-state.md").read_text(encoding="utf-8")

    assert current == expected
    assert f"Milestone: `{board['active_milestone']}`" in current
    assert str(manifest["source"]["commit"]) in current
    assert "docs/investigations/INDEX.md" in current


def test_checked_truth_manifest_matches_current_gate_registry() -> None:
    manifest = goal_state.load_head_truth_manifest()
    specs = gate_specs(ROOT)

    assert manifest["required_gate_ids"] == list(REQUIRED_GATE_IDS)
    assert [gate["gate_id"] for gate in manifest["gates"]] == [
        spec.gate_id for spec in specs
    ]
    for gate, spec in zip(manifest["gates"], specs, strict=True):
        assert gate["command"] == list(spec.command)
        assert gate["timeout_seconds"] == spec.timeout_seconds
