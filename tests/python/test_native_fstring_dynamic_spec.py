"""Dynamic f-string format spec (nested {field}) under strict no-libpython.

f"{v:>{w}}" (dynamic width) and f"{v:.{p}f}" (dynamic precision) raised
``ValueError: unsupported format specifier``: the parser stored the format spec
as a flat string (">{w}") and lift emitted format(v, ">{w}") — the nested {w}
was never evaluated.

Fix (lift-level, py_lift.py, no parser-grammar change): _fstring_spec_to_expr
parses a spec containing a nested bare-identifier field into a runtime
concatenation (e.g. ">{w}" -> ">" + str(w)); the assembled spec string is then
passed to format(). {{/}} are literal braces; a non-identifier field keeps the
static spec (prior behaviour).

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


def test_dynamic_width_and_precision(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    w = 6\n"
        "    p = 3\n"
        "    print(f'[{42:>{w}}]')\n"        # [    42]
        "    print(f'[{42:<{w}}]')\n"        # [42    ]
        "    print(f'[{99:^{w}}]')\n"        # [  99  ]
        "    print(f'{3.14159:.{p}f}')\n"    # 3.142
        "    print(f'{255:0{w}x}')\n"        # 0000ff
        "main()\n")
    assert out.split("\n")[:5] == [
        "[    42]", "[42    ]", "[  99  ]", "3.142", "0000ff",
    ], out


def test_static_spec_regression(tmp_path):
    # Specs without a nested field must be unchanged.
    out = _run(tmp_path,
        "def main():\n"
        "    print(f'{42:>6}')\n"        # '    42'
        "    print(f'{3.14159:.2f}')\n"  # 3.14
        "    print(f'{255:#06x}')\n"     # 0x00ff
        "    print(f'{1000000:,}')\n"    # 1,000,000
        "main()\n")
    assert out.split("\n")[:4] == [
        "    42", "3.14", "0x00ff", "1,000,000",
    ], out
