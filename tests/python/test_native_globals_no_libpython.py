from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_globals_items_sees_classes_and_dynamic_subscript_store(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
class Base:
    @classmethod
    def name(cls):
        return cls.__name__

class A_Cipher(Base):
    pass

globals()["B_Cipher"] = type("B_Cipher", (A_Cipher,), {})

MAP = {cls.name(): cls for name, cls in globals().items()
       if name.endswith("_Cipher")}

print("A_Cipher" in MAP)
print("B_Cipher" in MAP)
print(len(MAP))
""",
    )
    assert out.split() == ["True", "True", "2"]


def test_globals_membership_excludes_later_module_assignment(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
print("before", "_is_loaded" in globals())
_is_loaded = True
print("after", "_is_loaded" in globals())
""",
    )
    assert out.splitlines() == ["before False", "after True"]


def test_bare_name_reads_dynamic_globals_subscript_store(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
globals()["dynamic_value"] = 42

def read_dynamic_value():
    return dynamic_value

print(dynamic_value)
print(read_dynamic_value())
try:
    print(missing_dynamic_value)
except NameError:
    print("missing")
""",
    )
    assert out.splitlines() == ["42", "42", "missing"]


def test_class_attribute_lambda_binds_as_instance_method_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
class C:
    F = lambda self, x, y=4: x + y

    def call(self):
        return self.F(5)

print(C().call())
""",
    )
    assert out.split() == ["9"]


def test_native_module_dynamic_int_equality_stays_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
import winreg

def probe():
    _data, regtype = winreg.QueryValueEx("root", "name")
    if regtype == winreg.REG_BINARY:
        return 1
    if winreg.REG_BINARY == regtype:
        return 2
    return 3

print("compiled")
""",
    )
    assert out.split() == ["compiled"]


def test_abs_dynamic_argument_stays_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
def magnitude(value):
    return abs(value)

print(magnitude(-7))
print(magnitude(3))
print(magnitude(-2.5))
""",
    )
    assert out.split() == ["7", "3", "2.5"]


def test_dynamic_type_constructor_name_variable_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        """
class Base:
    pass

name = "Dynamic"
method = Base
Dynamic = type(name, (method,), dict(KEY_LENGTH=32, IV_LENGTH=16))

print(Dynamic.__name__)
print(Dynamic.KEY_LENGTH)
print(Dynamic.IV_LENGTH)
""",
    )
    assert out.split() == ["Dynamic", "32", "16"]
