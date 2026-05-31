"""os.path.relpath lowered natively, no-libpython.

``os.path.relpath(path[, start])`` previously fell back to libpython. The native
lowering (native_os.py dispatch) wraps BOTH arguments in ``os.path.abspath`` and
calls a pure component-diff C helper (``py_os_path_relpath`` in py_os_native.c),
which normalises '.'/'..' while splitting (so it is correct even though the
native abspath only cwd-prefixes and does not itself run normpath). This runtime
test verifies the VALUES match CPython on ``--backend self
--python-libpython=off`` (a generic B-P0-PKG fallback shrink).
"""
from __future__ import annotations
import os, subprocess


def _run(tmp_path, body):
    src = "import os\ndef main():\n" + body + "main()\n"
    main = tmp_path / "main.py"
    main.write_text(src, encoding="utf-8")
    exe = tmp_path / "bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(main), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_relpath_two_arg_absolute(tmp_path):
    out = _run(
        tmp_path,
        "    print(os.path.relpath('/a/b/c', '/a'))\n"       # b/c
        "    print(os.path.relpath('/a/b', '/a/b'))\n"       # .
        "    print(os.path.relpath('/a/x', '/a/b/c'))\n"     # ../../x
        "    print(os.path.relpath('/usr/local/bin', '/usr/bin'))\n"  # ../local/bin
        "    print(os.path.relpath('/a/b/c/', '/a/'))\n"     # b/c (trailing slashes)
    )
    assert out[:5] == ["b/c", ".", "../../x", "../local/bin", "b/c"], out


def test_relpath_dotdot_normalised(tmp_path):
    # '.' / '..' inside the inputs are resolved (native abspath cwd-prefixes but
    # does not normpath; the C helper normalises while splitting).
    out = _run(
        tmp_path,
        "    print(os.path.relpath('/a/./b/../c', '/a'))\n"   # c
        "    print(os.path.relpath('/a/b/c', '/a/x/../b'))\n" # c
    )
    assert out[:2] == ["c", "c"], out


def test_relpath_one_arg_defaults_to_cwd(tmp_path):
    # 1-arg form: start defaults to os.curdir -> cwd, must match CPython.
    out = _run(
        tmp_path,
        "    print(os.path.relpath('foo/bar'))\n"             # foo/bar
        "    print(os.path.relpath(os.getcwd() + '/sub/x'))\n"  # sub/x
    )
    assert out[:2] == ["foo/bar", "sub/x"], out
