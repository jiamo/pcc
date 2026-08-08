"""Tests for the auditable Harness migration-ledger gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "projects" / "harness" / "migration" / "validate_ledger.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("harness_ledger_validator", VALIDATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _entry(sequence: int, pcc_change: str, upstream: str) -> str:
    return f"""# Test migration

- Schema: pcc.harness.migration.v1
- Sequence: {sequence:04d}
- PCC change: {pcc_change}
- Upstream range: {upstream}..{upstream}
- Native-only rationale: not-applicable
- Changed domains: packages/core/session
- Tasks: HARNESS-P0-NATIVE-CORE
- GUI impact: none

## Behavior migrated

- A finite test behavior.

## PCC facilities

- none

## Verification

- PASS | `test command` | Test passed.

## GUI evidence

- NOT-APPLICABLE | No visible GUI behavior changed.

## Remaining boundaries

- none
"""


def _repo(tmp_path: Path, *, with_pending: bool = False) -> tuple[Path, str, Path]:
    repo = tmp_path / "repo"
    commits = repo / "projects" / "harness" / "migration" / "commits"
    commits.mkdir(parents=True)
    board = repo / "docs" / "goal"
    board.mkdir(parents=True)
    (board / "task-board.yaml").write_text(
        "tasks:\n  - id: HARNESS-P0-NATIVE-CORE\n", encoding="utf-8"
    )
    upstream = "a" * 40
    (commits.parent / "upstream.json").write_text(
        json.dumps({"last_audited_commit": upstream}) + "\n", encoding="utf-8"
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "ledger@example.invalid")
    _git(repo, "config", "user.name", "Ledger Test")
    source = repo / "projects" / "harness" / "runtime.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add harness runtime")
    commit = _git(repo, "rev-parse", "HEAD")
    (commits / "0001-runtime.md").write_text(
        _entry(1, commit, upstream), encoding="utf-8"
    )
    _git(repo, "add", str(commits / "0001-runtime.md"))
    _git(repo, "commit", "-m", "record harness ledger")
    if with_pending:
        source.write_text("VALUE = 2\n", encoding="utf-8")
        (commits / "0002-next.md").write_text(
            _entry(2, "pending:next", upstream), encoding="utf-8"
        )
    return repo, commit, commits


def test_repository_ledger_is_current() -> None:
    validator = _load_validator()

    assert validator.validate_ledger(ROOT) == []


def test_valid_ledger_binds_committed_and_pending_implementation(tmp_path: Path) -> None:
    validator = _load_validator()
    repo, _, _ = _repo(tmp_path, with_pending=True)

    assert validator.validate_ledger(repo) == []


def test_rejects_missing_duplicate_out_of_order_and_stale_entries(tmp_path: Path) -> None:
    validator = _load_validator()
    repo, commit, commits = _repo(tmp_path, with_pending=True)
    second = commits / "0002-next.md"

    second.unlink()
    (commits / "0003-next.md").write_text(
        _entry(4, commit, "b" * 40), encoding="utf-8"
    )
    errors = validator.validate_ledger(repo)

    joined = "\n".join(errors)
    assert "out of order" in joined
    assert "Sequence must equal filename sequence" in joined
    assert "stale ledger" in joined
    assert "duplicate PCC change" in joined


def test_rejects_malformed_unknown_task_and_stale_pending(tmp_path: Path) -> None:
    validator = _load_validator()
    repo, _, commits = _repo(tmp_path)
    entry = commits / "0001-runtime.md"
    text = entry.read_text(encoding="utf-8")
    text = text.replace("pcc.harness.migration.v1", "wrong.schema")
    text = text.replace("HARNESS-P0-NATIVE-CORE", "HARNESS-P9-UNKNOWN")
    entry.write_text(text, encoding="utf-8")
    (commits / "0002-stale.md").write_text(
        _entry(2, "pending:stale", "a" * 40), encoding="utf-8"
    )

    joined = "\n".join(validator.validate_ledger(repo))

    assert "Schema must be" in joined
    assert "absent from task-board.yaml" in joined
    assert "stale pending ledger entry" in joined


def test_validator_does_not_open_credential_files(tmp_path: Path, monkeypatch) -> None:
    validator = _load_validator()
    repo, _, _ = _repo(tmp_path, with_pending=True)
    secret = repo / "projects" / "harness" / ".env"
    secret.write_text("DEEPSEEK_API_KEY=must-not-be-read\n", encoding="utf-8")
    original = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        assert path.name != ".env"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    assert validator.validate_ledger(repo) == []
