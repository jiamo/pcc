"""Native ``collections.abc`` import and Mapping mixin surface."""
from __future__ import annotations

import os
import subprocess


def test_collections_abc_mapping_surface_no_libpython(tmp_path):
    source = tmp_path / "prog.py"
    source.write_text(
        "from collections.abc import Callable, Collection, Iterator, Mapping, Sequence\n"
        "class Values(Mapping):\n"
        "    def __init__(self):\n"
        "        self.data = {'a': 1, 'b': 2}\n"
        "    def __getitem__(self, key):\n"
        "        return self.data[key]\n"
        "    def __iter__(self):\n"
        "        return iter(self.data)\n"
        "    def __len__(self):\n"
        "        return len(self.data)\n"
        "values = Values()\n"
        "print(values.get('a'), values.get('z', 9))\n"
        "print(values.keys())\n"
        "print(values.items())\n"
        "print(values.values())\n"
        "print(Callable.__name__, Collection.__name__, Iterator.__name__, Sequence.__name__)\n",
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
    assert run.stdout.splitlines() == [
        "1 9",
        "['a', 'b']",
        "[('a', 1), ('b', 2)]",
        "[1, 2]",
        "Callable Collection Iterator Sequence",
    ]


def test_sys_version_guarded_collections_abc_class_alias_no_libpython(tmp_path):
    source = tmp_path / "prog.py"
    source.write_text(
        "import sys\n"
        "if sys.version_info >= (3, 12):\n"
        "    from collections.abc import Buffer as _Buffer\n"
        "else:\n"
        "    class _Buffer:\n"
        "        pass\n"
        "print(_Buffer.__name__)\n",
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
    assert run.stdout.strip() == "Buffer"
