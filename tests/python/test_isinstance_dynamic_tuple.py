"""Runtime classinfo tuples must behave like the literal tuple fast path."""

from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("runtime_kind", ["c", "py"])
def test_isinstance_dynamic_nested_tuple(tmp_path: Path, request, monkeypatch, runtime_kind):
    from pcc.py_frontend.pipeline import compile_python

    archive = request.getfixturevalue(
        "c_runtime_archive" if runtime_kind == "c" else "pcc_py_runtime_archive"
    )
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    source = tmp_path / "classinfo.py"
    executable = tmp_path / "classinfo"
    source.write_text('''from dataclasses import dataclass
@dataclass(frozen=True)
class Base:
    name: str
@dataclass(frozen=True)
class Child(Base):
    pass
TYPES = (str, (Base, int))
EMPTY = ()
def check(value, classes):
    print(isinstance(value, classes))
check(Child("child"), TYPES)
check("text", TYPES)
check(True, TYPES)
check(1.5, TYPES)
check(Child("empty"), EMPTY)
''', encoding="utf-8")
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on", libpython_mode="off")
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.splitlines() == ["True", "True", "True", "False", "False"]
