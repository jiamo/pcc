#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    name: str
    pytest_args: tuple[str, ...]
    timeout_seconds: int
    description: str


GATES: dict[str, Gate] = {
    "self-unit": Gate(
        name="self-unit",
        pytest_args=("tests/test_self_backend.py", "-q", "-n0"),
        timeout_seconds=300,
        description="focused self backend unit/IR/emitter coverage",
    ),
    "c-testsuite": Gate(
        name="c-testsuite",
        pytest_args=("tests/test_c_testsuite_self.py", "-q", "-n0"),
        timeout_seconds=1200,
        description="full c-testsuite self/native and self/LLVM broad gate",
    ),
    "gcc-torture": Gate(
        name="gcc-torture",
        pytest_args=("tests/test_gcc_torture_self.py", "-q", "-n0"),
        timeout_seconds=3600,
        description="full gcc-torture self/native and self/LLVM formal gate",
    ),
    "zlib": Gate(
        name="zlib",
        pytest_args=(
            "tests/test_zlib.py::test_zlib_runtime_with_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="strict real-workload self gate with emitter-call assertion",
    ),
    "lz4": Gate(
        name="lz4",
        pytest_args=(
            "tests/test_lz4.py::test_lz4_runtime_with_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="lz4 self backend system-link runtime gate",
    ),
    "zstd": Gate(
        name="zstd",
        pytest_args=(
            "tests/test_zstd.py::test_zstd_runtime_with_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="zstd self backend system-link runtime gate",
    ),
    "pcre": Gate(
        name="pcre",
        pytest_args=(
            "tests/test_pcre.py::test_pcre_self_backend_runtime_with_system_link",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="pcre self backend system-link runtime gate",
    ),
    "openssl": Gate(
        name="openssl",
        pytest_args=(
            "tests/test_openssl.py::test_openssl_runtime_with_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="openssl smoke self backend system-link runtime gate",
    ),
    "readline": Gate(
        name="readline",
        pytest_args=(
            "tests/test_readline.py::test_readline_runtime_with_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=300,
        description="readline smoke self backend system-link runtime gate",
    ),
    "postgres-zlib": Gate(
        name="postgres-zlib",
        pytest_args=(
            "-m",
            "integration",
            "tests/test_postgres.py::test_postgres_runtime_with_self_backend_system_link_depends_on_repo_local_zlib_project",
            "-q",
            "-n0",
        ),
        timeout_seconds=600,
        description="postgres libpq client slice self backend runtime gate",
    ),
    "postgres-cli": Gate(
        name="postgres-cli",
        pytest_args=(
            "-m",
            "integration",
            "tests/test_postgres.py::test_postgres_cli_self_backend_system_link_depends_on",
            "-q",
            "-n0",
        ),
        timeout_seconds=600,
        description="postgres CLI --backend=self --system-link gate",
    ),
}


TIER_GATES: dict[str, tuple[str, ...]] = {
    "quick": ("self-unit", "zlib"),
    "broad": ("self-unit", "c-testsuite", "gcc-torture"),
    "workloads": ("zlib", "lz4", "zstd", "pcre", "openssl", "readline"),
    "full": (
        "self-unit",
        "c-testsuite",
        "gcc-torture",
        "zlib",
        "lz4",
        "zstd",
        "pcre",
        "openssl",
        "readline",
    ),
}


POSTGRES_GATES = ("postgres-zlib", "postgres-cli")


def _supported_host() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _ordered_unique(names: tuple[str, ...]) -> list[str]:
    seen = set()
    ordered = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _selected_gate_names(args: argparse.Namespace) -> list[str]:
    if args.only:
        names = tuple(args.only)
    else:
        names = TIER_GATES[args.tier]
        if args.include_postgres:
            names = (*names, *POSTGRES_GATES)

    unknown = [name for name in names if name not in GATES]
    if unknown:
        known = ", ".join(sorted(GATES))
        raise SystemExit(f"unknown gate(s): {', '.join(unknown)}; known gates: {known}")
    return _ordered_unique(names)


def _run_gate(gate: Gate, *, dry_run: bool) -> int:
    cmd = ("uv", "run", "pytest", *gate.pytest_args)
    print(f"\n== {gate.name}: {gate.description}", flush=True)
    print("+ " + " ".join(cmd), flush=True)
    if dry_run:
        return 0

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=_repo_root(),
            env=_child_env(),
            timeout=gate.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        print(
            f"gate {gate.name!r} timed out after {elapsed:.1f}s "
            f"(limit {gate.timeout_seconds}s)",
            file=sys.stderr,
        )
        return 124

    elapsed = time.monotonic() - start
    print(
        f"== {gate.name}: exit={result.returncode} elapsed={elapsed:.1f}s", flush=True
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the supported-host macOS arm64 self-backend promotion gate. "
            "The full tier is intentionally expensive."
        )
    )
    parser.add_argument(
        "--tier",
        choices=sorted(TIER_GATES),
        default="full",
        help="gate tier to run; default: full",
    )
    parser.add_argument(
        "--only",
        choices=sorted(GATES),
        action="append",
        help="run only this gate name; may be repeated",
    )
    parser.add_argument(
        "--include-postgres",
        action="store_true",
        help="include postgres integration gates in addition to the selected tier",
    )
    parser.add_argument(
        "--allow-non-supported-host",
        action="store_true",
        help="run even when the host is not macOS arm64",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print selected commands without executing them",
    )
    args = parser.parse_args(argv)

    if not args.allow_non_supported_host and not _supported_host():
        print(
            "self-backend promotion gate is defined for the supported macOS "
            "arm64 host; pass --allow-non-supported-host to override",
            file=sys.stderr,
        )
        return 2

    gate_names = _selected_gate_names(args)
    print("self backend promotion gate", flush=True)
    print(f"host={platform.system()} {platform.machine()}", flush=True)
    print("gates=" + ", ".join(gate_names), flush=True)

    for name in gate_names:
        code = _run_gate(GATES[name], dry_run=args.dry_run)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
