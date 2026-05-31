"""CPython parity for ``import`` statements, ported from
``Lib/test/test_import.py``.

All tests use ``libpython_mode="off"``.

Reference contract (from CPython):
  * ``import X`` binds ``X`` to a module object
  * ``from X import Y`` binds ``Y`` to whatever ``X.Y`` resolves to
  * ``import X as Z`` binds the alias only; the original name isn't
  * ``import X.Y`` binds ``X`` (parent), not ``X.Y``
  * after import, ``module.attr`` reads the module slot
  * ``module.attr = val`` writes the module slot for native builtin modules

pcc gates on the recursive native stdlib closure: ``import math``,
``import json``, ``from os import path``, etc. resolve to
``pcc/py_stdlib/<name>.py`` first, host CPython's stdlib only as a
last resort, and never to libpython-fallback under ``mode="off"``.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


def _compile(monkeypatch, src: Path, exe: Path) -> None:
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off",
    )


def _run(exe: Path, timeout: float = 30.0) -> str:
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=timeout,
    )
    assert result.returncode == 0, (
        f"{exe.name} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def test_import_math_floor_sqrt(tmp_path, monkeypatch):
    src = tmp_path / "imp_math.py"
    exe = tmp_path / "imp_math.out"
    src.write_text(textwrap.dedent("""
        import math

        def main() -> None:
            print(math.floor(3.7))
            print(math.floor(-3.2))
            print(int(math.sqrt(16.0)))
            print(int(math.sqrt(2.0) * 1000))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    out = _run(exe).strip().splitlines()
    assert out[0] == "3"
    assert out[1] == "-4"
    assert out[2] == "4"
    # sqrt(2) ≈ 1.414213562 → 1414
    assert out[3] == "1414"


def test_import_math_prod_iterable(tmp_path, monkeypatch):
    src = tmp_path / "imp_math_prod.py"
    exe = tmp_path / "imp_math_prod.out"
    src.write_text(textwrap.dedent("""
        import math

        def main() -> None:
            print(math.prod([2, 3, 4]))
            print(math.prod([2, 3], start=5))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    out = _run(exe).strip().splitlines()
    assert out == ["24", "30"]


def test_from_import(tmp_path, monkeypatch):
    src = tmp_path / "imp_from.py"
    exe = tmp_path / "imp_from.out"
    src.write_text(textwrap.dedent("""
        from math import floor, sqrt

        def main() -> None:
            print(floor(5.9))
            print(int(sqrt(25.0)))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == ["5", "5"]


def test_import_as_alias(tmp_path, monkeypatch):
    src = tmp_path / "imp_as.py"
    exe = tmp_path / "imp_as.out"
    src.write_text(textwrap.dedent("""
        import math as m

        def main() -> None:
            print(m.floor(7.5))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip() == "7"


def test_from_import_as_alias(tmp_path, monkeypatch):
    src = tmp_path / "imp_from_as.py"
    exe = tmp_path / "imp_from_as.out"
    src.write_text(textwrap.dedent("""
        from math import floor as fl

        def main() -> None:
            print(fl(9.9))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip() == "9"


def test_import_inside_function_body(tmp_path, monkeypatch):
    src = tmp_path / "imp_local.py"
    exe = tmp_path / "imp_local.out"
    src.write_text(textwrap.dedent("""
        def main() -> None:
            import math
            print(math.floor(2.7))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip() == "2"


def test_import_os_path(tmp_path, monkeypatch):
    src = tmp_path / "imp_os.py"
    exe = tmp_path / "imp_os.out"
    src.write_text(textwrap.dedent("""
        from os import path

        def main() -> None:
            print(path.join("a", "b"))
            print(path.basename("/tmp/foo.txt"))

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip().splitlines() == ["a/b", "foo.txt"]


def test_import_json_loads_dumps(tmp_path, monkeypatch):
    src = tmp_path / "imp_json.py"
    exe = tmp_path / "imp_json.out"
    src.write_text(textwrap.dedent("""
        import json

        def main() -> None:
            d = json.loads('{"a": 1, "b": 2}')
            print(d["a"], d["b"])
            s = json.dumps({"x": 10})
            print(s)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    out = _run(exe).strip().splitlines()
    assert out[0] == "1 2"
    # CPython's default dumps spacing:
    assert out[1] == '{"x": 10}'


def test_module_attribute_write(tmp_path, monkeypatch):
    src = tmp_path / "imp_attr_write.py"
    exe = tmp_path / "imp_attr_write.out"
    src.write_text(textwrap.dedent("""
        import sys

        def main() -> None:
            sys.my_added = 42
            print(sys.my_added)

        if __name__ == "__main__":
            main()
        """).lstrip())
    _compile(monkeypatch, src, exe)
    assert _run(exe).strip() == "42"
