import os

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import collect_project, collect_translation_units


PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
LUA_WITH_ONELUA_DIR = os.path.join(PROJECT_DIR, "projects", "lua-5.5.0")
LUA_MATH_TEST = os.path.join(LUA_WITH_ONELUA_DIR, "testes", "math.lua")


def test_collect_translation_units_accepts_with_onelua_directory():
    units, base_dir = collect_translation_units(LUA_WITH_ONELUA_DIR)

    assert base_dir == os.path.abspath(LUA_WITH_ONELUA_DIR)
    assert any(unit.name == "onelua.c" for unit in units)
    assert units[-1].name == "lua.c"


def test_collect_translation_units_accepts_make_selected_lua_sources():
    units, base_dir = collect_translation_units(
        LUA_WITH_ONELUA_DIR, sources_from_make="lua"
    )

    assert base_dir == os.path.abspath(LUA_WITH_ONELUA_DIR)
    assert units[-1].name == "lua.c"
    assert all(unit.name != "onelua.c" for unit in units)


def test_collect_translation_units_rejects_multiple_mains(tmp_path):
    (tmp_path / "a.c").write_text("int main(void) { return 1; }\n")
    (tmp_path / "b.c").write_text("int main(void) { return 2; }\n")

    with pytest.raises(ValueError, match="Multiple main"):
        collect_translation_units(str(tmp_path))


def test_collect_project_merged_directory_reports_same_tu_redefinitions(tmp_path):
    (tmp_path / "helper.c").write_text("int helper(void) { return 1; }\n")
    (tmp_path / "main.c").write_text(
        '#include "helper.c"\nint main(void) { return helper(); }\n'
    )

    source, base_dir = collect_project(str(tmp_path))

    with pytest.raises(ValueError, match="redefinition of function 'helper'"):
        CEvaluator().evaluate(source, base_dir=base_dir)


def test_system_cpp_does_not_leave_temp_c_files_in_source_dir(tmp_path):
    (tmp_path / "local.h").write_text("#define VALUE 42\n")
    before = sorted(p.name for p in tmp_path.iterdir())

    output = CEvaluator._system_cpp(
        '#include "local.h"\nint main(void) { return VALUE; }\n',
        base_dir=str(tmp_path),
    )

    after = sorted(p.name for p in tmp_path.iterdir())

    assert "return 42;" in output
    assert after == before


def test_collect_project_make_goal_filters_c_sources(tmp_path):
    (tmp_path / "helper.c").write_text("int helper(void) { return 41; }\n")
    (tmp_path / "main.c").write_text(
        "int helper(void);\nint main(void) { return helper() + 1; }\n"
    )
    (tmp_path / "ignored.c").write_text("int ignored(void) { return 99; }\n")
    (tmp_path / "Makefile").write_text(
        "app: helper.o main.o\n"
        "\tcc -o app helper.o main.o\n\n"
        "helper.o: helper.c\n"
        "\tcc -c -o helper.o helper.c\n\n"
        "main.o: main.c\n"
        "\tcc -c -o main.o main.c\n\n"
        "ignored.o: ignored.c\n"
        "\tcc -c -o ignored.o ignored.c\n"
    )

    source, base_dir = collect_project(str(tmp_path), sources_from_make="app")

    assert base_dir == os.path.abspath(str(tmp_path))
    assert "// --- helper.c ---" in source
    assert "// --- ignored.c ---" not in source
    assert CEvaluator().evaluate(source, base_dir=base_dir) == 42


def test_collect_project_make_selected_lua_sources_runtime():
    source, base_dir = collect_project(LUA_WITH_ONELUA_DIR, sources_from_make="lua")

    assert "// --- onelua.c ---" not in source
    assert CEvaluator().evaluate(
        source,
        base_dir=base_dir,
        prog_args=[LUA_MATH_TEST],
    ) == 0
