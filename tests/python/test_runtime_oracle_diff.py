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

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
ORACLE_DIR = REPO_ROOT / "tests" / "runtime_oracle"
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"

# Each corpus case compiles a small program through pcc with a 120s per-compile
# timeout (kept tight on purpose: an un-stamped archive rebuild must not happen
# once per corpus program). Under the default `-n auto` that timeout
# is also contention-sensitive — running this file concurrently with the other
# heavy subprocess-spawning suites (GC backend matrix, runtime emit) starved a
# normally-fast per-program compile past 120s. Pin the file to its own
# xdist_group so `--dist=loadgroup` runs its cases on a single worker, isolated
# from that cross-suite contention, without relaxing the rebuild guard.
pytestmark = pytest.mark.xdist_group(name="pcc_runtime_oracle")


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

_BASELINE_RESULT_CACHE: dict[
    tuple[str, tuple[str, ...], str], tuple[int, str, str, str]
] = {}
_EXE_PLACEHOLDER = "__PCC_RUNTIME_ORACLE_EXE__"


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
    runtime_dir: Path,
) -> tuple[int, str, str, str]:
    """Compile `source` with the given runtime-cc mode and run it.

    Returns (returncode, stdout, stderr, runtime_archive_basename).
    """
    cache_key = (str(source), tuple(args), str(runtime_dir))
    if runtime_cc == "cc" and cache_key in _BASELINE_RESULT_CACHE:
        returncode, stdout, stderr, archive = _BASELINE_RESULT_CACHE[cache_key]
        return (
            returncode,
            stdout.replace(_EXE_PLACEHOLDER, out_path.name),
            stderr.replace(_EXE_PLACEHOLDER, out_path.name),
            archive,
        )
    pcc_bin = _pcc_binary()
    if pcc_bin is None:
        pytest.fail("pcc CLI not on PATH")
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_DIR"] = str(runtime_dir)
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
    result = (
        run_result.returncode,
        run_result.stdout,
        run_result.stderr,
        archive_basename,
    )
    if runtime_cc == "cc":
        _BASELINE_RESULT_CACHE[cache_key] = (
            result[0],
            result[1].replace(out_path.name, _EXE_PLACEHOLDER),
            result[2].replace(out_path.name, _EXE_PLACEHOLDER),
            result[3],
        )
    return result


def _run_runtime_archive_make(
    make: str,
    runtime_dir: Path,
    target: str,
    pcc_bin: str | None = None,
    *make_args: str,
):
    """Build one isolated archive with bounded whole-process-group ownership."""
    from tests.python.process_timeout import run_process_group_timeout

    cmd = [make, "-C", str(runtime_dir)]
    if pcc_bin is not None:
        cmd.append(f"PCC={pcc_bin}")
        cmd.append(f"PYTHON={sys.executable}")
        cmd.append(f"PCC_REPO_ROOT={REPO_ROOT}")
    cmd.extend(make_args)
    cmd.append(target)
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    result = run_process_group_timeout(
        cmd,
        env=env,
        timeout=300,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )


def _runtime_oracle_build_key(pcc_bin: str) -> str:
    """Hash every source that can affect the four runtime archives."""

    digest = hashlib.sha256()
    digest.update(sys.version.encode("utf-8"))
    digest.update(os.path.realpath(pcc_bin).encode("utf-8"))
    roots = (
        RUNTIME_DIR,
        REPO_ROOT / "pcc" / "backend",
        REPO_ROOT / "pcc" / "codegen",
        REPO_ROOT / "pcc" / "evaluater",
        REPO_ROOT / "pcc" / "llvm_capi",
        REPO_ROOT / "pcc" / "parse",
        REPO_ROOT / "pcc" / "py_frontend",
        REPO_ROOT / "pcc" / "tools",
        REPO_ROOT / "utils" / "fake_libc_include",
    )
    files = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                continue
            if path.suffix not in {".c", ".h", ".py"} and path.name != "Makefile":
                continue
            files.append(path)
    for path in (
        REPO_ROOT / "pcc" / "__main__.py",
        REPO_ROOT / "pcc" / "api.py",
        REPO_ROOT / "pcc" / "cli_core.py",
        REPO_ROOT / "pcc" / "pcc.py",
        REPO_ROOT / "pcc" / "project.py",
    ):
        if path.is_file():
            files.append(path)
    for path in sorted(set(files)):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:24]


def _cached_runtime_dir(make: str, pcc_bin: str) -> Path:
    """Return an immutable content-keyed runtime build outside the repo."""

    key = _runtime_oracle_build_key(pcc_bin)
    cache_root = (
        Path.home() / ".cache" / "pcc" / "test-artifacts" / "runtime-oracle"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = cache_root / key
    marker = runtime_dir / ".pcc-runtime-oracle-complete"
    archives = (
        "libpy_runtime.a",
        "libpy_runtime_libpython.a",
        "libpy_runtime_pcc.a",
        "libpy_runtime_pcc_py.a",
    )

    lock_path = cache_root / (key + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - POSIX test environment
            fcntl = None
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if marker.is_file() and marker.read_text(encoding="utf-8") == key:
                if all((runtime_dir / name).is_file() for name in archives):
                    return runtime_dir
            if runtime_dir.exists():
                shutil.rmtree(runtime_dir)

            staging_root = Path(
                tempfile.mkdtemp(prefix=key + ".", dir=str(cache_root))
            )
            work_runtime = staging_root / "py_runtime"
            shutil.copytree(
                RUNTIME_DIR,
                work_runtime,
                ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
                ),
            )
            _run_runtime_archive_make(make, work_runtime, "libpy_runtime.a")
            _run_runtime_archive_make(
                make,
                work_runtime,
                "libpy_runtime_libpython.a",
                None,
                "PCC_WITH_LIBPYTHON=1",
                "LIB=libpy_runtime_libpython.a",
                "OBJDIR=build_libpython",
            )
            _run_runtime_archive_make(
                make, work_runtime, "libpy_runtime_pcc.a", pcc_bin
            )
            _run_runtime_archive_make(
                make, work_runtime, "libpy_runtime_pcc_py.a", pcc_bin
            )

            from pcc.py_frontend import pipeline as _pcc_pipeline

            for archive_name in archives:
                _pcc_pipeline._write_runtime_archive_target_stamp(
                    str(work_runtime / archive_name)
                )
            (work_runtime / marker.name).write_text(key, encoding="utf-8")
            os.replace(work_runtime, runtime_dir)
            shutil.rmtree(staging_root, ignore_errors=True)
            return runtime_dir
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def _ensure_runtime_archives(tmp_path_factory):
    """Build all oracle runtime archives once in an isolated directory."""
    make = shutil.which("make")
    if make is None:
        pytest.fail("make not available")

    pcc_bin = _pcc_binary()
    if pcc_bin is None:
        pytest.fail("pcc CLI not available")

    del tmp_path_factory
    return _cached_runtime_dir(make, pcc_bin)


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
        program, cc_exe, "cc", args, _ensure_runtime_archives
    )
    pcc_rc, pcc_stdout, pcc_stderr, pcc_arc = _compile_and_run(
        program, pcc_exe, "pcc", args, _ensure_runtime_archives
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
    # port is genuinely exercised. Keep this as a ratchet: every current
    # runtime-oracle basics program is covered, and newly added corpus
    # programs must prove archive selection before joining the set.
    "exc_basics",
    "int_basics",
    "str_basics",
    "list_basics",
    "tuple_basics",
    "class_basics",
    "dict_basics",
    "exc_inherit_basics",
    "obj_ops_basics",
    "os_basics",
    "path_basics",
    "print_basics",
    "set_basics",
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
        pytest.fail(
            f"{program.stem} does not exercise a pcc-py archive slot; "
            "only pcc-py-covered programs enforce archive selection."
        )

    args = ["oracle-arg-1", "oracle-arg-2"]
    cc_exe = tmp_path / f"{program.stem}.cc.out"
    pcc_py_exe = tmp_path / f"{program.stem}.pcc_py.out"

    cc_rc, cc_stdout, cc_stderr, _ = _compile_and_run(
        program, cc_exe, "cc", args, _ensure_runtime_archives
    )
    pcc_py_rc, pcc_py_stdout, pcc_py_stderr, pcc_py_arc = _compile_and_run(
        program, pcc_py_exe, "pcc-py", args, _ensure_runtime_archives
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


def test_pcc_py_archive_coverage_manifest_matches_current_corpus():
    """The pcc-Python runtime oracle should not silently skip current corpus."""
    corpus = {p.stem for p in _corpus_programs()}
    extra = _PCC_PY_ARCHIVE_COVERED - corpus
    missing = corpus - _PCC_PY_ARCHIVE_COVERED
    assert not extra, f"pcc-py coverage references non-existent programs: {extra}"
    assert not missing, (
        "runtime oracle programs missing from pcc-py archive coverage: "
        f"{missing}"
    )
