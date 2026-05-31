from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_FRESHNESS_SUFFIXES = {".py", ".c", ".h"}
_IGNORED_FRESHNESS_DIRS = {"__pycache__", "build", "build_py"}


def _normalize_repo_root(repo: Path) -> Path:
    if (repo / "pcc" / "__main__.py").exists():
        return repo
    nested = repo / "pcc"
    if (nested / "pcc" / "__main__.py").exists():
        return nested
    return repo


def _is_pcc1_freshness_source(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix not in _FRESHNESS_SUFFIXES:
        return False
    if any(part in _IGNORED_FRESHNESS_DIRS for part in path.parts):
        return False
    return True


def pcc1_freshness_sources(repo: Path) -> tuple[Path, ...]:
    repo = _normalize_repo_root(repo)
    pcc_dir = repo / "pcc"
    sources: list[Path] = []
    for suffix in _FRESHNESS_SUFFIXES:
        pattern = "*" + suffix
        sources.extend(pcc_dir.rglob(pattern))
    return tuple(sorted(path for path in sources if _is_pcc1_freshness_source(path)))


def pcc1_freshness_cutoff(repo: Path) -> float:
    mtimes = [
        source.stat().st_mtime
        for source in pcc1_freshness_sources(repo)
        if source.exists()
    ]
    if not mtimes:
        return 0.0
    return max(mtimes)


def current_pcc1_candidates(repo: Path) -> tuple[Path, ...]:
    repo = _normalize_repo_root(repo)
    fixed = (
        repo / "pcc1",
        repo / "build" / "bootstrap-pytest-self" / "pcc1",
        repo / "build" / "bootstrap" / "pcc1",
        repo / "build" / "bootstrap-self-claude" / "pcc1",
        repo / "build" / "bootstrap-llvm-claude" / "pcc1",
        repo / "build" / "bootstrap-strict-self" / "pcc1",
        repo / "build" / "bootstrap-self-darwin_arm64" / "pcc1",
        repo / "build" / "bootstrap-llvm-darwin_arm64" / "pcc1",
    )
    dynamic = tuple(
        sorted(
            (repo / "build").glob("bootstrap-*/pcc1"),
            key=lambda path: (path.stat().st_mtime, str(path)),
            reverse=True,
        )
    )
    seen: set[Path] = set()
    candidates: list[Path] = []
    for candidate in fixed + dynamic:
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(candidates)


def find_current_pcc1(repo: Path) -> Path | None:
    repo = _normalize_repo_root(repo)
    explicit = os.environ.get("PCC_CURRENT_PCC1")
    if explicit:
        raw = Path(explicit)
        candidates = (raw,) if raw.is_absolute() else (raw, repo / raw)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    source_mtime = pcc1_freshness_cutoff(repo)
    candidates = current_pcc1_candidates(repo)
    if os.environ.get("PCC_DEBUG_PCC1_GATE"):
        try:
            sys.stderr.write(
                "PCC1_GATE repo="
                + str(repo)
                + " source_mtime="
                + str(source_mtime)
                + "\n"
            )
            for candidate in candidates:
                if candidate.exists():
                    sys.stderr.write(
                        "PCC1_GATE candidate="
                        + str(candidate)
                        + " mtime="
                        + str(candidate.stat().st_mtime)
                        + "\n"
                    )
        except Exception:
            pass
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_mtime >= source_mtime:
            return candidate
    return None


def require_current_pcc1_enabled() -> bool:
    return os.environ.get("PCC_REQUIRE_CURRENT_PCC1") == "1"


def skip_or_fail_no_current_pcc1(reason: str) -> None:
    if require_current_pcc1_enabled():
        pytest.fail(
            reason
            + "; PCC_REQUIRE_CURRENT_PCC1=1 makes pcc1 package parity a hard gate"
        )
    pytest.skip(reason)
