from __future__ import annotations

from copy import deepcopy
import json

from scripts.numpy_first_blocker import (
    evaluate_result,
    load_baseline,
    main,
    promote_baseline,
    validate_baseline,
)

_CURRENT = object()


def _result(
    lane_id: str = "numpy-core-head",
    *,
    blocker: dict[str, object] | None | object = _CURRENT,
    baseline: dict[str, object] | None = None,
) -> dict[str, object]:
    baseline = baseline or load_baseline()
    lane = baseline["lanes"][lane_id]
    current = lane["current"]
    observed = (
        dict(current) if blocker is _CURRENT and isinstance(current, dict) else current
    )
    if blocker is not _CURRENT:
        observed = blocker
    return {
        "schema": lane["result_schema"],
        "source": dict(baseline["source"]),
        "mode": dict(lane["mode"]),
        "loader": {"first_blocker": observed},
    }


def _active_baseline() -> dict[str, object]:
    """Return a valid non-terminal lane for state-machine transition tests."""
    baseline = deepcopy(load_baseline())
    baseline["lanes"]["numpy-core-head"]["current"] = {
        "kind": "first_missing_module",
        "phase": "Py_mod_exec",
        "value": "active-test-frontier",
    }
    return baseline


def test_checked_numpy_first_blocker_baseline_is_valid() -> None:
    assert validate_baseline(load_baseline()) == []


def test_current_real_lane_state_is_stable_not_progress() -> None:
    observation = evaluate_result(_result(), "numpy-core-head")

    assert observation["status"] == "STABLE"
    assert observation["accepted"] is True
    assert observation["progressed"] is False
    assert observation["promotion_candidate"] is False
    assert observation["errors"] == []


def test_changed_blocker_requires_explicit_promotion() -> None:
    baseline = _active_baseline()
    candidate = _result(
        blocker={
            "kind": "first_missing_module",
            "phase": "Py_mod_exec",
            "value": "sys",
        },
        baseline=baseline,
    )

    observation = evaluate_result(candidate, "numpy-core-head", baseline=baseline)

    assert observation["status"] == "UNREVIEWED_CHANGE"
    assert observation["accepted"] is False
    assert observation["promotion_candidate"] is True
    assert "explicit baseline promotion required" in observation["errors"][0]


def test_promotion_preserves_history_and_rejects_reappearance() -> None:
    baseline = _active_baseline()
    candidate = _result(
        blocker={
            "kind": "first_semantic_mismatch",
            "phase": "Py_mod_exec",
            "value": "dtype registration mismatch",
        },
        baseline=baseline,
    )

    promoted = promote_baseline(baseline, "numpy-core-head", candidate)
    previous_lane = baseline["lanes"]["numpy-core-head"]

    assert (
        promoted["lanes"]["numpy-core-head"]["frontier"]
        == previous_lane["frontier"] + 1
    )
    assert promoted["lanes"]["numpy-core-head"]["resolved"] == [
        *previous_lane["resolved"],
        {
            "frontier": previous_lane["frontier"],
            "blocker": previous_lane["current"],
        },
    ]
    assert (
        evaluate_result(candidate, "numpy-core-head", baseline=promoted)["status"]
        == "STABLE"
    )
    regression = evaluate_result(
        _result(baseline=baseline), "numpy-core-head", baseline=promoted
    )
    assert regression["status"] == "REGRESSION"
    assert regression["accepted"] is False


def test_empty_blocker_requires_promotion_then_becomes_terminal() -> None:
    baseline = _active_baseline()
    complete_result = _result(blocker=None, baseline=baseline)

    observation = evaluate_result(
        complete_result, "numpy-core-head", baseline=baseline
    )

    assert observation["status"] == "UNREVIEWED_CHANGE"
    assert observation["accepted"] is False
    assert observation["promotion_candidate"] is True
    assert "first blocker cleared" in observation["errors"][0]

    promoted = promote_baseline(baseline, "numpy-core-head", complete_result)
    lane = promoted["lanes"]["numpy-core-head"]
    assert lane["current"] is None
    assert validate_baseline(promoted) == []
    assert (
        evaluate_result(complete_result, "numpy-core-head", baseline=promoted)["status"]
        == "STABLE"
    )

    reappeared = evaluate_result(
        _result(baseline=baseline), "numpy-core-head", baseline=promoted
    )
    assert reappeared["status"] == "REGRESSION"
    assert reappeared["accepted"] is False


def test_earlier_phase_and_identity_drift_cannot_be_promoted() -> None:
    baseline = _active_baseline()
    earlier = _result(
        blocker={
            "kind": "first_missing_symbol",
            "phase": "extension_load_or_init",
            "value": "PyInit__multiarray_umath",
        },
        baseline=baseline,
    )
    drifted = deepcopy(_result(baseline=baseline))
    drifted["source"]["sha256"] = "0" * 64

    earlier_observation = evaluate_result(
        earlier, "numpy-core-head", baseline=baseline
    )
    drift_observation = evaluate_result(
        drifted, "numpy-core-head", baseline=baseline
    )

    assert earlier_observation["status"] == "REGRESSION"
    assert earlier_observation["promotion_candidate"] is False
    assert drift_observation["status"] == "INVALID"
    assert "source identity drift" in drift_observation["errors"][0]


def test_fake_or_ambiguous_result_cannot_satisfy_real_lane() -> None:
    fake = _result()
    fake["schema"] = "fake-provider-slot-result.v1"
    ambiguous = _result()
    ambiguous["loader"]["first_blocker"] = {
        "kind": "provider_slot_count",
        "phase": "Py_mod_exec",
        "value": "200",
        "second_blocker": "math",
    }

    fake_observation = evaluate_result(fake, "numpy-core-head")
    ambiguous_observation = evaluate_result(ambiguous, "numpy-core-head")

    assert fake_observation["accepted"] is False
    assert "result schema" in fake_observation["errors"][0]
    assert ambiguous_observation["accepted"] is False
    assert any(
        "exactly one" in error or "only kind" in error
        for error in ambiguous_observation["errors"]
    )


def test_cli_check_and_explicit_promotion(tmp_path) -> None:
    baseline_path = tmp_path / "baseline.json"
    result_path = tmp_path / "result.json"
    promoted_path = tmp_path / "promoted.json"
    baseline = _active_baseline()
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            _result(
                blocker={
                    "kind": "first_missing_module",
                    "phase": "Py_mod_exec",
                    "value": "sys",
                },
                baseline=baseline,
            )
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--baseline",
                str(baseline_path),
                "check",
                "--lane",
                "numpy-core-head",
                "--result",
                str(result_path),
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "--baseline",
                str(baseline_path),
                "promote",
                "--lane",
                "numpy-core-head",
                "--result",
                str(result_path),
                "--write",
                str(promoted_path),
            ]
        )
        == 0
    )
    promoted = json.loads(promoted_path.read_text(encoding="utf-8"))
    assert (
        promoted["lanes"]["numpy-core-head"]["frontier"]
        == baseline["lanes"]["numpy-core-head"]["frontier"] + 1
    )
