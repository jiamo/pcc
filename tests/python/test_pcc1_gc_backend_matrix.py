"""pcc1 must run under every runtime GC backend.

``test_pcc_bootstrap_full.py`` proves that the self-host chain can build
``pcc1`` under the default runtime environment. This file is the narrower GC
matrix: reuse an existing bootstrapped ``pcc1`` binary and run the pcc1
compiler process itself under ``PCC_GC_BACKEND=0..4``.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).absolute().parents[2]
_PCC1_CANDIDATES = (
    REPO / "build" / "bootstrap-pytest-self" / "pcc1",
    REPO / "build" / "bootstrap" / "pcc1",
    REPO / "build" / "bootstrap-self-claude" / "pcc1",
    REPO / "build" / "bootstrap-llvm-claude" / "pcc1",
    REPO / "build" / "bootstrap-strict-self" / "pcc1",
    REPO / "build" / "bootstrap-self-darwin_arm64" / "pcc1",
    REPO / "build" / "bootstrap-llvm-darwin_arm64" / "pcc1",
)
_PCC2_CANDIDATES = (
    REPO / "build" / "bootstrap-pytest-self" / "pcc2",
    REPO / "build" / "bootstrap" / "pcc2",
    REPO / "build" / "bootstrap-self-claude" / "pcc2",
    REPO / "build" / "bootstrap-llvm-claude" / "pcc2",
    REPO / "build" / "bootstrap-strict-self" / "pcc2",
    REPO / "build" / "bootstrap-self-darwin_arm64" / "pcc2",
    REPO / "build" / "bootstrap-llvm-darwin_arm64" / "pcc2",
)


def _find_pcc1() -> Path | None:
    return _find_stage_binary("PCC1_BINARY", _PCC1_CANDIDATES)


def _find_pcc2() -> Path | None:
    return _find_stage_binary("PCC2_BINARY", _PCC2_CANDIDATES)


def _find_stage_binary(env_name: str, candidates: tuple[Path, ...]) -> Path | None:
    env_path = os.environ.get(env_name)
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file() and os.access(p, os.X_OK):
            return p
    for p in candidates:
        if p.exists() and p.is_file() and os.access(p, os.X_OK):
            return p
    return None


PCC1 = _find_pcc1()
PCC2 = _find_pcc2()
pytestmark = pytest.mark.pcc_gate(probe="pcc1")


@pytest.mark.parametrize(
    "stage_name,binary",
    [
        ("pcc1", PCC1),
        pytest.param(
            "pcc2",
            PCC2,
            marks=pytest.mark.pcc_gate(
                unavailable=None if PCC2 is not None else "No pcc2 binary on disk"
            ),
        ),
    ],
)
@pytest.mark.parametrize("gc_backend", ["0", "1", "2", "3", "4"])
def test_bootstrap_stage_cli_starts_under_gc_backend(
    stage_name: str,
    binary: Path | None,
    gc_backend: str,
) -> None:
    """Bootstrapped compiler stages must start and exit cleanly under every
    selectable runtime GC backend.

    This is intentionally lighter than the compile/run churn tests below: it
    proves the compiler process itself can initialize the selected GC backend,
    parse CLI options, print help, and shut down without finalizer/root issues.
    """
    if binary is None:
        pytest.fail(f"No {stage_name} binary found on disk")
    env = {
        **os.environ,
        "PCC_GC_BACKEND": gc_backend,
    }
    proc = subprocess.run(
        [str(binary), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO),
        env=env,
    )
    assert proc.returncode == 0, (
        f"{stage_name} --help failed under PCC_GC_BACKEND={gc_backend} "
        f"(exit {proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "Usage:" in proc.stdout


@pytest.mark.parametrize("gc_backend", ["0", "1", "2", "3", "4"])
def test_pcc1_self_backend_compile_smoke_under_gc_backend(
    tmp_path: Path,
    gc_backend: str,
) -> None:
    src = tmp_path / "gc_smoke.py"
    exe = tmp_path / "gc_smoke.out"
    src.write_text(
        textwrap.dedent(
            """
            import gc

            def main() -> None:
                values = []
                i = 0
                while i < 64:
                    values.append(i)
                    i = i + 1

                total = 0
                j = 0
                while j < len(values):
                    total = total + values[j]
                    j = j + 1
                print(total)

                churn_total = 0
                k = 0
                while k < 128:
                    chunk = [k, k + 1, k + 2, k + 3]
                    churn_total = churn_total + chunk[0]
                    if k % 8 == 0:
                        gc.collect()
                    k = k + 1
                print(churn_total)
                gc.collect()
                print(gc.isenabled())

            if __name__ == "__main__":
                main()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PCC_GC_BACKEND": gc_backend,
    }
    compile_cmd = [
        str(PCC1),
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(src),
        "-o",
        str(exe),
    ]
    compile_proc = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
        env=env,
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 compile failed under PCC_GC_BACKEND={gc_backend} "
        f"(exit {compile_proc.returncode}):\n"
        f"cmd: {' '.join(compile_cmd)}\n"
        f"stdout:\n{compile_proc.stdout}\n"
        f"stderr:\n{compile_proc.stderr}"
    )
    assert exe.exists(), f"pcc1 produced no binary at {exe}"

    run_proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert run_proc.returncode == 0, (
        f"pcc1-built binary failed under PCC_GC_BACKEND={gc_backend} "
        f"(exit {run_proc.returncode}):\n"
        f"stdout:\n{run_proc.stdout}\n"
        f"stderr:\n{run_proc.stderr}"
    )
    assert run_proc.stdout.strip().splitlines() == [
        "2016",
        "8128",
        "True",
    ]


@pytest.mark.parametrize("gc_backend", ["0", "1", "2", "3", "4"])
def test_pcc1_threading_objects_survive_gc_backend_churn(
    tmp_path: Path,
    gc_backend: str,
) -> None:
    """pcc1 must native-compile ``threading`` and keep thread objects alive
    while GC runs under every backend.

    This is intentionally a pcc1 frontend/runtime object-lifetime gate, not a
    full concurrent-pthread proof: the pcc-Python runtime archive currently
    runs ``Thread.start()`` through a synchronous shim. Real pthread/STW
    substrate coverage lives in ``test_gc_threading_substrate.py``.
    """
    src = tmp_path / "thread_gc_smoke.py"
    exe = tmp_path / "thread_gc_smoke.out"
    src.write_text(
        textwrap.dedent(
            """
            import gc
            from threading import Lock, Thread

            counts = [0]
            lock = Lock()

            def worker() -> None:
                i = 0
                while i < 200:
                    chunk = [i, i + 1, i + 2, i + 3]
                    if i % 17 == 0:
                        gc.collect()
                    lock.acquire()
                    counts[0] = counts[0] + chunk[0]
                    lock.release()
                    i = i + 1

            def main() -> None:
                t0 = Thread(target=worker)
                t1 = Thread(target=worker)
                t0.start()
                t1.start()
                i = 0
                while i < 20:
                    gc.collect()
                    i = i + 1
                t0.join()
                t1.join()
                gc.collect()
                print(counts[0])
                print(gc.isenabled())

            if __name__ == "__main__":
                main()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PCC_GC_BACKEND": gc_backend,
        "PCC_WITH_THREADS": "1",
    }
    compile_cmd = [
        str(PCC1),
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(src),
        "-o",
        str(exe),
    ]
    compile_proc = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
        env=env,
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 threading compile failed under PCC_GC_BACKEND={gc_backend} "
        f"(exit {compile_proc.returncode}):\n"
        f"cmd: {' '.join(compile_cmd)}\n"
        f"stdout:\n{compile_proc.stdout}\n"
        f"stderr:\n{compile_proc.stderr}"
    )
    assert exe.exists(), f"pcc1 produced no binary at {exe}"

    run_proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert run_proc.returncode == 0, (
        f"pcc1-built threading binary failed under PCC_GC_BACKEND={gc_backend} "
        f"(exit {run_proc.returncode}):\n"
        f"stdout:\n{run_proc.stdout}\n"
        f"stderr:\n{run_proc.stderr}"
    )
    assert run_proc.stdout.strip().splitlines() == [
        "39800",
        "True",
    ]
