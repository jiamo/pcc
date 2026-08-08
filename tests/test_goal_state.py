from __future__ import annotations

from copy import deepcopy

from scripts import goal_state


def _task(
    task_id: str,
    *,
    milestone: str,
    rank: int,
    status: str = "TODO_READY",
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "priority": "P0",
        "status": status,
        "track": "test",
        "title": task_id,
        "milestone": milestone,
        "depends_on": depends_on or [],
        "rank": rank,
        "exit_criteria": [f"Complete {task_id}"],
        "latest_evidence": "",
        "open_boundary": "" if status == "DONE_STRONG" else "Not complete",
        "required_gates": ["test gate"],
    }


def _board(tasks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "version": 2,
        "source_protocol": goal_state.CANONICAL_GOAL_PROTOCOL,
        "active_milestone": "M1",
        "milestone_order": ["M0", "M1", "M2"],
        "tasks": tasks,
    }


def test_sorted_open_tasks_uses_active_milestone_rank_and_dependencies() -> None:
    board = _board(
        [
            _task("M0-DONE", milestone="M0", rank=1, status="DONE_STRONG"),
            _task("M1-BLOCKER", milestone="M1", rank=90),
            _task(
                "M1-BLOCKED-FIRST",
                milestone="M1",
                rank=1,
                depends_on=["M1-BLOCKER"],
            ),
            _task("M1-READY", milestone="M1", rank=20, depends_on=["M0-DONE"]),
            _task("M2-HIGH-PRIORITY", milestone="M2", rank=0),
        ]
    )

    assert goal_state.validate(board) == []
    assert [task["id"] for task in goal_state.sorted_open_tasks(board)] == [
        "M1-READY",
        "M1-BLOCKER",
    ]

    completed = deepcopy(board)
    completed["tasks"][1]["status"] = "DONE_STRONG"
    completed["tasks"][1]["open_boundary"] = ""
    assert [task["id"] for task in goal_state.sorted_open_tasks(completed)] == [
        "M1-BLOCKED-FIRST",
        "M1-READY",
    ]


def test_validate_rejects_unknown_later_and_cyclic_dependencies() -> None:
    board = _board(
        [
            _task("M1-A", milestone="M1", rank=1, depends_on=["M1-B"]),
            _task("M1-B", milestone="M1", rank=2, depends_on=["M1-A"]),
            _task("M1-LATER", milestone="M1", rank=3, depends_on=["M2-TASK"]),
            _task("M1-UNKNOWN", milestone="M1", rank=4, depends_on=["MISSING"]),
            _task("M2-TASK", milestone="M2", rank=1),
        ]
    )

    errors = goal_state.validate(board)

    assert "M1-LATER: dependency 'M2-TASK' belongs to later milestone M2" in errors
    assert "M1-UNKNOWN: unknown dependency 'MISSING'" in errors
    assert any(error.startswith("dependency cycle: ") for error in errors)


def test_validate_requires_version_2_execution_fields() -> None:
    task = _task("M1-TASK", milestone="M1", rank=1)
    del task["exit_criteria"]

    errors = goal_state.validate(_board([task]))

    assert "M1-TASK: missing version 2 fields: exit_criteria" in errors


def test_validate_requires_the_single_canonical_protocol() -> None:
    board = _board([_task("M1-TASK", milestone="M1", rank=1)])
    board["source_protocol"] = "legacy-goal-prompt.md"

    errors = goal_state.validate(board)

    assert (
        "version 2 board source_protocol must be docs/goal/goal-prompt.md"
        in errors
    )


def test_validate_rejects_oversized_boundaries_and_unbounded_performance_rows() -> None:
    task = _task("PERF-M1-TASK", milestone="M1", rank=1)
    task["track"] = "performance/runtime"
    task["open_boundary"] = "x" * (goal_state.MAX_OPEN_BOUNDARY_CHARS + 1)

    errors = goal_state.validate(_board([task]))

    assert (
        "PERF-M1-TASK: open_boundary exceeds "
        + str(goal_state.MAX_OPEN_BOUNDARY_CHARS)
        + " characters"
    ) in errors
    assert "PERF-M1-TASK: unfinished performance task missing scope_limit" in errors
    for field in goal_state.REQUIRED_OPEN_PERFORMANCE_FIELDS - {"scope_limit"}:
        assert (
            "PERF-M1-TASK: execution-ready performance task missing " + field
        ) in errors

    task["open_boundary"] = "One bounded optimization shape."
    task["scope_limit"] = "One lowering pattern on AArch64."
    task["baseline_metric"] = "Instruction count on one pinned fixture."
    task["success_threshold"] = "At least 10% fewer instructions."
    task["failure_disposition"] = "Record rejection evidence and remove the experiment."
    task["baseline_evidence"] = "docs/goal/task-board.yaml"
    assert goal_state.validate(_board([task])) == []


def test_validate_allows_performance_design_rows_without_invented_baselines() -> None:
    task = _task(
        "PERF-M1-DESIGN",
        milestone="M1",
        rank=1,
        status="TODO_NEEDS_DESIGN",
    )
    task["track"] = "performance/runtime"
    task["scope_limit"] = "One finite runtime shape."

    assert goal_state.validate(_board([task])) == []

    del task["scope_limit"]
    assert (
        "PERF-M1-DESIGN: unfinished performance task missing scope_limit"
        in goal_state.validate(_board([task]))
    )


def test_validate_requires_traceable_baseline_evidence_for_ready_performance_rows() -> None:
    task = _task("PERF-M1-READY", milestone="M1", rank=1)
    task["track"] = "performance/runtime"
    task["scope_limit"] = "One finite runtime shape."
    task["baseline_metric"] = "Recorded wall time and peak RSS."
    task["success_threshold"] = "Beat the recorded baseline without higher RSS."
    task["failure_disposition"] = "Remove the experiment and record rejection evidence."

    errors = goal_state.validate(_board([task]))
    assert (
        "PERF-M1-READY: execution-ready performance task missing baseline_evidence"
        in errors
    )

    task["baseline_evidence"] = "docs/goal/does-not-exist.md"
    errors = goal_state.validate(_board([task]))
    assert (
        "PERF-M1-READY: baseline_evidence missing: "
        "docs/goal/does-not-exist.md"
        in errors
    )

    task["baseline_evidence"] = "docs/goal/task-board.yaml"
    assert goal_state.validate(_board([task])) == []


def test_validate_requires_a_finite_budget_for_task_producing_audits() -> None:
    task = _task("AUDIT-M1-TASK", milestone="M1", rank=1)
    task["produces_tasks"] = True

    errors = goal_state.validate(_board([task]))

    assert (
        "AUDIT-M1-TASK: produces_tasks requires a positive "
        "task_expansion_limit no greater than 6"
        in errors
    )

    task["task_expansion_limit"] = 6
    assert goal_state.validate(_board([task])) == []
