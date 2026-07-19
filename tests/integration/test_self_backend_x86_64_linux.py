from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
DOCKER_HARNESS = REPO_ROOT / "scripts" / "run_self_backend_linux_x86_64_docker.sh"
CTESTSUITE_HARNESS = REPO_ROOT / "scripts" / "run_self_backend_linux_x86_64_c_testsuite.py"
X86_64_LINUX_TRIPLE = "x86_64-unknown-linux-gnu"
X86_64_LINUX_ALIAS_TRIPLE = "amd64-pc-linux-gnu"
X86_64_LINUX_BUCKET_SIZE = 128
DOCKER_PROBE_TIMEOUT_SECONDS = 5
DOCKER_HARNESS_TIMEOUT_SECONDS = 900

pytestmark = pytest.mark.integration


@lru_cache(maxsize=1)
def _docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None or not DOCKER_HARNESS.is_file():
        return False
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _run_linux_x86_64_harness(shell_script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DOCKER_HARNESS), "bash", "-lc", shell_script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=DOCKER_HARNESS_TIMEOUT_SECONDS,
    )


def test_docker_availability_requires_reachable_daemon(monkeypatch):
    calls = []

    def unavailable_daemon(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 1)

    _docker_available.cache_clear()
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/docker")
    monkeypatch.setattr(subprocess, "run", unavailable_daemon)

    assert _docker_available() is False
    assert calls[0][0][0] == [
        "/fake/docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    ]
    assert calls[0][1]["timeout"] == DOCKER_PROBE_TIMEOUT_SECONDS
    _docker_available.cache_clear()


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_llvm_smoke_can_build_and_run():
    result = _run_linux_x86_64_harness(
        rf"""
set -euo pipefail
cat >/tmp/self_backend_linux_smoke.c <<'EOF'
int main(void) {{ return 0; }}
EOF
env -u LC_ALL uv run pcc --target {X86_64_LINUX_TRIPLE} --emit-obj /tmp/self_backend_linux_smoke.o /tmp/self_backend_linux_smoke.c
cc -no-pie /tmp/self_backend_linux_smoke.o -o /tmp/self_backend_linux_smoke
/tmp/self_backend_linux_smoke
"""
    )

    assert result.returncode == 0, (
        "linux x86_64 docker llvm smoke failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_self_backend_smoke_can_build_and_run():
    result = _run_linux_x86_64_harness(
        rf"""
set -euo pipefail
cat >/tmp/self_backend_linux_self_smoke.c <<'EOF'
int main(void) {{ return 42; }}
EOF
env -u LC_ALL uv run pcc --backend=self --target {X86_64_LINUX_TRIPLE} --emit-obj /tmp/self_backend_linux_self_smoke.o /tmp/self_backend_linux_self_smoke.c
cc -no-pie /tmp/self_backend_linux_self_smoke.o -o /tmp/self_backend_linux_self_smoke
/tmp/self_backend_linux_self_smoke
"""
    )

    assert result.returncode == 42, (
        "linux x86_64 docker self-backend smoke failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_self_backend_smoke_supports_amd64_alias_triple():
    result = _run_linux_x86_64_harness(
        rf"""
set -euo pipefail
cat >/tmp/self_backend_linux_unsupported_alias.c <<'EOF'
int main(void) {{ return 7; }}
EOF
env -u LC_ALL uv run pcc --backend=self --target {X86_64_LINUX_ALIAS_TRIPLE} --emit-obj /tmp/self_backend_linux_unsupported_alias.o /tmp/self_backend_linux_unsupported_alias.c
cc -no-pie /tmp/self_backend_linux_unsupported_alias.o -o /tmp/self_backend_linux_unsupported_alias
/tmp/self_backend_linux_unsupported_alias
"""
    )

    assert result.returncode == 7, (
        "linux x86_64 docker self-backend amd64-alias smoke failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_self_backend_direct_call_and_binop_smoke():
    result = _run_linux_x86_64_harness(
        rf"""
set -euo pipefail
cat >/tmp/self_backend_linux_direct_call.c <<'EOF'
int add(int a, int b) {{ return a + b; }}
int main(void) {{ return add(40, 2); }}
EOF
env -u LC_ALL uv run pcc --backend=self --target {X86_64_LINUX_TRIPLE} --emit-obj /tmp/self_backend_linux_direct_call.o /tmp/self_backend_linux_direct_call.c
cc -no-pie /tmp/self_backend_linux_direct_call.o -o /tmp/self_backend_linux_direct_call
/tmp/self_backend_linux_direct_call
"""
    )

    assert result.returncode == 42, (
        "linux x86_64 docker self-backend direct-call/binop smoke failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_llvm_c_testsuite_exact_match_bucket():
    assert CTESTSUITE_HARNESS.is_file(), f"missing harness: {CTESTSUITE_HARNESS}"

    result = _run_linux_x86_64_harness(
        f"env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py --mode llvm-native-exact --bucket-size {X86_64_LINUX_BUCKET_SIZE} --timeout 20"
    )

    assert result.returncode == 0, (
        "linux x86_64 docker llvm c-testsuite bucket failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_self_backend_c_testsuite_bucket_handles_partial_support_cleanly():
    assert CTESTSUITE_HARNESS.is_file(), f"missing harness: {CTESTSUITE_HARNESS}"

    result = _run_linux_x86_64_harness(
        f"env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py --mode self-partial --bucket-size {X86_64_LINUX_BUCKET_SIZE} --timeout 20"
    )

    assert result.returncode == 0, (
        "linux x86_64 docker self-backend partial c-testsuite bucket failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(not _docker_available(), reason="docker harness not available")
def test_linux_x86_64_docker_self_backend_strict_exact_bucket():
    assert CTESTSUITE_HARNESS.is_file(), f"missing harness: {CTESTSUITE_HARNESS}"

    result = _run_linux_x86_64_harness(
        "env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py "
        "--mode self-strict-exact --bucket-size 32 --timeout 20"
    )

    assert result.returncode == 0, (
        "linux x86_64 docker self-backend strict-exact c-testsuite bucket failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
