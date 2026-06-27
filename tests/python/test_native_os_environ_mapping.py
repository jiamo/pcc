"""os.environ CPython mapping semantics under no-libpython.

(S-P0-SELF-OS-ENVIRON-MAPPING) ``os.environ[key]`` used to lower to
``py_os_getenv`` with a ``None`` fallback, so a missing key printed
``None`` instead of raising, and ``os.environ[key] = value`` had no
native store hook at all. Added ``py_os_environ_getitem`` /
``py_os_environ_setitem`` (py_os_env.c + the pcc-Python port
py_os_env.py) with CPython mapping semantics:

- getitem raises KeyError (carrying the key) when the variable is unset
- getitem/setitem require str key/value (TypeError otherwise, like
  CPython's encodekey())
- setitem stores via setenv, so the update is visible to os.getenv
  (and child processes), matching CPython's putenv-backed __setitem__
- ``os.environ.get`` / ``os.getenv`` keep the non-raising default path

Compiles + runs under ``--backend self --python-libpython=off`` in
DEFAULT runtime mode (pcc-Python ports — the goal mode) and diffs the
program output against python3 running the same source.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROGRAM = (
    "import os\n"
    "def main():\n"
    "    os.environ['PCC_ENV_MAP_X'] = 'pcc-value'\n"
    "    print(os.environ['PCC_ENV_MAP_X'])\n"
    "    print(os.getenv('PCC_ENV_MAP_X'))\n"
    "    print('PCC_ENV_MAP_X' in os.environ)\n"
    "    print('PCC_ENV_MAP_MISSING' not in os.environ)\n"
    "    try:\n"
    "        os.environ['PCC_ENV_MAP_MISSING']\n"
    "    except KeyError:\n"
    "        print('keyerror-missing')\n"
    "    try:\n"
    "        os.environ['PCC_ENV_MAP_X'] = 2\n"
    "    except TypeError:\n"
    "        print('typeerror-value')\n"
    "    try:\n"
    "        os.environ[1]\n"
    "    except TypeError:\n"
    "        print('typeerror-key')\n"
    "    try:\n"
    "        1 in os.environ\n"
    "    except TypeError:\n"
    "        print('typeerror-contains-key')\n"
    "    print(os.environ.get('PCC_ENV_MAP_MISSING', 'fallback'))\n"
    "main()\n"
)

_EXPECTED = [
    "pcc-value",
    "pcc-value",
    "True",
    "True",
    "keyerror-missing",
    "typeerror-value",
    "typeerror-key",
    "typeerror-contains-key",
    "fallback",
]


def _clean_env() -> dict:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # The program asserts the missing-key KeyError path; make sure the
    # variable is really absent in the child.
    env.pop("PCC_ENV_MAP_MISSING", None)
    env.pop("PCC_ENV_MAP_X", None)
    return env


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = _clean_env()
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
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def _run_cpython_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(src)],
        text=True,
        capture_output=True,
        timeout=30,
        env=_clean_env(),
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_os_environ_mapping_no_libpython(tmp_path):
    out = _run_pcc_program(tmp_path, _PROGRAM)
    assert out.split("\n")[: len(_EXPECTED)] == _EXPECTED, out


def test_os_environ_mapping_matches_cpython(tmp_path):
    pcc_out = _run_pcc_program(tmp_path, _PROGRAM)
    cpy_out = _run_cpython_program(tmp_path, _PROGRAM)
    assert pcc_out == cpy_out, f"pcc:\n{pcc_out}\ncpython:\n{cpy_out}"
