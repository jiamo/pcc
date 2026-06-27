from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _build(tmp_path: Path, source: str) -> tuple[Path, dict[str, str]]:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
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
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    return exe, env


def _build_and_run(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    exe, env = _build(tmp_path, source)
    return subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )


def test_builtin_int_in_dynamic_ternary_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import re\n"
        "def parse(port, default_port):\n"
        "    return int(port) if port else default_port\n"
        "def main():\n"
        "    match = re.match('(.*):(\\d+)$', '127.0.0.1:8081')\n"
        "    port = match.groups()[1]\n"
        "    print(parse(port, 10))\n"
        "    print(parse('', 10))\n"
        "main()\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["8081", "10"]


def test_type_equals_builtin_str_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "def main():\n"
        "    text = 'abc'\n"
        "    other = 7\n"
        "    print(type(text) == str)\n"
        "    print(type(other) == str)\n"
        "    print(type(text) != str)\n"
        "    print(type(text) is str)\n"
        "    print(type(other) is str)\n"
        "    print(type(text) is not str)\n"
        "main()\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "True",
        "False",
        "False",
        "True",
        "False",
        "False",
    ]


def test_functools_partial_with_bound_kwargs_no_libpython(tmp_path: Path):
    _build(
        tmp_path,
        "import functools\n"
        "def handler(value, client_side=True, stream_handler=None):\n"
        "    print(value)\n"
        "def marker(value):\n"
        "    return value\n"
        "def main():\n"
        "    p = functools.partial(handler, client_side=False, stream_handler=marker)\n"
        "    print(p is not None)\n"
        "main()\n",
    )


def test_min_max_promotes_boxed_int_literal_with_float_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "def main():\n"
        "    errwait = 2\n"
        "    errwait = min(errwait * 1.3 + 0.1, 30)\n"
        "    cap = max(30, errwait)\n"
        "    print(errwait)\n"
        "    print(cap)\n"
        "main()\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["2.7", "30.0"]


def test_dynamic_string_replace_does_not_route_to_bytes_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "class Token:\n"
        "    def __init__(self, text):\n"
        "        self.text = text\n"
        "def clean(t):\n"
        "    return t.text.replace('_', '')\n"
        "def main():\n"
        "    print(clean(Token('a_b_c')))\n"
        "main()\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["abc"]
