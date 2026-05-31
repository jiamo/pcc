"""Unsafe-runtime and pcc-Python archive regression tests.

These checks cover the pcc.unsafe lowering path, pcc-Python runtime
replacement archive membership, and a few C harnesses that link against
the pcc-Python archive directly.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
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
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if line.startswith("PY_MODULES ="):
            return line.split("=", 1)[1].split()
    raise AssertionError("PY_MODULES line not found in py_runtime Makefile")


def _make_var_words(name: str) -> list[str]:
    makefile = PY_RUNTIME_DIR / "Makefile"
    lines = makefile.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not (
            line.startswith(f"{name} =")
            or line.startswith(f"{name} +=")
        ):
            i += 1
            continue
        line = line.split("=", 1)[1].strip()
        while True:
            continued = line.endswith("\\")
            if continued:
                line = line[:-1].strip()
            if line:
                out.extend(line.split())
            if not continued:
                break
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()
        i += 1
    if not out:
        raise AssertionError(f"{name} line not found in py_runtime Makefile")
    return out


def _runtime_c_source_modules() -> set[str]:
    srcs: set[str] = set()
    for word in _make_var_words("SRCS"):
        marker = "$(SRCDIR)/"
        if not word.startswith(marker) or not word.endswith(".c"):
            continue
        srcs.add(word[len(marker):-2])
    return srcs


def _replaced_c_modules() -> set[str]:
    py_modules = set(_active_python_runtime_modules())
    replaced: set[str] = set()
    for word in _make_var_words("PY_REPLACED_C_MODULES"):
        if word == "$(PY_MODULES)":
            replaced.update(py_modules)
        else:
            replaced.add(word)
    return replaced


def _pcc_py_c_helper_modules() -> set[str]:
    helpers: set[str] = set()
    for word in _make_var_words("OBJ_PY_CC_HELPERS"):
        marker = "$(OBJDIR_PY)/"
        if word.startswith(marker) and word.endswith(".o"):
            helpers.add(word[len(marker):-2])
    return helpers


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


def test_resolve_pcc_binary_prefers_current_stage_binary(tmp_path):
    from pcc.py_frontend import pipeline

    stage = tmp_path / "pcc1"
    stage.write_text("#!/bin/sh\n", encoding="utf-8")
    stage.chmod(0o755)

    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(sys, "argv", [str(stage), "--backend", "self"]):
            assert pipeline._resolve_pcc_binary() == str(stage)


def test_resolve_pcc_binary_ignores_pytest_argv0(tmp_path):
    from pcc.py_frontend import pipeline

    pytest_bin = tmp_path / "pytest"
    pytest_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    pytest_bin.chmod(0o755)
    pcc_bin = tmp_path / "pcc"
    pcc_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    pcc_bin.chmod(0o755)

    with mock.patch.dict(os.environ, {"PATH": str(tmp_path)}, clear=True):
        with mock.patch.object(sys, "argv", [str(pytest_bin), "-q"]):
            with mock.patch.object(sys, "executable", str(tmp_path / "python")):
                assert pipeline._resolve_pcc_binary() == str(pcc_bin)


def test_active_python_runtime_modules_do_not_call_substrate_helpers():
    # Modules allowed to call the substrate's ``py_mem_alloc`` /
    # ``py_mem_free`` directly. The rule is "active runtime modules
    # don't reach into substrate", but a small set of modules manage
    # their own raw-buffer working storage and the alternative
    # (routing every alloc through an indirect wrapper) would force
    # an extra ABI hop for every codepath that needs scratch memory.
    # ``py_obj_stubs`` was the original allowed caller; ``py_str_accessors``
    # is the second: ``str.ljust`` / ``str.rjust`` / ``str.center`` /
    # ``str.zfill`` / ``str.rsplit`` all allocate transient padded /
    # split buffers, and the byte-level work is closer to memory
    # management than to higher-level Python semantics.
    allowed_py_mem_callers = {"py_obj_stubs", "py_str_accessors"}
    offenders: list[str] = []
    for module in _active_python_runtime_modules():
        if module == "py_substrate":
            continue
        path = PY_RUNTIME_PY_DIR / f"{module}.py"
        text = path.read_text(encoding="utf-8")
        tokens = ("py_subs_",)
        if module not in allowed_py_mem_callers:
            tokens = ("py_mem_",) + tokens
        for token in tokens:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} contains {token}")

    assert offenders == []


def test_python_runtime_replacement_list_has_only_declared_c_islands():
    srcs = _runtime_c_source_modules()
    replaced = _replaced_c_modules()
    remaining = srcs - replaced
    allowed_c_islands = _pcc_py_c_helper_modules() & srcs
    assert remaining == allowed_c_islands


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


def test_no_libpython_pcc_python_archive_staleness_ignores_libpython_bridge(tmp_path):
    from pcc.py_frontend import pipeline

    runtime_dir = tmp_path / "py_runtime"
    src_dir = runtime_dir / "src"
    include_dir = runtime_dir / "include"
    py_dir = runtime_dir / "py"
    src_dir.mkdir(parents=True)
    include_dir.mkdir()
    py_dir.mkdir()

    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_text("archive", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    (include_dir / "py_runtime.h").write_text("/* header */\n", encoding="utf-8")
    (src_dir / "py_obj.c").write_text("/* normal no-lib source */\n", encoding="utf-8")
    (src_dir / "py_libpython.c").write_text("/* optional bridge */\n", encoding="utf-8")
    (py_dir / "py_obj.py").write_text("# pcc-python runtime\n", encoding="utf-8")

    stamp = Path(pipeline._runtime_archive_target_stamp(str(archive)))
    stamp.write_text(pipeline._runtime_archive_target_id() + "\n", encoding="utf-8")

    old = 1_700_000_000
    current = old + 10
    newer = current + 10
    for path in [
        runtime_dir / "Makefile",
        include_dir / "py_runtime.h",
        src_dir / "py_obj.c",
        py_dir / "py_obj.py",
        stamp,
    ]:
        os.utime(path, (old, old))
    os.utime(archive, (current, current))
    os.utime(src_dir / "py_libpython.c", (newer, newer))

    with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
        assert pipeline._runtime_archive_stale(str(archive)) is False


def test_libpython_pcc_python_archive_staleness_tracks_libpython_bridge(tmp_path):
    from pcc.py_frontend import pipeline

    runtime_dir = tmp_path / "py_runtime"
    src_dir = runtime_dir / "src"
    include_dir = runtime_dir / "include"
    py_dir = runtime_dir / "py"
    src_dir.mkdir(parents=True)
    include_dir.mkdir()
    py_dir.mkdir()

    base_archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive = runtime_dir / "libpy_runtime_pcc_py_libpython.a"
    base_archive.write_text("base archive", encoding="utf-8")
    archive.write_text("archive", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    (include_dir / "py_runtime.h").write_text("/* header */\n", encoding="utf-8")
    (src_dir / "py_libpython.c").write_text("/* optional bridge */\n", encoding="utf-8")

    stamp = Path(pipeline._runtime_archive_target_stamp(str(archive)))
    stamp.write_text(pipeline._runtime_archive_target_id() + "\n", encoding="utf-8")

    old = 1_700_000_000
    current = old + 10
    newer = current + 10
    for path in [
        runtime_dir / "Makefile",
        include_dir / "py_runtime.h",
        base_archive,
        archive,
        stamp,
    ]:
        os.utime(path, (old, old))
    os.utime(archive, (current, current))
    os.utime(src_dir / "py_libpython.c", (newer, newer))

    with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
        with mock.patch.object(pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(base_archive)):
            assert pipeline._runtime_archive_stale(str(archive)) is True


def test_pcc_python_runtime_objects_do_not_depend_on_module_main_globals():
    build_dir = PY_RUNTIME_DIR / "build_py"
    if not build_dir.exists():
        pytest.skip(f"runtime build dir missing: {build_dir}")

    offenders: list[str] = []
    for module in _active_python_runtime_modules():
        ll_path = build_dir / f"{module}.ll"
        if not ll_path.exists():
            continue
        text = ll_path.read_text(encoding="utf-8")
        if ".modvar." in text:
            offenders.append(str(ll_path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_pcc_python_runtime_library_ir_has_no_program_main():
    build_dir = PY_RUNTIME_DIR / "build_py"
    if not build_dir.exists():
        pytest.skip(f"runtime build dir missing: {build_dir}")

    offenders: list[str] = []
    for module in _active_python_runtime_modules():
        ll_path = build_dir / f"{module}.ll"
        if not ll_path.exists():
            continue
        text = ll_path.read_text(encoding="utf-8")
        if re.search(r"define\s+i32\s+@main\(", text):
            offenders.append(str(ll_path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_pcc_python_runtime_makefile_uses_python_library_mode_not_text_stripping():
    makefile = (PY_RUNTIME_DIR / "Makefile").read_text(encoding="utf-8")
    assert "--python-library --emit-llvm=" in makefile
    assert "awk 'BEGIN{s=0}" not in makefile


def test_python_library_mode_emits_module_without_program_main(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "libmod.py"
    out_ll = tmp_path / "libmod.ll"
    src.write_text(
        "def exported() -> int:\n"
        "    return 7\n",
        encoding="utf-8",
    )

    compile_python(
        str(src),
        str(out_ll),
        emit_llvm_only=True,
        python_library=True,
    )

    ir_text = out_ll.read_text(encoding="utf-8")
    assert "define i32 @main(" not in ir_text
    assert re.search(
        r"define(?:\s+external)?\s+void\s+@_pcc_py_module_top_libmod\(",
        ir_text,
    )


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


def test_pcc_python_archive_uses_python_py_coroutine_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_coroutine.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_coroutine.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_coroutine_run" in out
    assert "_py_await" in out
    assert "_user_py_coroutine__checked_coroutine" in out


def test_pcc_python_archive_uses_python_py_func_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_func.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_func.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_func_new" in out
    assert "_py_func_call" in out
    assert "_py_dealloc_func" in out
    assert "_user_py_func__checked_func" in out


def test_pcc_python_archive_uses_python_py_re_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_re.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_re.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_re_match" in out
    assert "_user_py_re__match_here" in out
    assert "_user_py_re__atom_matches" in out


def test_pcc_python_archive_uses_python_py_dunder_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_dunder.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_dunder.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_int_to_str_obj" in out
    assert "_py_int_format_hex" in out
    assert "_py_user_str_dispatch" in out
    assert "_user_py_dunder__store_rev_hex_digits" in out


def test_pcc_python_archive_uses_python_py_file_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_file.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_file.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_file_open" in out
    assert "_py_file_read_all" in out
    assert "_py_file_write" in out
    assert "_user_py_file__checked_file" in out


def test_pcc_python_archive_uses_python_py_os_substrate_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_os_substrate.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_os_substrate.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_path_stat_kind" in out
    assert "_py_path_stat_mtime" in out
    assert "_py_os_getcwd_str" in out
    assert "_user_py_os_substrate__str_from_cstr" in out


def test_pcc_python_archive_uses_python_py_process_substrate_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    subprocess.run(
        ["ar", "x", str(archive), "py_process_substrate.o"],
        check=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    result = subprocess.run(
        ["nm", "-g", "py_process_substrate.o"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "_py_subprocess_check_output" in out
    assert "_py_os_listdir" in out
    assert "_py_tempdir_cleanup" in out
    assert "_user_py_process_substrate__build_shell_command" in out


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


def test_pcc_python_relocate_copy_rejects_oversized_copy(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    harness = tmp_path / "relocate_copy_size_harness.c"
    harness.write_text(
        """
        #include "pcc/py_runtime/include/py_runtime.h"
        #include <stdio.h>
        #include <stdlib.h>

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            PyObject *old = pcc_gc_alloc(24, PY_TYPE_FLOAT, 0);
            if (old == NULL) return 2;
            printf("selected=%lld\\n", (long long)pcc_gc_select_relocation_set(1));
            PyObject *moved = pcc_gc_relocate_copy(old, 32);
            printf("oversize_null=%d\\n", moved == NULL);
            if (moved != NULL) {
                pcc_gc_release(moved);
            }
            pcc_gc_release(old);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocate_copy_size_harness"
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
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == "selected=1\noversize_null=1\n"


def test_pcc_python_relocate_copy_consumes_relocation_entry(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    harness = tmp_path / "relocate_copy_single_forward_harness.c"
    harness.write_text(
        """
        #include "pcc/py_runtime/include/py_runtime.h"
        #include <stdio.h>
        #include <stdlib.h>

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            PyObject *old = pcc_gc_alloc(24, PY_TYPE_FLOAT, 0);
            if (old == NULL) return 2;
            pcc_gc_reset_relocation_set();
            pcc_gc_select_relocation_set(1);
            PyObject *moved = pcc_gc_relocate_copy(old, 24);
            printf("first_null=%d\\n", moved == NULL);
            printf("still_selected=%lld\\n", (long long)pcc_gc_relocation_set_contains(old));
            PyObject *moved_again = pcc_gc_relocate_copy(old, 24);
            printf("second_null=%d\\n", moved_again == NULL);
            if (moved_again != NULL) {
                pcc_gc_release(moved_again);
            }
            if (moved != NULL) {
                pcc_gc_release(moved);
            }
            pcc_gc_release(old);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocate_copy_single_forward_harness"
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
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == (
        "first_null=0\n"
        "still_selected=0\n"
        "second_null=1\n"
    )


def test_pcc_python_relocating_step_copies_simple_object(tmp_path):
    archive = PY_RUNTIME_DIR / "libpy_runtime_pcc_py.a"
    if not archive.exists():
        pytest.skip(f"runtime archive missing: {archive}")

    harness = tmp_path / "relocating_step_harness.c"
    harness.write_text(
        """
        #include "pcc/py_runtime/include/py_runtime.h"
        #include <stdio.h>
        #include <stdlib.h>

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            PyObject *old = pcc_gc_alloc(24, PY_TYPE_FLOAT, 0);
            if (old == NULL) return 2;
            int64_t old_id = pcc_gc_object_id(old);
            PyObject *slot = NULL;
            pcc_gc_store_ptr(NULL, &slot, old);
            pcc_gc_telemetry_reset();
            printf("step=%lld\\n", (long long)pcc_gc_step(2));
            printf("forwards=%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS));
            PyObject *loaded = pcc_gc_load_ptr(NULL, &slot);
            printf("same_old=%d\\n", loaded == old);
            printf("same_id=%d\\n", pcc_gc_object_id(loaded) == old_id);
            printf("barrier_forwards=%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS));
            pcc_gc_store_ptr(NULL, &slot, NULL);
            pcc_gc_release(old);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocating_step_harness"
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
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == (
        "step=2\n"
        "forwards=1\n"
        "same_old=0\n"
        "same_id=1\n"
        "barrier_forwards=1\n"
    )


def test_pcc_python_traceback_helpers_ignore_ambient_exception_tls(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_exc_traceback.py"
    ll = tmp_path / "py_exc_traceback.ll"
    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    ir_text = ll.read_text()
    match = re.search(
        r"define (?:external )?i64 @user_py_exc_traceback__is_exception"
        r"\([^)]*\)[^{]*\{(?P<body>.*?)\n\}",
        ir_text,
        re.S,
    )
    assert match is not None
    assert "py_err_occurred" not in match.group("body")


def test_pcc_python_dict_lookup_masks_signed_hash_perturb(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_dict.py"
    ll = tmp_path / "py_dict.ll"
    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    ir_text = ll.read_text()
    match = re.search(
        r"define (?:external )?i64 @user_py_dict__lookup"
        r"\([^)]*\)[^{]*\{(?P<body>.*?)\n\}",
        ir_text,
        re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "9223372036854775807" in body


def test_pcc_python_set_lookup_masks_signed_hash_perturb(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_set.py"
    ll = tmp_path / "py_set.ll"
    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    ir_text = ll.read_text()
    # ``_lookup_slot`` takes the set ``s`` as its first parameter
    # (``_lookup_slot(s, entries, capacity, hash_val, key)`` in
    # py_set.py); the old regex required the signature to start
    # with ``ptr ... %entries`` which assumed only one ptr arg
    # ahead of ``%entries``. Accept the current 5-param shape.
    match = re.search(
        r"define (?:external )?i64 @user_py_set__lookup_slot"
        r"\(ptr %s, ptr %entries, i64 %capacity, i64 %hash_val, "
        r"ptr %key\)"
        r"[^\n]* \{(?P<body>.*?)\n\}",
        ir_text,
        re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "9223372036854775807" in body


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
