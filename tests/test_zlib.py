import os

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import collect_translation_units, translation_unit_include_dirs


PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
PROJECTS_DIR = os.path.join(PROJECT_DIR, "projects")
ZLIB_DIR = os.path.join(PROJECTS_DIR, "zlib-1.3.1")
ZLIB_TEST_MAIN = os.path.join(PROJECTS_DIR, "test_zlib_main.c")
ZLIB_CPP_ARGS = (
    "-DHAVE_UNISTD_H",
    "-DHAVE_STDARG_H",
    "-U__ARM_FEATURE_CRC32",
)

pytestmark = pytest.mark.xdist_group(name="vendor_builds")


def _zlib_units():
    return collect_translation_units(
        ZLIB_TEST_MAIN,
        dependencies=[f"{ZLIB_DIR}=libz.a"],
    )


@pytest.fixture(scope="module")
def zlib_compiled_units():
    units, base_dir = _zlib_units()
    compiled_units = CEvaluator().compile_translation_units(
        units,
        base_dir=base_dir,
        jobs=2,
        include_dirs=translation_unit_include_dirs(units),
        cpp_args=ZLIB_CPP_ARGS,
    )
    return compiled_units, base_dir


@pytest.mark.skipif(not os.path.isdir(ZLIB_DIR), reason="zlib-1.3.1 not found")
def test_zlib_make_goal_dependency_collects_library_sources():
    units, base_dir = _zlib_units()

    names = [unit.name for unit in units]

    assert base_dir == os.path.abspath(PROJECTS_DIR)
    assert names[-1] == "test_zlib_main.c"
    assert "deflate.c" in names
    assert "inflate.c" in names
    assert "zutil.c" in names
    assert "example.c" not in names
    assert "minigzip.c" not in names


@pytest.mark.skipif(not os.path.isdir(ZLIB_DIR), reason="zlib-1.3.1 not found")
def test_zlib_runtime_with_mcjit_depends_on(zlib_compiled_units):
    compiled_units, _base_dir = zlib_compiled_units

    result = CEvaluator().evaluate_compiled_translation_units(
        compiled_units,
        optimize=True,
    )

    assert result == 0


@pytest.mark.skipif(not os.path.isdir(ZLIB_DIR), reason="zlib-1.3.1 not found")
def test_zlib_runtime_with_system_link_depends_on(zlib_compiled_units):
    compiled_units, base_dir = zlib_compiled_units

    result = CEvaluator().run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
    )

    assert (
        result.returncode == 0
    ), f"zlib system-link runtime failed:\n{result.stdout}\n{result.stderr}"
    assert "compress/uncompress: hello, hello!" in result.stdout
    assert "deflate/inflate: hello, hello!" in result.stdout
    assert "OK" in result.stdout
