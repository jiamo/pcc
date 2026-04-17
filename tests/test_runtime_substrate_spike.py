"""Unsafe-runtime and pcc-Python archive regression tests.

These checks cover the pcc.unsafe lowering path, pcc-Python runtime
replacement archive membership, and a few C harnesses that link against
the pcc-Python archive directly.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE_SRC = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_tuple_spike.py"
PY_RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
PY_RUNTIME_PY_DIR = PY_RUNTIME_DIR / "py"


def _pcc_binary() -> str:
    candidate = Path(sys.executable).parent / "pcc"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("pcc")
    if found is None:
        pytest.skip("pcc CLI not on PATH")
    return found


def _active_python_runtime_modules() -> list[str]:
    makefile = PY_RUNTIME_DIR / "Makefile"
    for line in makefile.read_text().splitlines():
        if line.startswith("PY_MODULES ="):
            return line.split("=", 1)[1].split()
    raise AssertionError("PY_MODULES line not found in py_runtime Makefile")


def test_no_libpython_runtime_selector_defaults_to_pcc_python_archive():
    from pcc.py_frontend import pipeline

    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch(
            "pcc.py_frontend.pipeline.os.path.isfile",
            return_value=True,
        ):
            with mock.patch(
                "pcc.py_frontend.pipeline._runtime_archive_stale",
                return_value=False,
            ):
                archive = pipeline._ensure_runtime(
                    False, needs_libpython=False,
                )

    assert Path(archive).name == "libpy_runtime_pcc_py.a"


def test_runtime_selector_keeps_explicit_oracle_archives():
    from pcc.py_frontend import pipeline

    with mock.patch(
        "pcc.py_frontend.pipeline.os.path.isfile",
        return_value=True,
    ):
        with mock.patch(
            "pcc.py_frontend.pipeline._runtime_archive_stale",
            return_value=False,
        ):
            with mock.patch.dict(
                os.environ,
                {"PCC_RUNTIME_CC": "cc", "PCC_RUNTIME_HIGH": "c"},
                clear=True,
            ):
                cc_archive = pipeline._ensure_runtime(
                    False, needs_libpython=False,
                )
            with mock.patch.dict(
                os.environ,
                {"PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "c"},
                clear=True,
            ):
                pcc_c_archive = pipeline._ensure_runtime(
                    False, needs_libpython=False,
                )
            with mock.patch.dict(os.environ, {}, clear=True):
                libpython_archive = pipeline._ensure_runtime(
                    False, needs_libpython=True,
                )

    assert Path(cc_archive).name == "libpy_runtime.a"
    assert Path(pcc_c_archive).name == "libpy_runtime_pcc.a"
    assert Path(libpython_archive).name == "libpy_runtime_pcc_py_libpython.a"


def test_active_python_runtime_modules_do_not_call_substrate_helpers():
    offenders: list[str] = []
    for module in _active_python_runtime_modules():
        if module == "py_substrate":
            continue
        path = PY_RUNTIME_PY_DIR / f"{module}.py"
        text = path.read_text()
        for token in ("py_mem_", "py_subs_"):
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} contains {token}")

    assert offenders == []


def test_python_runtime_replacement_list_has_no_c_runtime_islands():
    makefile = PY_RUNTIME_DIR / "Makefile"
    text = makefile.read_text()
    srcs: set[str] = set()
    for line in text.splitlines():
        marker = "$(SRCDIR)/"
        if marker in line and line.rstrip().endswith(".c \\"):
            name = line.split(marker, 1)[1].split(".c", 1)[0]
            srcs.add(name)
        elif marker in line and line.rstrip().endswith(".c"):
            name = line.split(marker, 1)[1].split(".c", 1)[0]
            srcs.add(name)

    replaced = set(_active_python_runtime_modules())
    remaining = srcs - replaced
    assert remaining == set()


def test_pcc_python_archive_has_no_libpython_object():
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    result = subprocess.run(
        ["ar", "t", str(archive)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    members = set(result.stdout.splitlines())
    assert "py_process.o" in members
    assert "py_substrate.o" in members
    assert "py_libpython.o" not in members


def test_pcc_python_libpython_archive_adds_only_bridge_object():
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py_libpython.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    result = subprocess.run(
        ["ar", "t", str(archive)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    members = set(result.stdout.splitlines())
    assert "py_substrate.o" in members
    assert "py_int.o" in members
    assert "py_libpython.o" in members


def test_pcc_python_archive_uses_python_py_substrate_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_substrate.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_substrate.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_None" in out
    assert "_py_True" in out
    assert "_py_False" in out
    assert "_PY_EXC_BUILTIN_NAMES" in out
    assert "_PY_OBJECT_NAME" in out
    assert "_py_tls_current_exc_storage" in out
    assert "_py_tls_exc_get" in out


def test_pcc_python_archive_uses_python_py_process_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_process.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_process.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_runtime_program_argc" in out
    assert "_py_runtime_program_argv" in out
    assert "_py_runtime_program_args_hook" in out
    assert "_py_set_program_args" in out


def test_pcc_python_archive_uses_python_py_int_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_int.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_int.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "_py_bigint_divmod" in result.stdout
    assert "_user_py_int__bit_length_mag" in result.stdout
    assert "_user_py_int__one_shifted" in result.stdout


def test_pcc_python_runtime_bigint_divmod_matches_python(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    harness = tmp_path / "bigint_divmod_harness.c"
    harness.write_text(
        """
        #include "pcc/py_runtime/src/py_internal.h"
        #include <stdio.h>
        #include <stdlib.h>

        static int run_case(const char *as, const char *bs) {
            PyIntObject *a = py_bigint_from_cstr(as);
            PyIntObject *b = py_bigint_from_cstr(bs);
            PyIntObject *q = NULL;
            PyIntObject *r = NULL;
            if (!a || !b) return 10;
            if (py_bigint_divmod(a, b, &q, &r) != 0) return 11;
            char *qs = py_bigint_to_cstr(q);
            char *rs = py_bigint_to_cstr(r);
            if (!qs || !rs) return 12;
            puts(qs);
            puts(rs);
            free(qs);
            free(rs);
            free(q);
            free(r);
            free(a);
            free(b);
            return 0;
        }

        int main(void) {
            int rc = 0;
            rc |= run_case("5", "10");
            rc |= run_case("10", "5");
            rc |= run_case("0", "123");
            rc |= run_case("1267650600228229401496703205376", "10000000000");
            rc |= run_case("-1267650600228229401496703205376", "10000000000");
            rc |= run_case("1267650600228229401496703205376", "-10000000000");
            return rc;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "bigint_divmod_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0
    assert run_res.stdout == (
        "0\n"
        "5\n"
        "2\n"
        "0\n"
        "0\n"
        "0\n"
        "126765060022822940149\n"
        "6703205376\n"
        "-126765060022822940150\n"
        "3296794624\n"
        "-126765060022822940150\n"
        "-3296794624\n"
    )


def test_pcc_python_traceback_archive_formats_exception(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    harness = tmp_path / "traceback_harness.c"
    harness.write_text(
        """
        #include "pcc/py_runtime/include/py_runtime.h"
        int main(void) {
            PyObject *exc = py_exc_new(2, "boom");
            py_exc_append_frame(exc, "fn", "file.py", 12);
            py_exc_print_unhandled(exc);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "traceback_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0
    assert run_res.stdout == ""
    assert run_res.stderr == (
        "Traceback (most recent call last):\n"
        "  File \"file.py\", line 12, in fn\n"
        "ValueError: boom\n"
    )


def test_py_tuple_port_spike_runs_correctly(tmp_path):
    if not SPIKE_SRC.exists():
        pytest.skip(f"spike source missing: {SPIKE_SRC}")
    out = tmp_path / "py_tuple_spike"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    compile_res = subprocess.run(
        [_pcc_binary(), str(SPIKE_SRC), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert compile_res.returncode == 0, (
        f"spike compile failed\n{compile_res.stderr}"
    )
    run_res = subprocess.run(
        [str(out)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, (
        f"spike run failed rc={run_res.returncode}\n"
        f"stdout: {run_res.stdout!r}\nstderr: {run_res.stderr!r}"
    )
    lines = run_res.stdout.strip().splitlines()
    assert lines == ["len 3", "10", "20", "30"], (
        f"unexpected spike output: {lines}"
    )


def test_py_tuple_port_spike_under_pcc_runtime_cc(tmp_path):
    """Same as above but with the pcc-emitted runtime archive."""
    if not SPIKE_SRC.exists():
        pytest.skip(f"spike source missing: {SPIKE_SRC}")
    out = tmp_path / "py_tuple_spike_pcc"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "pcc"
    compile_res = subprocess.run(
        [_pcc_binary(), str(SPIKE_SRC), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert compile_res.returncode == 0, (
        f"spike compile failed (pcc runtime)\n{compile_res.stderr}"
    )
    run_res = subprocess.run(
        [str(out)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, (
        f"spike run failed (pcc runtime) rc={run_res.returncode}\n"
        f"stdout: {run_res.stdout!r}\nstderr: {run_res.stderr!r}"
    )
    lines = run_res.stdout.strip().splitlines()
    assert lines == ["len 3", "10", "20", "30"], (
        f"unexpected spike output (pcc runtime): {lines}"
    )
