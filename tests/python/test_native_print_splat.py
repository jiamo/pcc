"""print(*items) positional splat under strict no-libpython.

print(*items) / print(a, *rest, b) failed at runtime with
"NameError: name '*'": the *m splat (lifted as Call(Name("*"),(m,))) reached the
single-/multi-arg print paths, which emitted it as a plain Name("*") lookup. The
builtin print lowering never expanded the splat (only user/object calls did).

Fix (frontend): _emit_print_call routes a starred-arg print to
_emit_print_many_splat, which builds the positional args as a runtime list
(_emit_pcc_args_list expands the splat) and converts it to a tuple via the
existing py_call_merge_posargs helper, then hands it to py_print_many like the
fixed-arity path. Only sep/end kwargs are handled here.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""
from __future__ import annotations
import os, subprocess


def _run(tmp_path, source):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self","--python-libpython=off","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_print_splat(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    items = [1, 2, 3]\n"
        "    print(*items)\n"                  # 1 2 3
        "    print(*[10, 20, 30])\n"           # 10 20 30
        "    print('x', *items, 'y')\n"        # x 1 2 3 y
        "    print(*range(3))\n"               # 0 1 2
        "main()\n")
    assert out.split("\n")[:4] == [
        "1 2 3", "10 20 30", "x 1 2 3 y", "0 1 2",
    ], out


def test_print_splat_sep_end(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    items = [1, 2, 3]\n"
        "    print(*items, sep=', ')\n"                    # 1, 2, 3
        "    print(*['a', 'b'], sep='-', end='!\\n')\n"    # a-b!
        "main()\n")
    assert out.split("\n")[:2] == ["1, 2, 3", "a-b!"], out


def test_print_no_splat_regression(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(1, 2, 3)\n"        # 1 2 3
        "    print('hello')\n"        # hello
        "    print(42, sep='|')\n"    # 42
        "main()\n")
    assert out.split("\n")[:3] == ["1 2 3", "hello", "42"], out
