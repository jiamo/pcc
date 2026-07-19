"""Commit-bound truth manifest for release and milestone claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Sequence

from pcc.macho_normalize import normalize_macho_metadata
from scripts.numpy_first_blocker import ALLOWED_KINDS, evaluate_result

SCHEMA = "pcc.head_truth.v1"
PASS = "PASS"
FAIL = "FAIL"
TIMEOUT = "TIMEOUT"
NOT_RUN = "NOT_RUN"
VALID_GATE_STATUSES = {PASS, FAIL, TIMEOUT, NOT_RUN}
REQUIRED_GATE_IDS = (
    "runtime-archive-preflight",
    "fallback-ratchet",
    "gc-production-contract",
    "llvm-bootstrap",
    "self-five-gc-bootstrap",
    "numpy-core-head",
)

_PYTEST_SUMMARY_RE = re.compile(
    r"(?m)^(?:=+\s*)?"
    r"(?P<summary>"
    r"\d+\s+(?:passed|failed|skipped|xfailed|xpassed|error|errors)"
    r"(?:,\s*\d+\s+(?:passed|failed|skipped|xfailed|xpassed|error|errors))*"
    r"\s+in\s+[0-9.]+s"
    r")(?:\s+\([0-9:]+\))?(?:\s*=+)?$"
)
_PYTEST_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped|xfailed|xpassed|error|errors)"
)


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    suite: str
    command: tuple[str, ...]
    timeout_seconds: int
    kind: str
    backend: str | None = None
    gc_backend: str | None = None


@dataclass
class GateResult:
    gate_id: str
    command: list[str]
    timeout_seconds: int
    kind: str
    status: str
    returncode: int | None
    duration_seconds: float | None
    log_path: str | None
    output_sha256: str | None
    pytest_summary: str | None
    backend: str | None
    gc_backend: str | None
    links_libpython: bool | None = None
    pcc2_pcc3_equal: bool | None = None
    artifact_paths: list[str] = field(default_factory=list)
    observations: list[dict[str, object]] = field(default_factory=list)
    failure: str | None = None


def gate_specs(repo_root: Path) -> tuple[GateSpec, ...]:
    del repo_root
    return (
        GateSpec(
            gate_id="runtime-archive-preflight",
            suite="heavy",
            command=(
                "make",
                "-B",
                "-C",
                "pcc/py_runtime",
                "libpy_runtime_pcc_py.a",
                "PCC=../../.venv/bin/pcc",
                "PYTHON=../../.venv/bin/python3",
            ),
            timeout_seconds=900,
            kind="command",
            backend="self",
        ),
        GateSpec(
            gate_id="fallback-ratchet",
            suite="light",
            command=(
                "uv",
                "run",
                "pytest",
                "-q",
                "-n0",
                "tests/python/test_fallback_baseline.py",
                "tests/python/test_ir_py_fallback_baseline.py",
            ),
            timeout_seconds=420,
            kind="pytest",
        ),
        GateSpec(
            gate_id="control-plane-ratchets",
            suite="light",
            command=(
                "uv",
                "run",
                "pytest",
                "-q",
                "-n0",
                "tests/test_goal_state.py",
                "tests/test_head_truth_manifest.py",
                "tests/test_head_truth_workflows.py",
                "tests/test_numpy_head_gate.py",
                "tests/test_numpy_first_blocker.py",
                "tests/test_gc_bootstrap_xdist_group.py",
                "tests/python/test_pcc_bootstrap_full.py::test_bootstrap_gc_parallel_slots_grouped_files_use_one_slot",
                "tests/python/test_intent_constraints.py::TestObligation1ModeLabeling",
                "tests/python/test_intent_constraints.py::TestObligation3EcosystemGeneric",
                "tests/python/test_intent_constraints.py::TestObligation5FixedPointContract",
                "tests/python/test_intent_constraints.py::TestObligation6FiveGCComparativeStatic",
                "tests/python/test_intent_constraints.py::TestObligation7ValueModelStatic",
            ),
            timeout_seconds=180,
            kind="pytest",
        ),
        GateSpec(
            gate_id="gc-production-contract",
            suite="heavy",
            command=(
                "uv",
                "run",
                "pytest",
                "-q",
                "-n0",
                "tests/python/gc_production_contract",
            ),
            timeout_seconds=420,
            kind="pytest",
            backend="self",
            gc_backend="0..4",
        ),
        GateSpec(
            gate_id="llvm-bootstrap",
            suite="heavy",
            command=(
                "bash",
                "scripts/bootstrap.sh",
                "--backend",
                "llvm",
                "--stage",
                "3",
                "--out-dir",
                "build/head-truth/bootstrap-llvm",
            ),
            timeout_seconds=900,
            kind="bootstrap",
            backend="llvm",
        ),
        GateSpec(
            gate_id="self-five-gc-bootstrap",
            suite="heavy",
            command=(
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/python/gc/test_pcc_bootstrap_full_gc0.py",
                "tests/python/gc/test_pcc_bootstrap_full_gc1.py",
                "tests/python/gc/test_pcc_bootstrap_full_gc2.py",
                "tests/python/gc/test_pcc_bootstrap_full_gc3.py",
                "tests/python/gc/test_pcc_bootstrap_full_gc4.py",
            ),
            timeout_seconds=1800,
            kind="pytest-bootstrap",
            backend="self",
            gc_backend="0..4",
        ),
        GateSpec(
            gate_id="numpy-core-head",
            suite="heavy",
            command=(
                "uv",
                "run",
                "python",
                "scripts/numpy_head_gate.py",
                "run",
                "--source",
                "projects/numpy-2.4.4",
                "--build-root",
                "build/head-truth/numpy-core",
                "--result",
                "build/head-truth/numpy-core/result.json",
            ),
            timeout_seconds=1200,
            kind="command",
            backend="self",
        ),
    )


def _run_git(repo_root: Path, args: Sequence[str]) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        timeout=15,
        check=True,
    )
    return result.stdout


def source_identity(repo_root: Path) -> dict[str, object]:
    commit = _run_git(repo_root, ("rev-parse", "HEAD")).decode().strip()
    status = _run_git(repo_root, ("status", "--porcelain=v1", "-z"))
    diff = _run_git(repo_root, ("diff", "--binary", "HEAD", "--"))
    untracked = _run_git(
        repo_root, ("ls-files", "--others", "--exclude-standard", "-z")
    ).split(b"\0")
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(diff)
    for raw_path in sorted(path for path in untracked if path):
        path = repo_root / os.fsdecode(raw_path)
        digest.update(b"untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
    return {
        "commit": commit,
        "worktree_dirty": bool(status),
        "worktree_fingerprint": digest.hexdigest(),
    }


def platform_identity() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def _pytest_summary(output: str) -> tuple[str | None, dict[str, int]]:
    matches = list(_PYTEST_SUMMARY_RE.finditer(output))
    if not matches:
        return None, {}
    summary = matches[-1].group("summary")
    counts = {
        match.group("label"): int(match.group("count"))
        for match in _PYTEST_COUNT_RE.finditer(summary)
    }
    return summary, counts


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _links_libpython(path: Path) -> bool:
    command = (
        ["otool", "-L", str(path)]
        if platform.system() == "Darwin"
        else [
            "ldd",
            str(path),
        ]
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return "libpython" in output or "Python.framework" in output


def _normalized_equal(left: Path, right: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        left_copy = Path(tmp) / "left"
        right_copy = Path(tmp) / "right"
        shutil.copy2(left, left_copy)
        shutil.copy2(right, right_copy)
        if platform.system() == "Darwin" and shutil.which("codesign"):
            for path in (left_copy, right_copy):
                subprocess.run(
                    ["codesign", "--remove-signature", str(path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                normalize_macho_metadata(path)
        return left_copy.read_bytes() == right_copy.read_bytes()


def _bootstrap_observation(
    repo_root: Path, *, backend: str, gc_backend: str | None, out_dir: Path
) -> dict[str, object]:
    stages = [out_dir / f"pcc{stage}" for stage in (1, 2, 3)]
    artifact_paths = [_relative(path, repo_root) for path in stages]
    missing = [path for path in stages if not path.exists()]
    if missing:
        return {
            "backend": backend,
            "gc_backend": gc_backend,
            "links_libpython": None,
            "pcc2_pcc3_equal": None,
            "artifact_paths": artifact_paths,
            "failure": "missing artifacts: "
            + ", ".join(_relative(path, repo_root) for path in missing),
        }
    return {
        "backend": backend,
        "gc_backend": gc_backend,
        "links_libpython": any(_links_libpython(path) for path in stages),
        "pcc2_pcc3_equal": _normalized_equal(stages[1], stages[2]),
        "artifact_paths": artifact_paths,
        "failure": None,
    }


def inspect_bootstrap_artifacts(
    repo_root: Path, gate_id: str
) -> list[dict[str, object]]:
    if gate_id == "llvm-bootstrap":
        return [
            _bootstrap_observation(
                repo_root,
                backend="llvm",
                gc_backend=None,
                out_dir=repo_root / "build" / "head-truth" / "bootstrap-llvm",
            )
        ]
    if gate_id != "self-five-gc-bootstrap":
        return []
    observations: list[dict[str, object]] = []
    for gc_backend in ("0", "1", "2", "3", "4"):
        suffix = "" if gc_backend == "0" else f"-gc{gc_backend}"
        observations.append(
            _bootstrap_observation(
                repo_root,
                backend="self",
                gc_backend=gc_backend,
                out_dir=repo_root / "build" / f"bootstrap-pytest-self{suffix}",
            )
        )
    return observations


def inspect_numpy_head_artifact(repo_root: Path) -> list[dict[str, object]]:
    result_path = repo_root / "build" / "head-truth" / "numpy-core" / "result.json"
    if not result_path.is_file():
        return [
            {
                "backend": "self",
                "gc_backend": None,
                "links_libpython": None,
                "pcc2_pcc3_equal": None,
                "artifact_paths": [_relative(result_path, repo_root)],
                "failure": f"missing NumPy gate result: {_relative(result_path, repo_root)}",
            }
        ]
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            {
                "backend": "self",
                "gc_backend": None,
                "links_libpython": None,
                "pcc2_pcc3_equal": None,
                "artifact_paths": [_relative(result_path, repo_root)],
                "failure": f"invalid NumPy gate result: {exc}",
            }
        ]
    if not isinstance(value, dict):
        value = {}
    loader = value.get("loader") if isinstance(value.get("loader"), dict) else {}
    link = value.get("link") if isinstance(value.get("link"), dict) else {}
    blocker_ratchet = evaluate_result(value, "numpy-core-head")
    recorded_ratchet = value.get("first_blocker_ratchet")
    artifacts = (
        value.get("artifacts") if isinstance(value.get("artifacts"), list) else []
    )
    artifact_paths = [str(item) for item in artifacts]
    if _relative(result_path, repo_root) not in artifact_paths:
        artifact_paths.append(_relative(result_path, repo_root))
    failure: str | None = None
    if value.get("schema") != "pcc.numpy-head-gate.v1":
        failure = f"unexpected NumPy gate schema {value.get('schema')!r}"
    elif value.get("status") != PASS:
        failure = str(value.get("failure") or "NumPy gate status is not PASS")
    elif link.get("links_libpython") is not False:
        failure = "NumPy pcc-native artifact links libpython"
    elif loader.get("entered_pyinit") is not True:
        failure = "NumPy gate did not enter PyInit"
    elif loader.get("entered_py_mod_exec") is not True:
        failure = "NumPy gate did not enter Py_mod_exec"
    elif recorded_ratchet != blocker_ratchet:
        failure = "NumPy gate did not record the current first-blocker ratchet"
    elif blocker_ratchet.get("accepted") is not True:
        failure = "NumPy first-blocker ratchet rejected the gate result"
    return [
        {
            "backend": "self",
            "gc_backend": None,
            "links_libpython": link.get("links_libpython"),
            "pcc2_pcc3_equal": None,
            "artifact_paths": artifact_paths,
            "source": value.get("source"),
            "compile": value.get("compile"),
            "link": link,
            "first_blocker": loader.get("first_blocker"),
            "first_blocker_ratchet": blocker_ratchet,
            "entered_pyinit": loader.get("entered_pyinit"),
            "entered_py_mod_exec": loader.get("entered_py_mod_exec"),
            "failure": failure,
        }
    ]


def not_run_result(spec: GateSpec) -> GateResult:
    return GateResult(
        gate_id=spec.gate_id,
        command=list(spec.command),
        timeout_seconds=spec.timeout_seconds,
        kind=spec.kind,
        status=NOT_RUN,
        returncode=None,
        duration_seconds=None,
        log_path=None,
        output_sha256=None,
        pytest_summary=None,
        backend=spec.backend,
        gc_backend=spec.gc_backend,
    )


def run_gate(
    repo_root: Path,
    artifacts_root: Path,
    spec: GateSpec,
    process_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> GateResult:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    started = time.monotonic()
    process = process_runner(
        spec.command,
        cwd=repo_root,
        env=env,
        timeout=spec.timeout_seconds,
    )
    duration = time.monotonic() - started
    output = (process.stdout or "") + (process.stderr or "")
    artifacts_root.mkdir(parents=True, exist_ok=True)
    log_path = artifacts_root / f"{spec.gate_id}.log"
    log_path.write_text(output, encoding="utf-8")
    summary, counts = _pytest_summary(output)

    failure: str | None = None
    if process.returncode == 124:
        status = TIMEOUT
        failure = f"command timed out after {spec.timeout_seconds}s"
    elif process.returncode != 0:
        status = FAIL
        failure = f"command exited {process.returncode}"
    elif spec.kind.startswith("pytest") and summary is None:
        status = FAIL
        failure = "pytest process exited zero without a final summary"
    elif spec.kind.startswith("pytest") and (
        counts.get("passed", 0) == 0
        or counts.get("failed", 0) > 0
        or counts.get("error", 0) > 0
        or counts.get("errors", 0) > 0
        or counts.get("skipped", 0) > 0
        or counts.get("xfailed", 0) > 0
    ):
        status = FAIL
        failure = f"pytest summary is not strict green: {summary}"
    else:
        status = PASS

    observations = inspect_bootstrap_artifacts(repo_root, spec.gate_id)
    if spec.gate_id == "numpy-core-head":
        observations = inspect_numpy_head_artifact(repo_root)
    if observations:
        if spec.gate_id in {"llvm-bootstrap", "self-five-gc-bootstrap"}:
            observation_failure = next(
                (
                    str(observation.get("failure"))
                    for observation in observations
                    if observation.get("failure")
                    or observation.get("links_libpython") is not False
                    or observation.get("pcc2_pcc3_equal") is not True
                ),
                None,
            )
        else:
            observation_failure = next(
                (
                    str(observation.get("failure"))
                    for observation in observations
                    if observation.get("failure")
                ),
                None,
            )
        if observation_failure is not None and status != TIMEOUT:
            status = FAIL
            failure = observation_failure or "bootstrap artifact inspection failed"

    artifact_paths = [
        str(path)
        for observation in observations
        for path in observation.get("artifact_paths", [])
    ]
    links_libpython = (
        any(bool(observation["links_libpython"]) for observation in observations)
        if observations
        else None
    )
    pcc2_pcc3_equal = (
        all(observation["pcc2_pcc3_equal"] is True for observation in observations)
        if observations and spec.gate_id in {"llvm-bootstrap", "self-five-gc-bootstrap"}
        else None
    )
    return GateResult(
        gate_id=spec.gate_id,
        command=list(spec.command),
        timeout_seconds=spec.timeout_seconds,
        kind=spec.kind,
        status=status,
        returncode=process.returncode,
        duration_seconds=round(duration, 3),
        log_path=_relative(log_path, repo_root),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        pytest_summary=summary,
        backend=spec.backend,
        gc_backend=spec.gc_backend,
        links_libpython=links_libpython,
        pcc2_pcc3_equal=pcc2_pcc3_equal,
        artifact_paths=artifact_paths,
        observations=observations,
        failure=failure,
    )


def build_manifest(
    *,
    source: dict[str, object],
    platform_info: dict[str, str],
    results: Sequence[GateResult],
    generated_at: str | None = None,
) -> dict[str, object]:
    result_by_id = {result.gate_id: result for result in results}
    complete = all(
        result_by_id.get(gate_id) is not None and result_by_id[gate_id].status == PASS
        for gate_id in REQUIRED_GATE_IDS
    )
    return {
        "schema": SCHEMA,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "platform": platform_info,
        "required_gate_ids": list(REQUIRED_GATE_IDS),
        "complete": complete,
        "claimable_commit": complete and not bool(source.get("worktree_dirty")),
        "gates": [asdict(result) for result in results],
    }


def validate_manifest(
    manifest: dict[str, object], *, require_complete: bool = False
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errors.append(f"unexpected schema {manifest.get('schema')!r}")
    source = manifest.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        commit = source.get("commit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            errors.append("source.commit must be a full lowercase Git SHA")
        if not isinstance(source.get("worktree_dirty"), bool):
            errors.append("source.worktree_dirty must be boolean")
        fingerprint = source.get("worktree_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        ):
            errors.append("source.worktree_fingerprint must be SHA-256")
    platform_info = manifest.get("platform")
    if not isinstance(platform_info, dict) or any(
        not isinstance(platform_info.get(key), str)
        for key in ("system", "release", "machine", "python")
    ):
        errors.append("platform must record system, release, machine, and python")
    gates = manifest.get("gates")
    if not isinstance(gates, list):
        errors.append("gates must be a list")
        gates = []
    gate_by_id: dict[str, dict[str, object]] = {}
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            errors.append(f"gate #{index + 1} must be an object")
            continue
        gate_id = gate.get("gate_id")
        if not isinstance(gate_id, str) or not gate_id:
            errors.append(f"gate #{index + 1} has no gate_id")
            continue
        if gate_id in gate_by_id:
            errors.append(f"duplicate gate {gate_id}")
        gate_by_id[gate_id] = gate
        status = gate.get("status")
        if status not in VALID_GATE_STATUSES:
            errors.append(f"{gate_id}: invalid status {status!r}")
        if status == PASS:
            if gate.get("returncode") != 0:
                errors.append(f"{gate_id}: PASS requires returncode 0")
            if not isinstance(gate.get("duration_seconds"), (int, float)):
                errors.append(f"{gate_id}: PASS requires duration_seconds")
            if str(gate.get("kind", "")).startswith("pytest") and not gate.get(
                "pytest_summary"
            ):
                errors.append(f"{gate_id}: pytest PASS requires a final summary")
            if gate_id in {"llvm-bootstrap", "self-five-gc-bootstrap"}:
                if gate.get("links_libpython") is not False:
                    errors.append(f"{gate_id}: PASS requires links_libpython=false")
                if gate.get("pcc2_pcc3_equal") is not True:
                    errors.append(f"{gate_id}: PASS requires pcc2_pcc3_equal=true")
            if (
                gate_id == "numpy-core-head"
                and gate.get("links_libpython") is not False
            ):
                errors.append("numpy-core-head: PASS requires links_libpython=false")
            if gate_id == "numpy-core-head":
                observations = gate.get("observations")
                if not isinstance(observations, list) or len(observations) != 1:
                    errors.append(
                        "numpy-core-head: PASS requires exactly one artifact observation"
                    )
                else:
                    observation = observations[0]
                    blocker = (
                        observation.get("first_blocker")
                        if isinstance(observation, dict)
                        else None
                    )
                    ratchet = (
                        observation.get("first_blocker_ratchet")
                        if isinstance(observation, dict)
                        else None
                    )
                    if (
                        not isinstance(observation, dict)
                        or "first_blocker" not in observation
                        or (
                            blocker is not None
                            and (
                                not isinstance(blocker, dict)
                                or blocker.get("kind") not in ALLOWED_KINDS
                            )
                        )
                    ):
                        errors.append(
                            "numpy-core-head: PASS requires a classified first blocker "
                            "or an explicit empty completion record"
                        )
                    if (
                        not isinstance(ratchet, dict)
                        or ratchet.get("accepted") is not True
                    ):
                        errors.append(
                            "numpy-core-head: PASS requires an accepted first-blocker ratchet"
                        )
    if require_complete:
        for gate_id in REQUIRED_GATE_IDS:
            gate = gate_by_id.get(gate_id)
            if gate is None or gate.get("status") != PASS:
                errors.append(f"required gate {gate_id} is not PASS")
        if manifest.get("complete") is not True:
            errors.append("complete manifest required")
    return errors


def serialize_manifest(manifest: dict[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_manifest(manifest), encoding="utf-8")


def not_run_results(repo_root: Path) -> list[GateResult]:
    return [not_run_result(spec) for spec in gate_specs(repo_root)]


def selected_gate_failures(
    results: Sequence[GateResult], selected_gate_ids: set[str]
) -> list[str]:
    return [
        f"selected gate {result.gate_id} is {result.status}"
        for result in results
        if result.gate_id in selected_gate_ids and result.status != PASS
    ]
