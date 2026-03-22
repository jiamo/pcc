import os

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import collect_translation_units


PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
LUA_PROJECT_DIR = os.path.join(PROJECT_DIR, "projects", "lua-5.5.0")
LUA_MATH_TEST = os.path.join(LUA_PROJECT_DIR, "testes", "math.lua")


def _evaluate_project(path, jobs=1, optimize=False, prog_args=None, sources_from_make=None):
    units, base_dir = collect_translation_units(
        path, sources_from_make=sources_from_make
    )
    return CEvaluator().evaluate_translation_units(
        units,
        optimize=optimize,
        base_dir=base_dir,
        prog_args=prog_args or [],
        jobs=jobs,
    )


def test_separate_tus_links_across_files(tmp_path):
    (tmp_path / "helper.c").write_text("int helper(void) { return 41; }\n")
    (tmp_path / "main.c").write_text(
        "int helper(void);\nint main(void) { return helper() + 1; }\n"
    )

    assert _evaluate_project(str(tmp_path), jobs=1) == 42


def test_separate_tus_resolves_extern_globals(tmp_path):
    (tmp_path / "common.h").write_text("extern int shared;\n")
    (tmp_path / "helper.c").write_text(
        '#include "common.h"\nint read_shared(void) { return shared; }\n'
    )
    (tmp_path / "main.c").write_text(
        '#include "common.h"\n'
        "int read_shared(void);\n"
        "int shared = 9;\n"
        "int main(void) { return read_shared(); }\n"
    )

    assert _evaluate_project(str(tmp_path), jobs=1) == 9


def test_separate_tus_reject_duplicate_external_functions(tmp_path):
    (tmp_path / "left.c").write_text("int helper(void) { return 11; }\n")
    (tmp_path / "right.c").write_text(
        "int helper(void) { return 31; }\n"
        "int main(void) { return helper(); }\n"
    )

    with pytest.raises(ValueError, match="duplicate external function definition"):
        _evaluate_project(str(tmp_path), jobs=2)


def test_separate_tus_rejects_lua_tree_with_onelua_directory():
    with pytest.raises(ValueError, match="duplicate external function definition"):
        _evaluate_project(LUA_PROJECT_DIR, jobs=2, optimize=False)


def test_separate_tus_keep_file_scope_static_symbols_distinct(tmp_path):
    (tmp_path / "left.c").write_text(
        "static int value = 17;\n"
        "static int helper(void) { return value; }\n"
        "int left(void) { return helper(); }\n"
    )
    (tmp_path / "right.c").write_text(
        "static int value = 25;\n"
        "static int helper(void) { return value; }\n"
        "int right(void) { return helper(); }\n"
    )
    (tmp_path / "main.c").write_text(
        "int left(void);\nint right(void);\nint main(void) { return left() + right(); }\n"
    )

    assert _evaluate_project(str(tmp_path), jobs=2) == 42


def test_separate_tus_lua_math_runtime_jobs1():
    assert _evaluate_project(
        LUA_PROJECT_DIR,
        jobs=1,
        optimize=True,
        prog_args=[LUA_MATH_TEST],
        sources_from_make="lua",
    ) == 0


def test_separate_tus_lua_math_runtime_jobs2():
    assert _evaluate_project(
        LUA_PROJECT_DIR,
        jobs=2,
        optimize=True,
        prog_args=[LUA_MATH_TEST],
        sources_from_make="lua",
    ) == 0
