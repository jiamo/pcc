import os

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import (
    collect_cpp_args,
    collect_translation_units,
    translation_unit_include_dirs,
)


PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
PROJECTS_DIR = os.path.join(PROJECT_DIR, "projects")
ZSTD_LIB_DIR = os.path.join(PROJECTS_DIR, "zstd-1.5.6", "lib")
ZSTD_TEST_MAIN = os.path.join(PROJECTS_DIR, "test_zstd_main.c")
ZSTD_MAKE_GOAL = "libzstd.a-release"


def _zstd_cpp_args():
    return tuple(collect_cpp_args(ZSTD_LIB_DIR, sources_from_make=ZSTD_MAKE_GOAL))


def _zstd_units():
    return collect_translation_units(
        ZSTD_TEST_MAIN,
        dependencies=[f"{ZSTD_LIB_DIR}={ZSTD_MAKE_GOAL}"],
    )


@pytest.fixture(scope="module")
def zstd_compiled_units():
    units, base_dir = _zstd_units()
    compiled_units = CEvaluator().compile_translation_units(
        units,
        base_dir=base_dir,
        jobs=2,
        include_dirs=translation_unit_include_dirs(units),
        cpp_args=_zstd_cpp_args(),
    )
    return compiled_units, base_dir


@pytest.mark.skipif(not os.path.isdir(ZSTD_LIB_DIR), reason="zstd-1.5.6/lib not found")
def test_zstd_runtime_with_mcjit_depends_on(zstd_compiled_units):
    compiled_units, _base_dir = zstd_compiled_units

    result = CEvaluator().evaluate_compiled_translation_units(
        compiled_units,
        optimize=True,
    )

    assert result == 0


@pytest.mark.skipif(not os.path.isdir(ZSTD_LIB_DIR), reason="zstd-1.5.6/lib not found")
def test_zstd_runtime_with_system_link_depends_on(zstd_compiled_units):
    compiled_units, base_dir = zstd_compiled_units

    result = CEvaluator().run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
        timeout=180,
    )

    assert (
        result.returncode == 0
    ), f"zstd system-link runtime failed:\n{result.stdout}\n{result.stderr}"
    assert "zstd version 1.5.6" in result.stdout
    assert "roundtrip: hello, zstd!" in result.stdout
    assert "OK" in result.stdout


@pytest.mark.skipif(not os.path.isdir(ZSTD_LIB_DIR), reason="zstd-1.5.6/lib not found")
def test_zstd_make_goal_dependency_collects_library_sources():
    units, base_dir = _zstd_units()

    names = [unit.name for unit in units]

    assert base_dir == os.path.abspath(PROJECTS_DIR)
    assert names[-1] == "test_zstd_main.c"
    assert "common/debug.c" in names
    assert "compress/zstd_compress.c" in names
    assert "decompress/zstd_decompress.c" in names
    assert "dictBuilder/zdict.c" in names
    assert "programs/zstdcli.c" not in names
    assert "tests/fuzzer.c" not in names
