from __future__ import annotations

from pcc.py_stdlib import base64
from pcc.py_stdlib import hashlib
from pcc.py_stdlib import pathlib
from pcc.py_stdlib import string


def test_pathlib_purepath_operations(tmp_path):
    p = pathlib.PurePath("a", "b", "file.txt")
    assert str(p).endswith("a/b/file.txt")
    assert p.name == "file.txt"
    assert p.stem == "file"
    assert p.suffix == ".txt"
    assert p.with_suffix(".md").name == "file.md"
    assert p.with_name("other.py").name == "other.py"
    assert p.match("*.txt")

    f = pathlib.Path(str(tmp_path / "x.txt"))
    assert f.write_text("hello") == 5
    assert f.read_text() == "hello"
    assert f.exists()


def test_base64_standard_and_urlsafe_roundtrip():
    raw = b"hello?\xff"
    encoded = base64.b64encode(raw)
    assert encoded == b"aGVsbG8//w=="
    assert base64.b64decode(encoded) == raw
    url = base64.urlsafe_b64encode(raw)
    assert b"/" not in url
    assert base64.urlsafe_b64decode(url) == raw


def test_hashlib_sha256_known_vectors_and_copy():
    h = hashlib.sha256()
    h.update(b"ab")
    h2 = h.copy()
    h.update(b"c")
    h2.update(b"d")
    assert h.hexdigest() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert h2.hexdigest() != h.hexdigest()
    assert hashlib.new("sha256", b"abc").digest() == h.digest()


def test_string_template_and_capwords():
    assert string.capwords("hello world") == "Hello World"
    t = string.Template("$greeting, ${name}!")
    assert t.substitute(greeting="hi", name="pcc") == "hi, pcc!"
    assert string.Template("$missing").safe_substitute({}) == "$missing"
    assert string.Template("$$x").substitute({}) == "$x"
