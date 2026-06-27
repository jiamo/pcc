from __future__ import annotations

import os
import subprocess


def test_dynamic_list_sort_with_callable_key_at_module_scope(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "def rank(value):\n"
        "    return value\n"
        "groups = {'numbers': {3, 1, 2}}\n"
        "for group_name in groups.keys():\n"
        "    values = list(groups[group_name])\n"
        "    values.sort(key=lambda value: rank(value))\n"
        "print(values)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    compile_result = subprocess.run(
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
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.strip() == "[1, 2, 3]"


def test_sorted_iterable_with_named_callable_key_at_module_scope(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "def rank(value):\n"
        "    return value\n"
        "values = dict.fromkeys([3, 1, 2])\n"
        "ordered = [0] + sorted(values, key=rank)\n"
        "print(ordered)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    compile_result = subprocess.run(
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
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.strip() == "[0, 1, 2, 3]"
