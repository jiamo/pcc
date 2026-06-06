"""Persistent first-blocker ratchet for real NumPy integration gates."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "numpy_first_blocker_baseline.json"
SCHEMA = "pcc.numpy-first-blocker-baseline.v1"
ALLOWED_KINDS = {
    "first_missing_module",
    "first_missing_symbol",
    "first_semantic_mismatch",
}
PHASE_RANK = {
    "loader_compile": 0,
    "extension_load_or_init": 1,
    "PyInit": 2,
    "Py_mod_exec": 3,
    "python_module_graph": 4,
    "array_runtime": 5,
}
SOURCE_KEYS = ("name", "version", "sha256")
MODE_KEYS = ("compiler", "backend", "python_abi", "libpython", "ir_scaffold")


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("NumPy first-blocker baseline root must be an object")
    return value


def _selected(value: object, keys: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in keys}


def _blocker_errors(
    blocker: object, *, prefix: str, allow_empty: bool = False
) -> list[str]:
    if blocker is None and allow_empty:
        return []
    if not isinstance(blocker, dict):
        return [f"{prefix} must contain exactly one first-blocker object"]
    errors: list[str] = []
    if set(blocker) != {"kind", "value", "phase"}:
        errors.append(f"{prefix} must contain only kind, value, and phase")
    if blocker.get("kind") not in ALLOWED_KINDS:
        errors.append(f"{prefix}.kind is not an allowed first-blocker category")
    if not isinstance(blocker.get("value"), str) or not blocker.get("value"):
        errors.append(f"{prefix}.value must be a non-empty string")
    if blocker.get("phase") not in PHASE_RANK:
        errors.append(f"{prefix}.phase is not an ordered integration phase")
    return errors


def validate_baseline(baseline: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if baseline.get("schema") != SCHEMA:
        errors.append(f"unexpected baseline schema {baseline.get('schema')!r}")
    source = _selected(baseline.get("source"), SOURCE_KEYS)
    if any(
        not isinstance(source.get(key), str) or not source.get(key)
        for key in SOURCE_KEYS
    ):
        errors.append("baseline source must record name, version, and sha256")
    lanes = baseline.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        return [*errors, "baseline lanes must be a non-empty object"]
    for lane_id, lane in lanes.items():
        prefix = f"lane {lane_id}"
        if not isinstance(lane, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(lane.get("result_schema"), str):
            errors.append(f"{prefix}.result_schema must be a string")
        mode = _selected(lane.get("mode"), MODE_KEYS)
        if any(
            not isinstance(mode.get(key), str) or not mode.get(key) for key in MODE_KEYS
        ):
            errors.append(f"{prefix}.mode must record the complete execution mode")
        frontier = lane.get("frontier")
        resolved = lane.get("resolved")
        if not isinstance(frontier, int) or frontier < 0:
            errors.append(f"{prefix}.frontier must be a non-negative integer")
        if not isinstance(resolved, list):
            errors.append(f"{prefix}.resolved must be a list")
            resolved = []
        if isinstance(frontier, int) and frontier != len(resolved):
            errors.append(f"{prefix}.frontier must equal resolved history length")
        seen: list[dict[str, object]] = []
        for index, entry in enumerate(resolved):
            if not isinstance(entry, dict) or set(entry) != {"frontier", "blocker"}:
                errors.append(
                    f"{prefix}.resolved[{index}] must contain frontier and blocker"
                )
                continue
            if entry.get("frontier") != index:
                errors.append(
                    f"{prefix}.resolved[{index}] has a non-sequential frontier"
                )
            errors.extend(
                _blocker_errors(
                    entry.get("blocker"), prefix=f"{prefix}.resolved[{index}].blocker"
                )
            )
            if isinstance(entry.get("blocker"), dict):
                if entry["blocker"] in seen:
                    errors.append(f"{prefix}.resolved contains a duplicate blocker")
                seen.append(entry["blocker"])
        errors.extend(
            _blocker_errors(
                lane.get("current"), prefix=f"{prefix}.current", allow_empty=True
            )
        )
        if isinstance(lane.get("current"), dict) and lane["current"] in seen:
            errors.append(f"{prefix}.current already appears in resolved history")
    return errors


def evaluate_result(
    result: dict[str, object],
    lane_id: str,
    *,
    baseline: dict[str, object] | None = None,
) -> dict[str, object]:
    baseline = baseline or load_baseline()
    errors = validate_baseline(baseline)
    lanes = baseline.get("lanes") if isinstance(baseline.get("lanes"), dict) else {}
    lane = lanes.get(lane_id) if isinstance(lanes, dict) else None
    if not isinstance(lane, dict):
        errors.append(f"unknown NumPy first-blocker lane: {lane_id}")
        lane = {}
    if result.get("schema") != lane.get("result_schema"):
        errors.append(
            f"{lane_id}: result schema {result.get('schema')!r} does not match baseline"
        )
    expected_source = _selected(baseline.get("source"), SOURCE_KEYS)
    observed_source = _selected(result.get("source"), SOURCE_KEYS)
    if observed_source != expected_source:
        errors.append(f"{lane_id}: source identity drift")
    expected_mode = _selected(lane.get("mode"), MODE_KEYS)
    observed_mode = _selected(result.get("mode"), MODE_KEYS)
    if observed_mode != expected_mode:
        errors.append(f"{lane_id}: execution mode drift")
    loader = result.get("loader") if isinstance(result.get("loader"), dict) else {}
    observed = loader.get("first_blocker") if isinstance(loader, dict) else None
    errors.extend(
        _blocker_errors(observed, prefix=f"{lane_id}.first_blocker", allow_empty=True)
    )

    status = "INVALID"
    accepted = False
    progressed = False
    promotion_candidate = False
    current = lane.get("current")
    resolved_entries = (
        lane.get("resolved") if isinstance(lane.get("resolved"), list) else []
    )
    resolved = [
        entry.get("blocker")
        for entry in resolved_entries
        if isinstance(entry, dict) and isinstance(entry.get("blocker"), dict)
    ]
    if not errors and observed == current:
        status = "STABLE"
        accepted = True
    elif not errors and observed in resolved:
        status = "REGRESSION"
        errors.append(f"{lane_id}: observed a previously resolved blocker")
    elif not errors and current is None:
        status = "REGRESSION"
        errors.append(f"{lane_id}: a blocker reappeared after import completion")
    elif not errors and observed is None and isinstance(current, dict):
        status = "UNREVIEWED_CHANGE"
        promotion_candidate = True
        errors.append(
            f"{lane_id}: first blocker cleared; explicit baseline promotion required"
        )
    elif not errors and isinstance(observed, dict) and isinstance(current, dict):
        observed_rank = PHASE_RANK[str(observed["phase"])]
        current_rank = PHASE_RANK[str(current["phase"])]
        if observed_rank < current_rank:
            status = "REGRESSION"
            errors.append(f"{lane_id}: first blocker moved to an earlier phase")
        else:
            status = "UNREVIEWED_CHANGE"
            promotion_candidate = True
            errors.append(
                f"{lane_id}: first blocker changed; explicit baseline promotion required"
            )
    return {
        "lane": lane_id,
        "status": status,
        "accepted": accepted,
        "progressed": progressed,
        "promotion_candidate": promotion_candidate,
        "frontier": lane.get("frontier"),
        "baseline": current,
        "observed": observed,
        "errors": errors,
    }


def promote_baseline(
    baseline: dict[str, object],
    lane_id: str,
    result: dict[str, object],
) -> dict[str, object]:
    observation = evaluate_result(result, lane_id, baseline=baseline)
    if (
        observation["status"] != "UNREVIEWED_CHANGE"
        or not observation["promotion_candidate"]
    ):
        raise ValueError("result is not an eligible forward promotion candidate")
    promoted = copy.deepcopy(baseline)
    lane = promoted["lanes"][lane_id]
    frontier = lane["frontier"]
    lane["resolved"].append({"frontier": frontier, "blocker": lane["current"]})
    lane["frontier"] = frontier + 1
    lane["current"] = observation["observed"]
    errors = validate_baseline(promoted)
    if errors:
        raise ValueError("invalid promoted baseline: " + "; ".join(errors))
    return promoted


def _read_result(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("NumPy gate result root must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=str(BASELINE_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--lane", required=True)
    check_parser.add_argument("--result", required=True)
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--lane", required=True)
    promote_parser.add_argument("--result", required=True)
    promote_parser.add_argument("--write", required=True)
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline).expanduser().resolve()
    baseline = load_baseline(baseline_path)
    if args.command == "validate":
        errors = validate_baseline(baseline)
        print(json.dumps({"ok": not errors, "errors": errors}, sort_keys=True))
        return 0 if not errors else 1

    result = _read_result(Path(args.result).expanduser().resolve())
    if args.command == "check":
        observation = evaluate_result(result, args.lane, baseline=baseline)
        print(json.dumps(observation, indent=2, sort_keys=True))
        return 0 if observation["accepted"] is True else 1

    try:
        promoted = promote_baseline(baseline, args.lane, result)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    write_path = Path(args.write).expanduser().resolve()
    write_path.parent.mkdir(parents=True, exist_ok=True)
    write_path.write_text(
        json.dumps(promoted, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "lane": args.lane,
                "frontier": promoted["lanes"][args.lane]["frontier"],
                "write": str(write_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
