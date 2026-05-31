"""POSIX os.path module constants lowered natively, no-libpython.

``os.path.sep`` / ``extsep`` / ``pathsep`` / ``devnull`` / ``curdir`` /
``pardir`` / ``altsep`` are plain POSIX constants; emitting them as native
literals keeps ``os.path.<const>`` off the libpython fallback (a generic
B-P0-PKG fallback shrink). This runtime test verifies the VALUES match CPython
on ``--backend self --python-libpython=off`` (``os.path.defpath`` is
intentionally NOT lowered — its value is platform/build-variable — so it is not
asserted here).
"""
from __future__ import annotations
import os, subprocess


def test_os_path_constants_match_cpython(tmp_path):
    src = (
        "import os\n"
        "def main():\n"
        "    print(os.path.sep)\n"
        "    print(os.path.extsep)\n"
        "    print(os.path.pathsep)\n"
        "    print(os.path.devnull)\n"
        "    print(os.path.curdir)\n"
        "    print(os.path.pardir)\n"
        "    print(repr(os.path.altsep))\n"
        "    print(os.path.curdir + os.path.sep + 'x')\n"
        "main()\n"
    )
    main = tmp_path / "main.py"
    main.write_text(src, encoding="utf-8")
    exe = tmp_path / "bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Strict no-libpython: a CPython fallback for any constant would error
    # PCC-PY-COMPILE-001 at build time.
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(main), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "/", ".", ":", "/dev/null", ".", "..", "None", "./x",
    ], r.stdout
