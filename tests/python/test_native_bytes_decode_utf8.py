"""bytes.decode("utf-8") (with an explicit encoding arg) under no-libpython.

`b.decode()` (no args) worked, but `b.decode("utf-8")` — the common explicit
form — raised NotImplementedError("bytes.decode() with arguments is not
supported yet"). Since pcc str is UTF-8 internally and decode() defaults to
utf-8, an explicit "utf-8" encoding (+ optional "strict" errors) is identical
to the no-arg form.

Fix (frontend): accept a literal utf-8 encoding ("utf-8"/"UTF-8"/"utf8",
positional or `encoding=` kwarg) and an optional "strict" errors arg, routing to
the existing py_bytes_decode; any other encoding / error mode still falls back.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
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
    # Decode the program's stdout as UTF-8 explicitly: the test programs print
    # non-ASCII (e.g. "café", 0xc3 0xa9), and a plain text=True relies on the
    # parent locale — under LC_ALL=C that is ASCII and raises UnicodeDecodeError
    # on `uv run pytest`.
    run = subprocess.run(
        [str(exe)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_bytes_decode_utf8_arg_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    b = 'caf\\u00e9'.encode('utf-8')\n"
        "    enc = 'utf-8'\n"
        "    print(b.decode())\n"  # café
        "    print(b.decode('utf-8'))\n"  # café
        "    print(b.decode('UTF-8'))\n"  # café
        "    print(b.decode('utf8'))\n"  # café
        "    print(b.decode('utf-8', 'strict'))\n"  # café
        "    print(b'a\\xffb'.decode('utf8', 'ignore'))\n"  # ab
        "    print(b.decode(encoding='utf-8'))\n"  # café
        "    print(b'hello'.decode('utf-8'))\n"  # hello
        "    print(b'hello'.decode('utf-8') == 'hello')\n"  # True
        "    print(b.decode(enc))\n"  # dynamic encoding
        "    print(str(b, enc))\n"  # two-argument str(bytes, encoding)
        "    print(str(b, enc, 'strict'))\n"  # three-argument form
        "main()\n",
    )
    assert out.split("\n")[:12] == [
        "café",
        "café",
        "café",
        "café",
        "café",
        "ab",
        "café",
        "hello",
        "True",
        "café",
        "café",
        "café",
    ], out


def test_class_constructor_decode_method_is_not_lowered_as_bytes(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "class Decoder:\n"
        "    def decode(self, value):\n"
        "        return 'decoded:' + value\n"
        "def loads(value, cls=None, **kwargs):\n"
        "    if cls is None:\n"
        "        cls = Decoder\n"
        "    return cls().decode(value)\n"
        "print(loads('text'))\n",
    )
    assert out == "decoded:text\n"


def test_bytes_maketrans_translate_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    table = bytes.maketrans(b'abc', b'xyz')\n"
        "    print(b'cab'.translate(table).decode())\n"
        "    print(bytes.translate(b'abc', table).decode())\n"
        "    print(bytearray(b'cab').translate(table).decode())\n"
        "    inv = bytes.maketrans(bytes(range(256)), bytes([255 - i for i in range(256)]))\n"
        "    print(bytes.translate(b'\\x00\\xffA', inv).hex())\n"
        "main()\n",
    )
    assert out.split("\n")[:4] == ["zxy", "xyz", "zxy", "ff00be"], out


def test_bytes_fromhex_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(bytes.fromhex('4865 6c6c6f').decode())\n"
        "    raw = b'/41%42%43'\n"
        "    print(bytes.fromhex(raw[1:].replace(b'%', b'').decode()).decode())\n"
        "main()\n",
    )
    assert out.split("\n")[:2] == ["Hello", "ABC"], out
