#!/usr/bin/env python3
"""Run or validate the commit-bound PCC HEAD truth manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.head_truth_manifest import (
    build_manifest,
    gate_specs,
    load_manifest,
    not_run_result,
    platform_identity,
    run_gate,
    selected_gate_failures,
    source_identity,
    validate_manifest,
    write_manifest,
)
from tests.python.process_timeout import run_process_group_timeout


def command_run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    artifacts_root = Path(args.artifacts_root)
    if not artifacts_root.is_absolute():
        artifacts_root = ROOT / artifacts_root

    specs = gate_specs(ROOT)
    known_gate_ids = {spec.gate_id for spec in specs}
    requested_gate_ids = set(args.gate or ())
    unknown_gate_ids = sorted(requested_gate_ids - known_gate_ids)
    if unknown_gate_ids:
        print(
            "unknown gate(s): " + ", ".join(unknown_gate_ids),
            file=sys.stderr,
        )
        return 2
    if requested_gate_ids:
        selected_gate_ids = requested_gate_ids
    else:
        selected_suites = {"light", "heavy"} if args.suite == "all" else {args.suite}
        selected_gate_ids = {
            spec.gate_id for spec in specs if spec.suite in selected_suites
        }
    source = source_identity(ROOT)
    results = []
    for spec in specs:
        if spec.gate_id not in selected_gate_ids:
            results.append(not_run_result(spec))
            continue
        print(
            f"[{spec.gate_id}] timeout={spec.timeout_seconds}s "
            f"command={' '.join(spec.command)}",
            flush=True,
        )
        result = run_gate(ROOT, artifacts_root, spec, run_process_group_timeout)
        results.append(result)
        print(
            f"[{spec.gate_id}] status={result.status} "
            f"duration={result.duration_seconds}s summary={result.pytest_summary or 'n/a'}",
            flush=True,
        )
        manifest = build_manifest(
            source=source,
            platform_info=platform_identity(),
            results=results,
        )
        write_manifest(output, manifest)
        if result.status != "PASS" and not args.keep_going:
            print(f"wrote incomplete manifest: {output}", file=sys.stderr)
            return 1

    manifest = build_manifest(
        source=source,
        platform_info=platform_identity(),
        results=results,
    )
    write_manifest(output, manifest)
    errors = selected_gate_failures(results, selected_gate_ids)
    errors.extend(
        validate_manifest(
            manifest,
            require_complete=args.suite == "all" and not requested_gate_ids,
        )
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"wrote {output}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    errors = validate_manifest(manifest, require_complete=args.require_complete)
    if args.require_clean_commit:
        source = manifest.get("source")
        if not isinstance(source, dict) or source.get("worktree_dirty") is not False:
            errors.append("clean commit-bound manifest required")
        if manifest.get("claimable_commit") is not True:
            errors.append("manifest is not claimable for its commit")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"OK: {args.manifest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--suite", choices=("light", "heavy", "all"), default="light"
    )
    run_parser.add_argument(
        "--gate",
        action="append",
        help="run only the named gate (repeatable); overrides --suite",
    )
    run_parser.add_argument("--output", default="build/head-truth/manifest.json")
    run_parser.add_argument("--artifacts-root", default="build/head-truth/logs")
    run_parser.add_argument("--keep-going", action="store_true")
    run_parser.set_defaults(func=command_run)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("manifest")
    validate_parser.add_argument("--require-complete", action="store_true")
    validate_parser.add_argument("--require-clean-commit", action="store_true")
    validate_parser.set_defaults(func=command_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
