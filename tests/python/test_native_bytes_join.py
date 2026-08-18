"""``bytes.join`` / ``bytearray.join`` under strict no-libpython (both tiers).

Before this, a statically typed ``b"".join(chunks)`` compiled but raised
``AttributeError: join`` at runtime inside a pcc1 worker (the self-backend
assembler's chunks-plus-join replacement for quadratic ``bytearray +=``
first hit it; pcc/py_stdlib zlib/lzma/bz2/hashlib use the same idiom).

Added ``py_bytes_join`` in BOTH tiers (cc ``src/py_bytes.c`` + pcc-Python port
``py/py_obj_stubs.py``), the header/ABI entries, and a typed frontend branch
for bytes/bytearray receivers.  A DynType ``.join`` deliberately stays on the
str path (same-name overlap; see ``_maybe_emit_bytes_method_via_dyn``).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

PROGRAM = textwrap.dedent("""
    def chunks(n: int) -> list:
        out = []
        i = 0
        while i < n:
            out.append(bytes([i & 255, (i >> 8) & 255]))
            i += 1
        return out

    def main() -> None:
        print(b"".join([b"ab", b"c", b"", b"def"]))
        print(b", ".join((b"x", b"y", b"z")))
        print(b"-".join([]))
        print(b"-".join([b"solo"]))
        print(b"|".join([bytearray(b"m"), b"n"]))
        joined_ba = bytearray(b"+").join([b"1", b"2"])
        print(type(joined_ba).__name__, bytes(joined_ba))
        big = b"".join(chunks(200000))
        print(len(big), big[:4], big[-4:])
        try:
            b"".join([b"ok", "not bytes"])
            print("no error")
        except TypeError:
            print("TypeError")

    if __name__ == "__main__":
        main()
    """).lstrip()


def _compile(tmp_path, monkeypatch, runtime_cc):
    if runtime_cc is not None:
        monkeypatch.setenv("PCC_RUNTIME_CC", runtime_cc)
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "bj.py"
    exe = tmp_path / "bj.out"
    src.write_text(PROGRAM, encoding="utf-8")
    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off", backend="self",
    )
    return src, exe


@pytest.mark.parametrize("runtime_cc", [None, "cc"], ids=["port", "cc"])
def test_bytes_join_matches_cpython(tmp_path, monkeypatch, runtime_cc):
    src, exe = _compile(tmp_path, monkeypatch, runtime_cc)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython


def test_bytes_join_port_binary_runs_under_every_gc_backend(tmp_path, monkeypatch):
    src, exe = _compile(tmp_path, monkeypatch, None)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    for backend in ("0", "1", "2", "3", "4"):
        env = os.environ.copy()
        env.pop("LC_ALL", None)
        env["PCC_GC_BACKEND"] = backend
        result = subprocess.run(
            [str(exe)], capture_output=True, text=True, timeout=60, env=env,
        )
        assert result.returncode == 0, (backend, result.stderr)
        assert result.stdout == cpython, backend
