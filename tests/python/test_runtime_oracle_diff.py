"""Phase 0 differential oracle harness (task #178).

For each Python program in tests/runtime_oracle/, compile + run under each
available runtime-source path, then assert byte-equivalent stdout / stderr
/ returncode across paths.

Runtime-source paths (see PCC_RUNTIME_CC/PCC_RUNTIME_HIGH switches in
pipeline.py):
  - cc-C : cc compiles py_runtime/src/*.c into libpy_runtime.a (baseline)
  - pcc-C: pcc --emit-obj on the same sources into libpy_runtime_pcc.a
  - pcc-Py: pcc on py_runtime/py/*.py into libpy_runtime_pcc_py.a

Currently the pcc-C archive only covers the no-libpython path. This
oracle passes ``--python-libpython=auto`` explicitly for programs whose
IR still emits py_cpy_* fallback calls; strict no-libpython tests live
elsewhere.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
ORACLE_DIR = REPO_ROOT / "tests" / "runtime_oracle"
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


_KNOWN_PCC_C_DIVERGENCES: dict[str, str] = {}

# Programs whose IR has no py_cpy_* call and therefore the explicit
# PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=c path should actually use
# libpy_runtime_pcc.a
# (not fall back to the cc-built libpython archive). This list defines
# which programs count as "pcc-C genuinely under test" today.
_PCC_ARCHIVE_COVERED = {
    "int_basics",
    "str_basics",
    "tuple_basics",
    "class_basics",
    "dict_basics",
    "exc_basics",
    "exc_inherit_basics",
    # 2026-04-28 wave: os.path.{join/dirname/basename/isfile/isdir/
    # getmtime/abspath/exists} + os.environ.get + sys.platform +
    # print(file=sys.stderr) all dispatch natively, leaving the
    # program with zero py_cpy_* fallback.
    "path_basics",
}


def _corpus_programs() -> list[Path]:
    return sorted(ORACLE_DIR.glob("*_basics.py"))


def _pcc_binary() -> Optional[str]:
    """Locate the pcc CLI binary."""
    candidate = Path(sys.executable).parent / "pcc"
    if candidate.exists():
        return str(candidate)
    return shutil.which("pcc")


def _compile_and_run(
    source: Path,
    out_path: Path,
    runtime_cc: str,
    args: list[str],
) -> tuple[int, str, str, str]:
    """Compile `source` with the given runtime-cc mode and run it.

    Returns (returncode, stdout, stderr, runtime_archive_basename).
    """
    pcc_bin = _pcc_binary()
    if pcc_bin is None:
        pytest.skip("pcc CLI not on PATH")
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    if runtime_cc == "cc":
        env["PCC_RUNTIME_CC"] = "cc"
        env["PCC_RUNTIME_HIGH"] = "c"
    elif runtime_cc in ("pcc", "pcc-py"):
        env["PCC_RUNTIME_CC"] = "pcc"
        env["PCC_RUNTIME_HIGH"] = "py" if runtime_cc == "pcc-py" else "c"
    else:
        env.pop("PCC_RUNTIME_CC", None)
        env.pop("PCC_RUNTIME_HIGH", None)
    compile_result = subprocess.run(
        [
            pcc_bin,
            "--verbose",
            "--python-libpython=auto",
            str(source),
            "-o",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    archive_basename = ""
    compile_log = compile_result.stderr + "\n" + compile_result.stdout
    for line in compile_log.splitlines():
        if "runtime archive:" in line:
            archive_basename = Path(line.rsplit(":", 1)[-1].strip()).name
            break
    if compile_result.returncode != 0:
        pytest.fail(
            f"compile failed ({runtime_cc}): rc={compile_result.returncode}\n"
            f"STDOUT:\n{compile_result.stdout}\n"
            f"STDERR:\n{compile_result.stderr}"
        )
    run_result = subprocess.run(
        [str(out_path), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(out_path.parent),
    )
    return (
        run_result.returncode,
        run_result.stdout,
        run_result.stderr,
        archive_basename,
    )


@pytest.fixture(scope="session")
def _ensure_runtime_archives(tmp_path_factory):
    """Build both cc and pcc runtime archives once per session."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("make not available")

    pcc_bin = _pcc_binary()
    if pcc_bin is None:
        pytest.skip("pcc CLI not available")

    # cc baseline
    subprocess.run(
        [make, "-C", str(RUNTIME_DIR), "libpy_runtime.a"],
        check=True,
        capture_output=True,
    )
    # pcc track (all C, compiled by pcc)
    subprocess.run(
        [
            make,
            "-C",
            str(RUNTIME_DIR),
            f"PCC={pcc_bin}",
            "libpy_runtime_pcc.a",
        ],
        check=True,
        capture_output=True,
    )
    # pcc-py track (Phase 4c: runtime-high from pcc-Python where available)
    subprocess.run(
        [
            make,
            "-C",
            str(RUNTIME_DIR),
            f"PCC={pcc_bin}",
            "libpy_runtime_pcc_py.a",
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "program",
    _corpus_programs(),
    ids=lambda p: p.stem,
)
def test_corpus_cc_vs_pcc_equivalence(
    tmp_path, program, _ensure_runtime_archives
):
    """Assert cc-C and pcc-C runtime artifacts produce identical behavior."""
    if program.stem in _KNOWN_PCC_C_DIVERGENCES:
        pytest.xfail(reason=_KNOWN_PCC_C_DIVERGENCES[program.stem])

    args = ["oracle-arg-1", "oracle-arg-2"]
    cc_exe = tmp_path / f"{program.stem}.cc.out"
    pcc_exe = tmp_path / f"{program.stem}.pcc.out"

    cc_rc, cc_stdout, cc_stderr, cc_arc = _compile_and_run(
        program, cc_exe, "cc", args
    )
    pcc_rc, pcc_stdout, pcc_stderr, pcc_arc = _compile_and_run(
        program, pcc_exe, "pcc", args
    )

    # Programs on the "pcc-C genuinely covered" list must actually link
    # the pcc-emitted archive — otherwise we are only testing the
    # trivial "same cc archive" case and not proving anything about
    # pcc's own C codegen.
    if program.stem in _PCC_ARCHIVE_COVERED:
        assert pcc_arc == "libpy_runtime_pcc.a", (
            f"{program.stem} expected libpy_runtime_pcc.a under "
            f"PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=c, got {pcc_arc!r}. "
            f"(cc mode picked {cc_arc!r}.)"
        )

    # argv[0] differs (cc.out vs pcc.out); normalize before comparing.
    cc_stdout = cc_stdout.replace(cc_exe.name, "ORACLE.out")
    pcc_stdout = pcc_stdout.replace(pcc_exe.name, "ORACLE.out")

    assert cc_rc == pcc_rc, (
        f"returncode diverges: cc={cc_rc} pcc={pcc_rc}\n"
        f"cc stdout: {cc_stdout!r}\n"
        f"pcc stdout: {pcc_stdout!r}\n"
        f"cc stderr: {cc_stderr!r}\n"
        f"pcc stderr: {pcc_stderr!r}"
    )
    assert cc_stdout == pcc_stdout, (
        f"stdout diverges (rc={cc_rc}):\n"
        f"cc:\n{cc_stdout}\n"
        f"pcc:\n{pcc_stdout}"
    )
    assert cc_stderr == pcc_stderr, (
        f"stderr diverges (rc={cc_rc}):\n"
        f"cc:\n{cc_stderr}\n"
        f"pcc:\n{pcc_stderr}"
    )


# Programs that exercise at least one pcc-Python-ported runtime module
# under the libpy_runtime_pcc_py.a archive. Extend as Phase 4c lands more
# modules.
_PCC_PY_ARCHIVE_COVERED = {
    # Programs that do NOT hit py_cpy_* fallback, so PCC_RUNTIME_HIGH=py
    # actually picks libpy_runtime_pcc_py.a and the pcc-Python runtime
    # port is genuinely exercised.
    "int_basics",
    "str_basics",
    "tuple_basics",
    "class_basics",
    "dict_basics",
    "exc_inherit_basics",
    # set_basics hits py_cpy_* fallback (sorted / set-union ops), so
    # pcc-Python port of py_set still runs but the archive selector
    # picks libpy_runtime_libpython.a instead. pcc-py archive coverage
    # is enforced by the cc-vs-pcc-C oracle path above.
    # path_basics: dual-implemented py_os_path_{dirname,isfile,isdir,
    # getmtime,abspath} ports must produce identical output to the C
    # baseline.
    "path_basics",
}


def _runs_under_pcc_py(program_stem: str) -> bool:
    # pcc-py archive only replaces the pcc-C archive; programs that
    # still route through the cc-built libpython archive (because they
    # hit py_cpy_* fallback paths) are not exercised by PCC_RUNTIME_HIGH.
    return program_stem in _PCC_PY_ARCHIVE_COVERED


@pytest.mark.parametrize(
    "program",
    _corpus_programs(),
    ids=lambda p: p.stem,
)
def test_corpus_cc_vs_pcc_py_equivalence(
    tmp_path, program, _ensure_runtime_archives
):
    """Assert cc-C and pcc-Py runtime artifacts produce identical behavior.

    Phase 4c equivalence proof: programs that exercise pcc-Python-ported
    runtime modules should produce byte-identical stdout/stderr/rc to
    the baseline cc-C runtime.
    """
    if program.stem in _KNOWN_PCC_C_DIVERGENCES:
        pytest.xfail(reason=_KNOWN_PCC_C_DIVERGENCES[program.stem])
    if not _runs_under_pcc_py(program.stem):
        pytest.skip(
            f"{program.stem} does not exercise a pcc-py archive slot; "
            "only pcc-py-covered programs enforce archive selection."
        )

    args = ["oracle-arg-1", "oracle-arg-2"]
    cc_exe = tmp_path / f"{program.stem}.cc.out"
    pcc_py_exe = tmp_path / f"{program.stem}.pcc_py.out"

    cc_rc, cc_stdout, cc_stderr, _ = _compile_and_run(
        program, cc_exe, "cc", args
    )
    pcc_py_rc, pcc_py_stdout, pcc_py_stderr, pcc_py_arc = _compile_and_run(
        program, pcc_py_exe, "pcc-py", args
    )

    if program.stem in _PCC_PY_ARCHIVE_COVERED:
        assert pcc_py_arc == "libpy_runtime_pcc_py.a", (
            f"{program.stem} expected libpy_runtime_pcc_py.a under "
            f"PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py, got {pcc_py_arc!r}."
        )

    cc_stdout = cc_stdout.replace(cc_exe.name, "ORACLE.out")
    pcc_py_stdout = pcc_py_stdout.replace(pcc_py_exe.name, "ORACLE.out")

    assert cc_rc == pcc_py_rc, (
        f"rc diverges (cc={cc_rc} pcc-py={pcc_py_rc})\n"
        f"cc stdout: {cc_stdout!r}\n"
        f"pcc-py stdout: {pcc_py_stdout!r}"
    )
    assert cc_stdout == pcc_py_stdout, (
        f"stdout diverges:\ncc:\n{cc_stdout}\npcc-py:\n{pcc_py_stdout}"
    )
    assert cc_stderr == pcc_py_stderr, (
        f"stderr diverges:\ncc:\n{cc_stderr}\npcc-py:\n{pcc_py_stderr}"
    )


def test_oracle_inventory_matches_manifest():
    """Smoke: every xfail entry corresponds to an actual corpus program."""
    corpus = {p.stem for p in _corpus_programs()}
    missing = set(_KNOWN_PCC_C_DIVERGENCES) - corpus
    assert not missing, (
        f"xfail list references non-existent programs: {missing}"
    )
