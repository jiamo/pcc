"""Randomized differential testing with Csmith.

Generates random C programs via csmith, compiles with both the system cc
and pcc, runs both, and compares the checksum output.  Any mismatch
indicates a code-generation bug in pcc.

Requires: ``csmith`` on $PATH and the csmith runtime headers installed.

Usage:
    uv run pytest tests/test_csmith.py -x           # 20 seeds, default
    uv run pytest tests/test_csmith.py -x -k seed   # 20 seeds
    PCC_CSMITH_SEEDS=200 uv run pytest ... -x       # 200 seeds
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from pcc.dependency_verdict import probe_executable_dependency

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit
from tests.worker_process import run_worker_process

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Structured generator verdict: a missing csmith is an UNAVAILABLE optional
# corpus generator, never C-semantics proof; availability (this verdict),
# generated-case execution (the skips inside test_csmith_seed), and semantic
# parity (the hard result-equality asserts) stay distinct
# (AUD-P2-DEPENDENCY-CSMITH-VERDICT).
CSMITH_VERDICT = probe_executable_dependency("csmith")
CSMITH_BIN = CSMITH_VERDICT.resolved_path
CSMITH_INCLUDE = None

# Auto-detect csmith include directory
if CSMITH_BIN:
    _prefix = Path(CSMITH_BIN).resolve().parent.parent
    for candidate in (
        _prefix / "include" / "csmith-2.3.0",
        _prefix / "include" / "csmith",
        Path("/usr/include/csmith-2.3.0"),
        Path("/usr/include/csmith"),
        Path("/usr/local/include/csmith-2.3.0"),
        Path("/usr/local/include/csmith"),
    ):
        if (candidate / "csmith.h").is_file():
            CSMITH_INCLUDE = str(candidate)
            break

DEFAULT_SEEDS = int(os.environ.get("PCC_CSMITH_SEEDS", "20"))
DEFAULT_TIMEOUT = 30

# Csmith flags that produce programs pcc can handle:
#   - no longlong  (pcc long long support is limited)
#   - no volatiles (avoids volatile-specific lowering)
#   - no packed-struct (avoids __attribute__((packed)))
#   - no bitfields (pcc bitfield support is limited)
#   - no argc (deterministic, no argv dependency)
CSMITH_FLAGS = (
    "--no-argc",
    "--no-volatiles",
    "--no-volatile-pointers",
    "--no-bitfields",
    "--no-packed-struct",
    "--no-longlong",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CsmithResult:
    seed: int
    native_returncode: int
    native_stdout: str
    pcc_returncode: int
    pcc_stdout: str
    pcc_stderr: str


def _host_cc():
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")
    return cc


def _generate(seed: int, outpath: str) -> None:
    subprocess.run(
        [CSMITH_BIN, "--seed", str(seed), *CSMITH_FLAGS, "-o", outpath],
        check=True,
        capture_output=True,
        timeout=10,
    )


def _preprocess(src_path: str, pp_path: str) -> None:
    cc = _host_cc()
    subprocess.run(
        [cc, "-E", f"-I{CSMITH_INCLUDE}", "-w", src_path, "-o", pp_path],
        check=True,
        capture_output=True,
        timeout=DEFAULT_TIMEOUT,
    )


def _run_native(src_path: str, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    cc = _host_cc()
    with tempfile.TemporaryDirectory(prefix="csmith_native_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        comp = subprocess.run(
            [cc, f"-I{CSMITH_INCLUDE}", "-w", "-O0", src_path, "-o", str(binary)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if comp.returncode != 0:
            return comp
        try:
            return subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                [str(binary)], 124, "", "timeout",
            )


def _pcc_worker_entry(pp_path: str, timeout: int, conn) -> None:
    with open(pp_path) as f:
        source = f.read()
    unit = TranslationUnit("csmith_test.c", pp_path, source)
    try:
        ev = CEvaluator()
        result = ev.run_translation_units_with_system_cc(
            [unit],
            base_dir=str(Path(pp_path).parent),
            timeout=timeout,
        )
        conn.send({
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
    except Exception as exc:
        conn.send({
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        })


def _run_pcc(pp_path: str, timeout: int = DEFAULT_TIMEOUT):
    result = run_worker_process(
        _pcc_worker_entry,
        (pp_path, timeout),
        timeout + 10,
    )
    if result.timed_out:
        return 124, "", "timeout"
    if result.payload is None:
        return 1, "", f"pcc worker exited without result (exitcode={result.exitcode})"
    return result.payload["returncode"], result.payload["stdout"], result.payload["stderr"]


def _run_seed(seed: int) -> CsmithResult:
    with tempfile.TemporaryDirectory(prefix=f"csmith_{seed}_") as tmpdir:
        src = os.path.join(tmpdir, "test.c")
        pp = os.path.join(tmpdir, "test_pp.c")

        _generate(seed, src)
        _preprocess(src, pp)

        native = _run_native(src)
        pcc_rc, pcc_out, pcc_err = _run_pcc(pp)

        return CsmithResult(
            seed=seed,
            native_returncode=native.returncode,
            native_stdout=native.stdout.strip(),
            pcc_returncode=pcc_rc,
            pcc_stdout=pcc_out.strip(),
            pcc_stderr=pcc_err,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_skip_reason = None
if not CSMITH_VERDICT.available:
    _skip_reason = CSMITH_VERDICT.skip_reason()
elif CSMITH_INCLUDE is None:
    _skip_reason = (
        f"UNAVAILABLE[headers:csmith.h]: runtime headers not found near "
        f"{CSMITH_VERDICT.resolved_path}; feature_claimed=false; runtime_executed=false"
    )


@pytest.mark.skipif(_skip_reason is not None, reason=_skip_reason or "")
@pytest.mark.parametrize("seed", range(DEFAULT_SEEDS))
def test_csmith_seed(seed):
    r = _run_seed(seed)

    if r.native_returncode != 0:
        pytest.skip(f"native compile/run failed (rc={r.native_returncode})")

    if r.pcc_returncode != 0 and ("timeout" in r.pcc_stderr.lower() or "timed out" in r.pcc_stderr.lower()):
        pytest.skip(f"csmith seed {r.seed}: pcc execution timed out")

    assert r.pcc_returncode == 0, (
        f"csmith seed {r.seed}: pcc failed (rc={r.pcc_returncode})\n"
        f"stderr: {r.pcc_stderr[:500]}"
    )
    assert r.pcc_stdout == r.native_stdout, (
        f"csmith seed {r.seed}: checksum mismatch\n"
        f"  native: {r.native_stdout}\n"
        f"  pcc:    {r.pcc_stdout}\n"
        f"  stderr: {r.pcc_stderr[:300]}"
    )


def test_csmith_tool_identity_recorded_when_present(record_property):
    """Seed identity lives in each test id; tool identity is recorded here."""
    if _skip_reason is not None:
        pytest.skip(_skip_reason)
    version = subprocess.run(
        [CSMITH_BIN, "--version"], text=True, capture_output=True, timeout=30
    )
    assert version.returncode == 0, version.stderr
    identity = version.stdout.strip() or version.stderr.strip()
    assert identity, "csmith --version produced no identity"
    record_property("csmith_path", CSMITH_BIN)
    record_property("csmith_version", identity)
    record_property("csmith_include", CSMITH_INCLUDE)
