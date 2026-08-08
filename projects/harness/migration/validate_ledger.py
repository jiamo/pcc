#!/usr/bin/env python3
"""Validate the auditable DeepSeek Harness migration ledger."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


SCHEMA = "pcc.harness.migration.v1"
ENTRY_NAME = re.compile(r"^(?P<sequence>[0-9]{4})-[a-z0-9][a-z0-9-]*\.md$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PENDING = re.compile(r"^pending:[a-z0-9][a-z0-9-]*$")
TASK = re.compile(r"^HARNESS-[A-Z0-9-]+$")
SECTION_NAMES = (
    "Behavior migrated",
    "PCC facilities",
    "Verification",
    "GUI evidence",
    "Remaining boundaries",
)
METADATA_NAMES = (
    "Schema",
    "Sequence",
    "PCC change",
    "Upstream range",
    "Native-only rationale",
    "Changed domains",
    "Tasks",
    "GUI impact",
)


class LedgerEntry:
    """One parsed migration record and its source path."""

    def __init__(
        self,
        path: Path,
        title: str,
        metadata: dict[str, str],
        sections: dict[str, list[str]],
    ) -> None:
        self.path = path
        self.title = title
        self.metadata = metadata
        self.sections = sections


def _split_csv(value: str) -> list[str]:
    """Return trimmed non-empty comma-separated values."""

    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_entry(path: Path) -> tuple[LedgerEntry | None, list[str]]:
    """Parse one ledger entry without reading any referenced external files."""

    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# ") or len(lines[0]) <= 2:
        errors.append(f"{path.name}: first line must be a non-empty level-one title")
        title = ""
    else:
        title = lines[0][2:].strip()

    metadata: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current_section = ""
    for line in lines[1:]:
        if line.startswith("## "):
            current_section = line[3:].strip()
            if current_section in sections:
                errors.append(f"{path.name}: duplicate section {current_section!r}")
            sections.setdefault(current_section, [])
            continue
        if current_section:
            if line.startswith("- "):
                sections[current_section].append(line[2:].strip())
            elif line.strip():
                errors.append(
                    f"{path.name}: section {current_section!r} accepts bullet lines only"
                )
            continue
        if not line.strip():
            continue
        if not line.startswith("- ") or ": " not in line:
            errors.append(f"{path.name}: malformed metadata line {line!r}")
            continue
        name, value = line[2:].split(": ", 1)
        if name in metadata:
            errors.append(f"{path.name}: duplicate metadata field {name!r}")
        metadata[name] = value.strip()

    for name in METADATA_NAMES:
        if not metadata.get(name):
            errors.append(f"{path.name}: missing metadata field {name!r}")
    unknown_metadata = sorted(set(metadata) - set(METADATA_NAMES))
    for name in unknown_metadata:
        errors.append(f"{path.name}: unknown metadata field {name!r}")
    for name in SECTION_NAMES:
        if not sections.get(name):
            errors.append(f"{path.name}: missing non-empty section {name!r}")
    unknown_sections = sorted(set(sections) - set(SECTION_NAMES))
    for name in unknown_sections:
        errors.append(f"{path.name}: unknown section {name!r}")

    return LedgerEntry(path, title, metadata, sections), errors


def _run_git(repo: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a bounded, non-interactive Git query for ledger ownership checks."""

    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _implementation_path(path: str) -> bool:
    """Return whether a project path requires a migration record."""

    prefix = "projects/harness/"
    if not path.startswith(prefix):
        return False
    relative = path[len(prefix) :]
    if relative.startswith("build/") or relative.startswith("migration/commits/"):
        return False
    return relative not in {
        "AGENTS.md",
        "ARCHITECTURE.md",
        "README.md",
        "TASKS.md",
        "migration/README.md",
        "migration/upstream.json",
        "migration/ENTRY_TEMPLATE.md",
    }


def _implementation_commits(repo: Path) -> tuple[list[str], list[str]]:
    """Return ordered committed Harness implementation changes and errors."""

    result = _run_git(repo, ["log", "--reverse", "--format=%H", "--", "projects/harness"])
    if result.returncode != 0:
        return [], [f"git log failed: {result.stderr.strip()}"]
    commits: list[str] = []
    errors: list[str] = []
    for commit in result.stdout.splitlines():
        changed = _run_git(
            repo,
            ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit],
        )
        if changed.returncode != 0:
            errors.append(f"git diff-tree failed for {commit}: {changed.stderr.strip()}")
            continue
        if any(_implementation_path(path) for path in changed.stdout.splitlines()):
            commits.append(commit)
    return commits, errors


def _dirty_implementation_paths(repo: Path) -> tuple[list[str], list[str]]:
    """Return dirty Harness implementation paths and Git query errors."""

    result = _run_git(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all", "--", "projects/harness"],
    )
    if result.returncode != 0:
        return [], [f"git status failed: {result.stderr.strip()}"]
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if _implementation_path(path):
            paths.append(path)
    return paths, []


def _task_ids(board_path: Path) -> set[str]:
    """Extract task ids from the repository task board without a YAML dependency."""

    ids: set[str] = set()
    for line in board_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*- id: ([A-Z0-9-]+)\s*$", line)
        if match:
            ids.add(match.group(1))
    return ids


def _validate_entry(
    entry: LedgerEntry,
    expected_sequence: int,
    known_tasks: set[str],
) -> list[str]:
    """Validate one parsed entry's fields and section values."""

    errors: list[str] = []
    label = entry.path.name
    metadata = entry.metadata
    name_match = ENTRY_NAME.match(label)
    if name_match is None:
        errors.append(f"{label}: filename must match NNNN-lowercase-slug.md")
    else:
        file_sequence = int(name_match.group("sequence"))
        if file_sequence != expected_sequence:
            errors.append(
                f"{label}: out of order; expected sequence {expected_sequence:04d}"
            )
        try:
            declared_sequence = int(metadata.get("Sequence", ""))
        except ValueError:
            declared_sequence = -1
        if declared_sequence != file_sequence:
            errors.append(f"{label}: Sequence must equal filename sequence")

    if metadata.get("Schema") != SCHEMA:
        errors.append(f"{label}: Schema must be {SCHEMA}")

    pcc_change = metadata.get("PCC change", "")
    if not COMMIT.fullmatch(pcc_change) and not PENDING.fullmatch(pcc_change):
        errors.append(f"{label}: PCC change must be a full hash or pending:<slice>")

    upstream_range = metadata.get("Upstream range", "")
    rationale = metadata.get("Native-only rationale", "")
    if upstream_range == "not-applicable":
        if rationale in {"", "not-applicable"}:
            errors.append(f"{label}: native-only entry requires a concrete rationale")
    else:
        parts = upstream_range.split("..")
        if len(parts) != 2 or not all(COMMIT.fullmatch(part) for part in parts):
            errors.append(f"{label}: Upstream range must be <40hex>..<40hex>")
        if rationale != "not-applicable":
            errors.append(
                f"{label}: ranged upstream entry must use Native-only rationale: not-applicable"
            )

    domains = _split_csv(metadata.get("Changed domains", ""))
    if not domains or any(" " in domain for domain in domains):
        errors.append(f"{label}: Changed domains must be comma-separated path-like tokens")

    tasks = _split_csv(metadata.get("Tasks", ""))
    if not tasks:
        errors.append(f"{label}: Tasks must name at least one HARNESS task")
    for task in tasks:
        if not TASK.fullmatch(task):
            errors.append(f"{label}: malformed task id {task!r}")
        elif task not in known_tasks:
            errors.append(f"{label}: task id {task!r} is absent from task-board.yaml")

    gui_impact = metadata.get("GUI impact", "")
    if gui_impact not in {"none", "changed"}:
        errors.append(f"{label}: GUI impact must be 'none' or 'changed'")
    gui_evidence = entry.sections.get("GUI evidence", [])
    if gui_impact == "none" and not all(
        item.startswith("NOT-APPLICABLE | ") for item in gui_evidence
    ):
        errors.append(f"{label}: GUI impact none requires NOT-APPLICABLE evidence")
    if gui_impact == "changed" and not all(
        item.startswith("PASS | ") or item.startswith("PENDING | ")
        for item in gui_evidence
    ):
        errors.append(f"{label}: changed GUI requires PASS or PENDING evidence")

    verification = entry.sections.get("Verification", [])
    for item in verification:
        fields = [field.strip() for field in item.split(" | ")]
        if len(fields) != 3 or fields[0] not in {"PASS", "NOT-RUN"}:
            errors.append(
                f"{label}: verification must be STATUS | command | result or reason"
            )
    remaining = entry.sections.get("Remaining boundaries", [])
    if "none" in remaining and len(remaining) != 1:
        errors.append(f"{label}: Remaining boundaries 'none' must be the only item")
    return errors


def validate_ledger(repo: Path) -> list[str]:
    """Return every ledger validation error for a PCC repository checkout."""

    project = repo / "projects" / "harness"
    migration = project / "migration"
    entries_dir = migration / "commits"
    board_path = repo / "docs" / "goal" / "task-board.yaml"
    upstream_path = migration / "upstream.json"
    errors: list[str] = []

    try:
        upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read migration/upstream.json: {exc}"]
    audited_commit = upstream.get("last_audited_commit", "")
    if not COMMIT.fullmatch(str(audited_commit)):
        errors.append("migration/upstream.json has an invalid last_audited_commit")

    known_tasks = _task_ids(board_path)
    paths = sorted(path for path in entries_dir.iterdir() if path.suffix == ".md")
    if not paths:
        return ["migration ledger has no entries"]

    entries: list[LedgerEntry] = []
    for index, path in enumerate(paths, start=1):
        entry, parse_errors = _parse_entry(path)
        errors.extend(parse_errors)
        if entry is not None:
            entries.append(entry)
            errors.extend(_validate_entry(entry, index, known_tasks))

    pcc_changes = [entry.metadata.get("PCC change", "") for entry in entries]
    duplicates = sorted(
        change for change in set(pcc_changes) if change and pcc_changes.count(change) > 1
    )
    for change in duplicates:
        errors.append(f"duplicate PCC change in ledger: {change}")

    pending_indexes = [
        index for index, change in enumerate(pcc_changes) if PENDING.fullmatch(change)
    ]
    if len(pending_indexes) > 1:
        errors.append("only one pending ledger entry is allowed")
    if pending_indexes and pending_indexes[-1] != len(entries) - 1:
        errors.append("pending ledger entry must be the final entry")

    range_heads: list[str] = []
    for entry in entries:
        value = entry.metadata.get("Upstream range", "")
        if value != "not-applicable" and ".." in value:
            range_heads.append(value.split("..", 1)[1])
    if range_heads and range_heads[-1] != audited_commit:
        errors.append(
            "stale ledger: latest ranged entry does not end at last_audited_commit"
        )

    committed, git_errors = _implementation_commits(repo)
    errors.extend(git_errors)
    recorded_commits = [change for change in pcc_changes if COMMIT.fullmatch(change)]
    for commit in committed:
        count = recorded_commits.count(commit)
        if count == 0:
            errors.append(f"missing ledger entry for Harness implementation commit {commit}")
        elif count > 1:
            errors.append(f"duplicate ledger entries for implementation commit {commit}")
    for commit in recorded_commits:
        if commit not in committed:
            errors.append(f"ledger PCC change is stale or not an implementation commit: {commit}")

    dirty_paths, dirty_errors = _dirty_implementation_paths(repo)
    errors.extend(dirty_errors)
    has_pending = bool(pending_indexes)
    if dirty_paths and not has_pending:
        errors.append("dirty Harness implementation paths require one pending ledger entry")
    if has_pending and not dirty_paths:
        errors.append("stale pending ledger entry: no dirty Harness implementation paths")
    return errors


def main() -> int:
    """Validate the repository containing this script."""

    repo = Path(__file__).resolve().parents[3]
    errors = validate_ledger(repo)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: Harness migration ledger is complete and current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
