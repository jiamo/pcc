"""Stress tests for the 3952f6e5 pcc_gc_store_ptr migration.

These tests target the container helpers whose signature changed from
``py_incref(item) + slot=item`` to ``pcc_gc_store_ptr(owner, &slot, item)``.
The migration is supposed to be refcount-equivalent for backend 0
(refcount-cycle, default), but the self-host pcc1 binary crashes with
heap corruption while compiling itself, which strongly suggests one
of the migrated helpers has skewed refcount accounting somewhere.

These tests build a small C harness against ``libpy_runtime.a`` and
run it under default (production) malloc — the same allocator setting
that triggers the pcc1 crash.  If a refcount is off by one, default
malloc's nano allocator will reuse freed chunks aggressively and the
patterns below should crash.

We intentionally don't run under MallocScribble / libgmalloc because
both make the corruption invisible (which is one of the diagnostic
signals: the pcc1 crash also disappears under those modes).
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import textwrap

import pytest

from tests.runtime_build_cache import cache_runtime_build


REPO_ROOT = Path(__file__).absolute().parents[2]


@cache_runtime_build
def _build_runtime(tmp_path: Path) -> Path:
    runtime = REPO_ROOT / "pcc" / "py_runtime"
    work = tmp_path / "py_runtime"
    shutil.copytree(
        runtime,
        work,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    make = subprocess.run(
        ["make", "-B", "-C", str(work), "libpy_runtime.a"],
        capture_output=True, text=True, timeout=120,
    )
    assert make.returncode == 0, make.stdout + make.stderr
    return work


def _compile_run(tmp_path: Path, c_src: str, name: str) -> subprocess.CompletedProcess:
    runtime = _build_runtime(tmp_path)
    src = tmp_path / f"{name}.c"
    exe = tmp_path / f"{name}.out"
    src.write_text(c_src, encoding="utf-8")
    cc = os.environ.get("CC", "cc")
    build = subprocess.run(
        [
            cc, "-std=c11",
            f"-I{runtime / 'include'}",
            str(src), str(runtime / "libpy_runtime.a"),
            "-o", str(exe),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert build.returncode == 0, build.stderr
    # Default malloc — no MallocScribble / libgmalloc. We want the same
    # nano-allocator reuse pattern that triggers the pcc1 crash.
    env = {k: v for k, v in os.environ.items() if not k.startswith("Malloc") and "DYLD_INSERT" not in k}
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, env=env)


def test_dict_update_replaces_value_without_uaf(tmp_path):
    """py_dict_set update path: store_ptr replaces an existing value.

    Pattern: dict[k] = v1; dict[k] = v2; v1 should be freed cleanly
    when v2 replaces it. Caller's borrowed ref to v1 (via dict_get)
    must keep v1 alive across the update."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        int main(void) {
            for (int round = 0; round < 5000; round++) {
                PyObject *d = py_dict_new();
                PyObject *k = py_str_new("k", 1);
                PyObject *v1 = py_str_new("v1", 2);
                py_dict_set(d, k, v1);
                /* Borrow v1 back, then replace. v1 must outlive the replace
                 * because we hold a borrowed ref via dict_get. */
                PyObject *got = py_dict_get(d, k);  /* increfs */
                PyObject *v2 = py_str_new("v2", 2);
                py_dict_set(d, k, v2);  /* store_ptr decrefs old v1 */
                /* got still points to v1 and must be valid. */
                if (got == NULL) return 1;
                py_decref(got);
                py_decref(v2);
                py_decref(v1);
                py_decref(k);
                py_decref(d);
            }
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "dict_update")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_instance_field_replacement_does_not_uaf(tmp_path):
    """py_instance_set_field via pcc_gc_store_ptr: replacing field
    value must decref old without disturbing borrowed refs."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>
        #include <string.h>

        struct PyClassObject;
        struct PyInstanceObject;
        struct PyClassObject *py_class_new(const char *name,
            struct PyClassObject **bases, int n_bases,
            const char **field_names, int n_fields);
        PyObject *py_instance_new(struct PyClassObject *cls);
        PyObject *py_instance_get_field(struct PyInstanceObject *inst, int idx);
        void py_instance_set_field(struct PyInstanceObject *inst, int idx, PyObject *value);

        int main(void) {
            const char *names[] = {"f0"};
            struct PyClassObject *cls = py_class_new("C", NULL, 0, names, 1);
            for (int round = 0; round < 5000; round++) {
                PyObject *inst = py_instance_new(cls);
                PyObject *a = py_str_new("a", 1);
                py_instance_set_field((struct PyInstanceObject *)inst, 0, a);
                py_decref(a);
                /* Borrow back: get_field increfs. */
                PyObject *got = py_instance_get_field((struct PyInstanceObject *)inst, 0);
                PyObject *b = py_str_new("b", 1);
                py_instance_set_field((struct PyInstanceObject *)inst, 0, b);
                py_decref(b);
                /* got still valid. */
                if (got == NULL) return 1;
                py_decref(got);
                py_decref(inst);
            }
            py_decref((PyObject *)cls);
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "instance_field")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_set_add_then_drop_does_not_uaf(tmp_path):
    """Many sets created, populated, freed. Tests the
    rehash + free path after pcc_gc_store_ptr migration."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        int main(void) {
            for (int round = 0; round < 1000; round++) {
                PyObject *s = py_set_new();
                /* Cross capacity boundary to force rehash. */
                for (int i = 0; i < 32; i++) {
                    char buf[16];
                    int n = snprintf(buf, sizeof buf, "i%d_%d", round, i);
                    PyObject *str = py_str_new(buf, n);
                    py_set_add(s, str);
                    py_decref(str);
                }
                py_decref(s);
            }
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "set_stress")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_list_set_replaces_without_uaf(tmp_path):
    """py_list_set goes through pcc_gc_store_ptr — old value at slot
    must get decref'd, new value must hold +1 ref."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        int main(void) {
            for (int round = 0; round < 5000; round++) {
                PyObject *l = py_list_new(0);
                PyObject *a = py_str_new("a", 1);
                py_list_append(l, a);
                py_decref(a);
                /* Borrow via list_get. */
                PyObject *got = py_list_get(l, 0);
                PyObject *b = py_str_new("b", 1);
                py_list_set(l, 0, b);  /* decrefs a; a survives via got's ref */
                py_decref(b);
                if (got == NULL) return 1;
                py_decref(got);
                py_decref(l);
            }
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "list_set")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_nested_instances_chain_does_not_corrupt_heap(tmp_path):
    """Mirror the lifter pattern: deeply nested instance creation with
    instance-typed fields (Return.value -> Expr -> Name etc).

    Each iteration creates a chain Inst3 -> Inst2 -> Inst1 -> str, then
    drops the head; refcounts must cascade to 0 cleanly without poisoning
    the free list."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        struct PyClassObject;
        struct PyInstanceObject;
        struct PyClassObject *py_class_new(const char *name,
            struct PyClassObject **bases, int n_bases,
            const char **field_names, int n_fields);
        PyObject *py_instance_new(struct PyClassObject *cls);
        void py_instance_set_field(struct PyInstanceObject *inst, int idx, PyObject *value);

        int main(void) {
            const char *names[] = {"f0"};
            struct PyClassObject *cls = py_class_new("Node", NULL, 0, names, 1);

            for (int round = 0; round < 5000; round++) {
                /* Build a chain: head -> mid -> leaf -> "leaf-str" */
                PyObject *leaf_str = py_str_new("L", 1);
                PyObject *leaf = py_instance_new(cls);
                py_instance_set_field((struct PyInstanceObject *)leaf, 0, leaf_str);
                py_decref(leaf_str);

                PyObject *mid = py_instance_new(cls);
                py_instance_set_field((struct PyInstanceObject *)mid, 0, leaf);
                py_decref(leaf);

                PyObject *head = py_instance_new(cls);
                py_instance_set_field((struct PyInstanceObject *)head, 0, mid);
                py_decref(mid);

                /* Drop head — should cascade-free the entire chain. */
                py_decref(head);
            }
            py_decref((PyObject *)cls);
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "nested_chain")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_tuple_with_instance_fields_freed_via_dealloc(tmp_path):
    """py_tuple_dealloc decrefs each item.  After 3952f6e5,
    ``py_tuple_set_item`` goes through pcc_gc_store_ptr.  The dealloc
    must still see exactly +1 ref per item to balance."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        struct PyClassObject;
        struct PyInstanceObject;
        struct PyClassObject *py_class_new(const char *name,
            struct PyClassObject **bases, int n_bases,
            const char **field_names, int n_fields);
        PyObject *py_instance_new(struct PyClassObject *cls);

        int main(void) {
            struct PyClassObject *cls = py_class_new("Item", NULL, 0, NULL, 0);
            for (int round = 0; round < 5000; round++) {
                PyObject *t = py_tuple_new(8);
                for (int i = 0; i < 8; i++) {
                    PyObject *inst = py_instance_new(cls);
                    py_tuple_set_item(t, i, inst);
                    /* drop our ref; tuple now holds the only ref */
                    py_decref(inst);
                }
                py_decref(t);  /* dealloc must decref each item to 0 */
            }
            py_decref((PyObject *)cls);
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "tuple_inst")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_dict_set_then_replace_under_pressure(tmp_path):
    """Mirror the symbol-table pattern: many dict[k]=v operations,
    sometimes replacing existing keys.  The replace path goes through
    pcc_gc_store_ptr and must decref the old value once."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        int main(void) {
            for (int round = 0; round < 1000; round++) {
                PyObject *d = py_dict_new();
                /* Insert + replace many times to trigger rehash + replace. */
                for (int i = 0; i < 32; i++) {
                    char buf[16];
                    int n = snprintf(buf, sizeof buf, "k%d", i);
                    PyObject *k = py_str_new(buf, n);
                    PyObject *v = py_str_new(buf, n);
                    py_dict_set(d, k, v);
                    py_decref(k);
                    py_decref(v);
                }
                /* Replace each existing key. */
                for (int i = 0; i < 32; i++) {
                    char buf[16];
                    int n = snprintf(buf, sizeof buf, "k%d", i);
                    PyObject *k = py_str_new(buf, n);
                    PyObject *v = py_str_new("X", 1);
                    py_dict_set(d, k, v);
                    py_decref(k);
                    py_decref(v);
                }
                py_decref(d);
            }
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "dict_replace")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_func_new_with_caller_supplied_captures(tmp_path):
    """py_func_new: when caller passes a captures tuple, the tuple
    should end up with rc=2 (caller + func)."""
    c = textwrap.dedent(r"""
        #include "py_runtime.h"
        #include <stdio.h>

        static PyObject *trampoline(PyObject *captures, PyObject *args) {
            (void)captures; (void)args;
            return py_None;
        }

        int main(void) {
            for (int round = 0; round < 5000; round++) {
                PyObject *captures = py_tuple_new(2);
                PyObject *a = py_str_new("a", 1);
                PyObject *b = py_str_new("b", 1);
                py_tuple_set_item(captures, 0, a);
                py_tuple_set_item(captures, 1, b);
                py_decref(a);
                py_decref(b);
                PyObject *fn = py_func_new((void *)trampoline, captures);
                /* Caller decrefs their own ref; func should still own one. */
                py_decref(captures);
                py_decref(fn);
            }
            puts("ok");
            return 0;
        }
        """).lstrip()
    r = _compile_run(tmp_path, c, "func_captures")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
