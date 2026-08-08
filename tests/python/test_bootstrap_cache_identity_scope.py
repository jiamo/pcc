"""The frontend IR cache namespace must not be the whole source tree.

Per-module cache keys already bind the module's own sources, its transitive
imports, the compiler binary's own sha256, the target, and the options. The
identity on top of that is a *namespace*, and hashing all 999 bootstrap-relevant
files into it meant an edit anywhere -- a GUI file, a runtime C source, a
documentation-only tool -- invalidated every cached frontend IR module.

`pcc/backend/self_backend_cache_identity.py` already solved this for the object
cache and says so in its docstring: "Hashing the whole compiler source tree here
would invalidate every object after an unrelated frontend, package, runtime, or
documentation change." These tests hold the frontend identity to the same rule.
"""

from __future__ import annotations

import pcc.bootstrap_cache_identity as identity


def _identity_with(tmp_path, rel_writes):
    root = tmp_path
    (root / "pcc").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "bootstrap.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    for rel, text in rel_writes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return identity.bootstrap_source_sha256(root)


def test_frontend_sources_change_the_identity(tmp_path):
    base = {
        "pcc/py_frontend/type_infer.py": "x = 1\n",
        "pcc/gui/window.py": "gui = 1\n",
    }
    before = _identity_with(tmp_path, base)
    after = _identity_with(
        tmp_path, {**base, "pcc/py_frontend/type_infer.py": "x = 2\n"}
    )
    assert before != after, "a frontend edit must change the namespace"


def test_unrelated_subtrees_do_not_change_the_identity(tmp_path):
    """A GUI or runtime-C edit cannot change frontend IR, so it must not
    invalidate every cached frontend module."""
    base = {
        "pcc/py_frontend/type_infer.py": "x = 1\n",
        "pcc/gui/window.py": "gui = 1\n",
        "pcc/py_runtime/src/py_obj.c": "int a;\n",
    }
    before = _identity_with(tmp_path, base)
    for rel, changed in (
        ("pcc/gui/window.py", "gui = 2\n"),
        ("pcc/py_runtime/src/py_obj.c", "int a; int b;\n"),
    ):
        after = _identity_with(tmp_path, {**base, rel: changed})
        assert before == after, f"{rel} must not invalidate frontend IR cache"
