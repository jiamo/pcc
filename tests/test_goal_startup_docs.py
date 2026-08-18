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


def test_ordinary_sessions_use_the_runner_neutral_resume_loop() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    protocol = (ROOT / "docs/goal/goal-prompt.md").read_text(encoding="utf-8")
    state = (ROOT / "docs/current-goal-state.md").read_text(encoding="utf-8")
    agents_words = " ".join(agents.split())
    protocol_words = " ".join(protocol.split())

    assert "继续任务板" in agents
    assert "scripts/goal_state.py resume" in agents
    assert "scripts/goal_state.py finish-check" in agents
    assert (
        "Do not voluntarily end the session while `resume` reports `CONTINUE`"
        in agents_words
    )
    assert "does not require a runner-specific Goal mode" in protocol_words
    assert "scripts/goal_state.py resume" in protocol
    assert "scripts/goal_state.py finish-check" in protocol
    assert "scripts/goal_state.py resume" in state
    assert "scripts/goal_state.py finish-check" in state


def test_task_board_mutations_are_direct_validated_operations() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    protocol = (ROOT / "docs/goal/goal-prompt.md").read_text(encoding="utf-8")
    words = " ".join((agents + "\n" + protocol).split())

    for operation in ("Add", "Update", "Remove"):
        assert f"{operation} a task" in words
    assert "dispatch: IMMEDIATE" in words
    assert "P0`, `P1`, or `P2" in words
    assert "No CRUD command is required" in words
    assert "reverse dependencies" in words
    assert "linked evidence" in words


def test_startup_projection_is_bounded_under_hundreds_of_tasks() -> None:
    """GOAL-P0-STARTUP-STATE-BOUNDED-RENDER: the active-task table is
    hard-bounded so the render size is independent of the unfinished-task
    count, while milestone/status counts, the dependency-ready selected task
    (id + title + open boundary) and the routing links are all retained."""
    tasks: list[dict[str, object]] = []
    for index in range(400):
        tasks.append(
            {
                "id": f"M1-SYNTH-{index:04d}-A-DESCRIPTIVE-UNFINISHED-TASK-ID",
                "priority": "P0",
                "status": "IN_PROGRESS",
                "track": "test",
                "title": f"Synthetic unfinished task number {index}",
                "milestone": "M1",
                # index 0 is dependency-ready; the rest depend on it so the
                # selection is deterministic and the table stays populated.
                "depends_on": [] if index == 0 else ["M1-SYNTH-0000-A-DESCRIPTIVE-UNFINISHED-TASK-ID"],
                "rank": index,
                "exit_criteria": [f"Complete synthetic task {index}"],
                "latest_evidence": f"docs/goal/evidence/SYNTH/{index:04d}-a-fairly-long-evidence-path.md",
                "open_boundary": "Not complete; " + "detail " * 20,
                "required_gates": ["synthetic gate"],
            }
        )
    board = {
        "version": 2,
        "source_protocol": goal_state.CANONICAL_GOAL_PROTOCOL,
        "active_milestone": "M1",
        "milestone_order": ["M0", "M1", "M2"],
        "tasks": tasks,
    }
    manifest = {"schema": "test", "source": {}, "platform": {}, "gates": []}

    rendered = goal_state.render_startup_markdown(board, manifest)
    size = len(rendered.encode("utf-8"))

    # Count-independent hard bound: hundreds of unfinished rows still render
    # under 20 KiB.
    assert size < 20_000, f"startup projection is {size} bytes, expected < 20000"

    # Exact milestone and status counts are retained (not hidden to shrink).
    assert "Tasks in milestone: `400`" in rendered
    assert "`IN_PROGRESS`: `400`" in rendered

    # Deterministic dependency-ready selection is retained with id, title and
    # open boundary.
    resume_outcome, next_task = goal_state.resume_state(board)
    assert next_task is not None and next_task["id"].endswith("SYNTH-0000-A-DESCRIPTIVE-UNFINISHED-TASK-ID")
    assert f"Next dependency-ready task: `{next_task['id']}`" in rendered
    assert f"Next title: {next_task['title']}" in rendered
    assert str(next_task["open_boundary"])[:20] in rendered

    # Authority/routing links survive the bound.
    assert "docs/investigations/INDEX.md" in rendered
    assert "docs/goal/task-board.yaml` is the only executable task queue" in rendered

    # The table was bounded (not the whole board hidden): the omitted-count
    # line points back to the authoritative queue.
    assert "more unfinished rows are not shown" in rendered
    assert rendered.count("| `M1-SYNTH-") == 40
