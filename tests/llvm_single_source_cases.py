"""Pinned llvm-test-suite SingleSource corpus contract.

The loader is deliberately strict.  A path, source byte, license, feature, or
budget change requires an explicit manifest review instead of silently
expanding an integration gate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


SCHEMA = "pcc-llvm-test-suite-single-source.v1"
UPSTREAM_REPOSITORY = "https://github.com/llvm/llvm-test-suite"
REQUIRED_FEATURES = frozenset(
    {"signedness", "aggregates", "function_pointers", "varargs"}
)
MAX_CASES = 8
MAX_WALL_TIME_BUDGET_SECONDS = 120


@dataclass(frozen=True)
class LLVMTestSuiteCase:
    relative_path: str
    source_path: Path
    sha256: str
    features: tuple[str, ...]


@dataclass(frozen=True)
class LLVMTestSuiteCorpus:
    commit: str
    root: Path
    wall_time_budget_seconds: int
    case_timeout_seconds: int
    cases: tuple[LLVMTestSuiteCase, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_bounded_int(value: object, *, field: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ValueError(f"{field} must be an integer in 1..{maximum}")
    return value


def _repository_relative_path(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{field} must stay inside the repository")
    return path


def load_llvm_single_source_corpus(
    repo_root: Path, manifest_path: Path
) -> LLVMTestSuiteCorpus:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"unsupported llvm SingleSource schema: {raw.get('schema')!r}")

    upstream = raw.get("upstream")
    if not isinstance(upstream, dict):
        raise ValueError("upstream must be an object")
    if upstream.get("repository") != UPSTREAM_REPOSITORY:
        raise ValueError("llvm SingleSource repository is not the pinned upstream")
    commit = upstream.get("commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(ch not in "0123456789abcdef" for ch in commit)
    ):
        raise ValueError("upstream.commit must be a full lowercase git object id")
    if upstream.get("license") != "Apache-2.0 WITH LLVM-exception":
        raise ValueError("unexpected llvm-test-suite license label")

    license_relative = upstream.get("license_path")
    license_digest = upstream.get("license_sha256")
    if not isinstance(license_digest, str):
        raise ValueError("license path and digest must be pinned")
    license_path = repo_root / _repository_relative_path(
        license_relative, field="upstream.license_path"
    )
    if not license_path.is_file() or _sha256(license_path) != license_digest:
        raise ValueError("llvm-test-suite license is missing or changed")
    license_text = license_path.read_text(encoding="utf-8")
    if "Apache License" not in license_text or "LLVM Exceptions" not in license_text:
        raise ValueError("llvm-test-suite license omits required terms")

    corpus_relative = _repository_relative_path(
        raw.get("corpus_root"), field="corpus_root"
    )
    root = repo_root / corpus_relative
    if not root.is_dir():
        raise ValueError(f"missing llvm SingleSource corpus root: {root}")

    budget = _positive_bounded_int(
        raw.get("wall_time_budget_seconds"),
        field="wall_time_budget_seconds",
        maximum=MAX_WALL_TIME_BUDGET_SECONDS,
    )
    case_timeout = _positive_bounded_int(
        raw.get("case_timeout_seconds"),
        field="case_timeout_seconds",
        maximum=budget,
    )

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > MAX_CASES:
        raise ValueError(f"cases must contain 1..{MAX_CASES} entries")

    seen_paths: set[str] = set()
    covered_features: set[str] = set()
    cases: list[LLVMTestSuiteCase] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("each case must be an object")
        relative = item.get("path")
        digest = item.get("sha256")
        features = item.get("features")
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ValueError("each case needs path and sha256 strings")
        pure_path = PurePosixPath(relative)
        if (
            pure_path.is_absolute()
            or ".." in pure_path.parts
            or pure_path.suffix != ".c"
            or not relative.startswith("SingleSource/")
            or relative.startswith("SingleSource/Benchmarks/")
        ):
            raise ValueError(f"case is outside the bounded SingleSource tests: {relative}")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"case has invalid sha256: {relative}")
        if relative in seen_paths:
            raise ValueError(f"duplicate llvm SingleSource case: {relative}")
        seen_paths.add(relative)
        if (
            not isinstance(features, list)
            or not features
            or any(feature not in REQUIRED_FEATURES for feature in features)
        ):
            raise ValueError(f"case has invalid features: {relative}")
        source_path = root / pure_path
        if not source_path.is_file() or _sha256(source_path) != digest:
            raise ValueError(f"llvm SingleSource case is missing or changed: {relative}")
        feature_tuple = tuple(features)
        if len(feature_tuple) != len(set(feature_tuple)):
            raise ValueError(f"case has duplicate features: {relative}")
        covered_features.update(feature_tuple)
        cases.append(
            LLVMTestSuiteCase(
                relative_path=relative,
                source_path=source_path,
                sha256=digest,
                features=feature_tuple,
            )
        )

    actual_sources = {
        path.relative_to(root).as_posix() for path in root.rglob("*.c")
    }
    if actual_sources != seen_paths:
        missing = sorted(seen_paths - actual_sources)
        unpinned = sorted(actual_sources - seen_paths)
        raise ValueError(f"corpus/manifest mismatch: missing={missing}, unpinned={unpinned}")
    if covered_features != REQUIRED_FEATURES:
        raise ValueError(
            "llvm SingleSource feature coverage mismatch: "
            f"expected={sorted(REQUIRED_FEATURES)}, actual={sorted(covered_features)}"
        )

    return LLVMTestSuiteCorpus(
        commit=commit,
        root=root,
        wall_time_budget_seconds=budget,
        case_timeout_seconds=case_timeout,
        cases=tuple(cases),
    )
