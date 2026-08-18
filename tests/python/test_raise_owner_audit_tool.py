"""Contract for scripts/raise_owner_audit.py.

The tool decides which `py_raise` sites may be rewritten to `py_raise_owned`.
A false positive here is a double free, so the classifier's boundaries are
pinned by the four shapes that actually occur in this runtime.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).absolute().parents[2]
_spec = importlib.util.spec_from_file_location(
    "raise_owner_audit", REPO_ROOT / "scripts" / "raise_owner_audit.py"
)
audit_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_mod)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_inline_forms_are_counted_including_multiline(tmp_path):
    src = _write(tmp_path, "a.c", """
void f(void) {
    py_raise(py_exc_new(3, "one"));
    py_raise(
        py_exc_new(3, "two")
    );
    py_raise_owned(py_exc_new(3, "already correct"));
}
""")
    a = audit_mod.audit(src)
    assert a["inline_single"] == 1
    assert len(a["inline_multi"]) == 1


def test_fresh_variable_is_convertible(tmp_path):
    src = _write(tmp_path, "b.py", """
def g():
    exc = py_exc_new(3, null())
    py_raise(exc)
    return null()
""")
    sites = audit_mod.audit(src)["variables"]
    assert [(s["var"], s["fresh"], s["released"]) for s in sites] == [
        ("exc", True, False)
    ]


def test_released_site_is_recognised_under_any_variable_name(tmp_path):
    """The earlier hand scan looked for the literal `py_decref(exc)` and so
    miscounted a correct site whose variable was named `stop`."""
    src = _write(tmp_path, "c.py", """
def g():
    stop = py_exc_new(8, null())
    py_raise(stop)
    py_decref(stop)
    return null()
""")
    sites = audit_mod.audit(src)["variables"]
    assert sites[0]["var"] == "stop"
    assert sites[0]["released"] is True


def test_borrowed_parameter_is_not_marked_fresh(tmp_path):
    """py_gen.py:272 shape: the exception is the caller's, not ours."""
    src = _write(tmp_path, "d.py", """
def py_gen_throw(gen, exc):
    _set_send_value(gen, global_load_ptr("py_None"))
    py_raise(exc)
    return null()
""")
    sites = audit_mod.audit(src)["variables"]
    assert sites[0]["var"] == "exc"
    assert sites[0]["fresh"] is False


def test_rooted_value_is_not_marked_fresh(tmp_path):
    """py_gen.py:404 shape: the root owns the reference."""
    src = _write(tmp_path, "e.py", """
def close(gen_slot, saved_slot):
    saved = load_ptr(saved_slot, 0)
    if ptr_is_null(saved) == 0:
        py_raise(saved)
    pcc_gc_store_root(saved_slot, null())
""")
    sites = audit_mod.audit(src)["variables"]
    assert sites[0]["var"] == "saved"
    assert sites[0]["fresh"] is False


def test_convert_rewrites_only_the_safe_shapes(tmp_path):
    src = _write(tmp_path, "f.py", """
py_raise = extern("py_raise", (c_ptr,), c_void)


def g(borrowed):
    py_raise(py_exc_new(3, null()))
    exc = py_exc_new(3, null())
    py_raise(exc)
    py_raise(borrowed)
""")
    audit_mod.convert(src)
    out = src.read_text(encoding="utf-8")
    assert "py_raise_owned(py_exc_new(3, null()))" in out
    assert "py_raise_owned(exc)" in out
    # The borrowed parameter must survive untouched.
    assert "py_raise(borrowed)" in out
    # The extern declaration is added exactly once for non-C files.
    assert out.count('py_raise_owned = extern("py_raise_owned"') == 1


def test_convert_does_not_add_an_extern_to_c_sources(tmp_path):
    src = _write(tmp_path, "g.c", 'void f(void) { py_raise(py_exc_new(3, "x")); }\n')
    audit_mod.convert(src)
    out = src.read_text(encoding="utf-8")
    assert "py_raise_owned(py_exc_new(" in out
    assert "extern(" not in out


def test_no_extern_is_added_when_nothing_needs_converting(tmp_path):
    """py_exc_tls.py shape: it *defines* py_raise, has nothing to convert, and
    must not grow an extern declaration (there is none to anchor to)."""
    src = _write(tmp_path, "h.py", """
@c_abi_export("py_raise")
def py_raise(exc) -> None:
    pass


def g():
    exc = py_exc_new(3, null())
    py_raise(exc)
    py_decref(exc)
""")
    before = src.read_text(encoding="utf-8")
    audit_mod.convert(src)
    assert src.read_text(encoding="utf-8") == before


def test_release_several_lines_after_the_raise_is_recognised(tmp_path):
    """A cleanup sequence can put the decref well after the raise.  A short
    lookahead would call this site unreleased, convert it, and double free."""
    src = _write(tmp_path, "i.py", """
def g():
    exc = py_exc_new(3, null())
    py_raise(exc)
    cleanup_a()
    cleanup_b()
    cleanup_c()
    py_decref(exc)
    return null()
""")
    sites = audit_mod.audit(src)["variables"]
    assert sites[0]["var"] == "exc"
    assert sites[0]["released"] is True

    before = src.read_text(encoding="utf-8")
    audit_mod.convert(src)
    assert src.read_text(encoding="utf-8") == before, "converted a released site"


def test_release_in_a_later_function_does_not_count(tmp_path):
    """The search must stop at the end of the enclosing function."""
    src = _write(tmp_path, "j.py", """
py_raise = extern("py_raise", (c_ptr,), c_void)


def g():
    exc = py_exc_new(3, null())
    py_raise(exc)
    return null()


def h(exc):
    py_decref(exc)
""")
    sites = audit_mod.audit(src)["variables"]
    g_site = [s for s in sites if s["line"] < 10][0]
    assert g_site["released"] is False
    assert g_site["fresh"] is True


def test_sibling_method_is_not_treated_as_the_same_function(tmp_path):
    """A class method's boundary is the next def at the same indent, not
    column 0.  Treating only column 0 as a boundary made ``b``'s decref look
    like it released ``a``'s exception, hiding a real leak."""
    src = _write(tmp_path, "k.py", """
class C:
    def a(self):
        exc = py_exc_new(3, null())
        py_raise(exc)

    def b(self):
        py_decref(exc)
""")
    sites = audit_mod.audit(src)["variables"]
    assert len(sites) == 1
    assert sites[0]["var"] == "exc"
    assert sites[0]["fresh"] is True
    assert sites[0]["released"] is False, "sibling method's decref counted"


def test_release_in_the_same_method_still_counts(tmp_path):
    src = _write(tmp_path, "l.py", """
class C:
    def a(self):
        exc = py_exc_new(3, null())
        py_raise(exc)
        cleanup()
        py_decref(exc)
""")
    sites = audit_mod.audit(src)["variables"]
    assert sites[0]["released"] is True
