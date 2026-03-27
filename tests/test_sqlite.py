import os
import sqlite3

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import collect_translation_units, translation_unit_include_dirs


PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
PROJECTS_DIR = os.path.join(PROJECT_DIR, "projects")
SQLITE_DIR = os.path.join(PROJECTS_DIR, "sqlite-amalgamation-3490100")
SQLITE_TEST_MAIN = os.path.join(PROJECTS_DIR, "test_sqlite_main.c")
SQLITE_CPP_ARGS = (
    "-U__APPLE__",
    "-U__MACH__",
    "-U__DARWIN__",
    "-DSQLITE_THREADSAFE=0",
    "-DSQLITE_OMIT_WAL=1",
    "-DSQLITE_MAX_MMAP_SIZE=0",
)

pytestmark = pytest.mark.xdist_group(name="sqlite")


def _sqlite_units():
    return collect_translation_units(
        SQLITE_TEST_MAIN,
        dependencies=[os.path.join(SQLITE_DIR, "sqlite3.c")],
    )


def _sqlite_db_path(tmp_path):
    return tmp_path / "runtime.sqlite3"


def _assert_sqlite_db_contents(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "select id, name, score from t order by id"
        ).fetchall()
        assert rows == [(1, "hello", 17), (2, "world", 20)]

        count, total, maxlen = conn.execute(
            "select count(*), sum(score), max(length(name)) from t"
        ).fetchone()
        assert (count, total, maxlen) == (2, 37, 5)
    finally:
        conn.close()


@pytest.fixture(scope="module")
def sqlite_compiled_units():
    units, base_dir = _sqlite_units()
    compiled_units = CEvaluator().compile_translation_units(
        units,
        base_dir=base_dir,
        jobs=2,
        include_dirs=translation_unit_include_dirs(units),
        cpp_args=SQLITE_CPP_ARGS,
    )
    return compiled_units, base_dir


@pytest.mark.skipif(not os.path.isdir(SQLITE_DIR), reason="sqlite-amalgamation-3490100 not found")
def test_sqlite_runtime_with_mcjit_depends_on(tmp_path, sqlite_compiled_units):
    compiled_units, _base_dir = sqlite_compiled_units
    db_path = _sqlite_db_path(tmp_path)

    result = CEvaluator().evaluate_compiled_translation_units(
        compiled_units,
        optimize=True,
        prog_args=[str(db_path)],
    )

    assert result == 0
    assert db_path.exists()
    _assert_sqlite_db_contents(db_path)


@pytest.mark.skipif(not os.path.isdir(SQLITE_DIR), reason="sqlite-amalgamation-3490100 not found")
def test_sqlite_runtime_with_system_link_depends_on(tmp_path, sqlite_compiled_units):
    compiled_units, base_dir = sqlite_compiled_units
    db_path = _sqlite_db_path(tmp_path)

    result = CEvaluator().run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
        prog_args=[str(db_path)],
    )

    assert (
        result.returncode == 0
    ), f"sqlite system-link runtime failed:\n{result.stdout}\n{result.stderr}"
    assert "sqlite version " in result.stdout
    assert "insert rowids: 1 2" in result.stdout
    assert "changes: insert=1 update=1" in result.stdout
    assert "selected row: world 20" in result.stdout
    assert "aggregate: count=2 sum=37 maxlen=5" in result.stdout
    assert "updated score: 17" in result.stdout
    assert "persisted score: 17" in result.stdout
    assert "OK" in result.stdout
    assert db_path.exists()
    _assert_sqlite_db_contents(db_path)


@pytest.mark.skipif(not os.path.isdir(SQLITE_DIR), reason="sqlite-amalgamation-3490100 not found")
def test_sqlite_depends_on_collects_amalgamation_and_main():
    units, base_dir = _sqlite_units()

    names = [unit.name for unit in units]

    assert base_dir == os.path.abspath(PROJECTS_DIR)
    assert names == ["sqlite3.c", "test_sqlite_main.c"]
