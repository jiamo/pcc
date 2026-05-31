from __future__ import annotations

from pcc.py_stdlib import io
from pcc.py_stdlib import math
from pcc.py_stdlib import os as pcc_os
from pcc.py_stdlib import re


def test_os_path_string_helpers():
    p = pcc_os.path
    assert p.join("a", "b", "c") == "a/b/c"
    assert p.join("a", "/b", "c") == "/b/c"
    assert p.basename("/a/b.txt") == "b.txt"
    assert p.dirname("/a/b.txt") == "/a"
    assert p.splitext("/a/b.txt") == ("/a/b", ".txt")
    assert p.normpath("a/./b/../c") == "a/c"
    assert p.isabs("/x")
    assert p.commonpath(["a/b/c", "a/b/d"]) == "a/b"


def test_stringio_and_bytesio_seek_tell_readline():
    s = io.StringIO("hello\nworld")
    assert s.readline() == "hello\n"
    assert s.tell() == 6
    s.seek(0)
    assert s.read(5) == "hello"
    s.write("!")
    assert s.getvalue().startswith("hello!")

    b = io.BytesIO(b"abc\ndef")
    assert b.readline() == b"abc\n"
    b.seek(0, 2)
    b.write(b"!")
    assert b.getvalue().endswith(b"!")


def test_re_literal_subset():
    assert re.match("abc", "abcdef").group(0) == "abc"
    assert re.match("^abc$", "abc").span() == (0, 3)
    assert re.fullmatch("abc", "abc").group() == "abc"
    assert re.search("bc", "abcd").start() == 1
    assert re.findall("a", "banana") == ["a", "a", "a"]
    assert re.sub("cat", "dog", "cat cat") == "dog dog"
    assert re.split(",", "a,b,c") == ["a", "b", "c"]
    assert re.escape("a+b") == "a\\+b"


def test_math_non_extern_helpers():
    assert math.isnan(math.nan)
    assert math.isinf(math.inf)
    assert math.isfinite(3.0)
    assert math.trunc(3.8) == 3
    assert math.copysign(2.0, -1.0) == -2.0
    assert math.prod([2, 3, 4]) == 24
    assert math.factorial(5) == 120
    assert round(math.radians(180.0), 6) == round(math.pi, 6)
