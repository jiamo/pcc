"""Native default-form ``functools.update_wrapper`` metadata copying."""
from __future__ import annotations

import os
import subprocess


def test_functools_update_wrapper_default_form_no_libpython(tmp_path):
    source = tmp_path / "prog.py"
    source.write_text(
        "import functools\n"
        "class Wrapper:\n"
        "    pass\n"
        "def original():\n"
        "    return 7\n"
        "wrapper = Wrapper()\n"
        "result = functools.update_wrapper(wrapper, original)\n"
        "print(result is wrapper)\n"
        "print(wrapper.__name__)\n"
        "print(wrapper.__wrapped__ is original)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=20, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["True", "original", "True"]


def test_functools_update_wrapper_from_returned_nested_function_no_libpython(
    tmp_path,
):
    source = tmp_path / "prog.py"
    source.write_text(
        "import functools\n"
        "class Wrapper:\n"
        "    pass\n"
        "def original():\n"
        "    return 7\n"
        "def factory():\n"
        "    def decorator(wrapper, wrapped):\n"
        "        return functools.update_wrapper(wrapper, wrapped)\n"
        "    return functools.partial(decorator)\n"
        "wrapper = Wrapper()\n"
        "result = factory()(wrapper, original)\n"
        "print(result is wrapper)\n"
        "print(wrapper.__name__)\n"
        "print(wrapper.__wrapped__ is original)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=20, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["True", "original", "True"]
