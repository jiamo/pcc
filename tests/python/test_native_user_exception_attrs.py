"""raise UserExceptionSubclass(args) preserves __init__ attributes, str() and
args under strict no-libpython.

`raise MyError(404, "not found")` for a custom exception with __init__ used to
skip __init__ entirely (the raise path called py_exc_new_with_class(cls,
args[0]-as-message)), so `e.code` -> AttributeError and str(e) was the code, not
the message. Fix: the raise path constructs the instance via emit_instantiate
(runs __init__); py_raise_normalize keeps the instance as-is; exc_to_class
projects instances to their class for except-matching; super().__init__(*args)
stores `args` (so str(e)/e.args work); py_obj_str gives the BaseException
message for an exc-subclass instance; and a no-__init__ subclass stores its
constructor args too.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path


def _run(tmp_path, source):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self","--python-libpython=off","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_user_exception_attrs_and_str(tmp_path):
    out = _run(tmp_path,
        "class MyError(Exception):\n"
        "    def __init__(self, code, msg):\n"
        "        super().__init__(msg)\n"
        "        self.code = code\n"
        "class PlainErr(Exception):\n"
        "    pass\n"
        "def main():\n"
        "    try:\n"
        "        raise MyError(404, 'not found')\n"
        "    except MyError as e:\n"
        "        print(e.code, str(e))\n"            # 404 not found
        "    try:\n"
        "        raise MyError(500, 'boom')\n"
        "    except Exception as e:\n"               # base-class catch
        "        print('base', e.code, str(e))\n"    # base 500 boom
        "    e2 = MyError(1, 'direct')\n"
        "    print(e2.code, str(e2), e2.args)\n"     # 1 direct ('direct',)
        "    try:\n"
        "        raise PlainErr('plain msg')\n"       # no-__init__ subclass
        "    except PlainErr as e:\n"
        "        print(str(e), e.args)\n"            # plain msg ('plain msg',)
        "main()\n")
    assert out.split("\n")[:4] == [
        "404 not found",
        "base 500 boom",
        "1 direct ('direct',)",
        "plain msg ('plain msg',)",
    ], out
