"""Backend-4 remap plumbing: pcc_gc_update_referents (C harness).

Stage-2 plumbing from docs/plans/gc4-relocation-remap-plan.md: the
slot-ADDRESS flavored sibling of pcc_gc_trace_referents. The probe
builds containers through the public C API, counts the slots the
walker hands out, and proves in-place rewrite through a public
accessor. Coverage parity with the trace walker is a review
obligation (same per-type switch); this gate covers the
representative container types end-to-end.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cached_c_runtime

REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
STRICT_OBJECT_SLOTS = RUNTIME_DIR / "py" / "freestanding_gc_object_slots.py"
STRICT_BACKEND0_SLOTS = (
    RUNTIME_DIR / "py" / "freestanding_gc_backend0_slots.py"
)
STRICT_BACKEND0_COLLECTOR = (
    RUNTIME_DIR / "py" / "freestanding_gc_backend0_collector.py"
)
STRICT_COMMON_MARK_CYCLE = (
    RUNTIME_DIR / "py" / "freestanding_gc_common_mark_cycle.py"
)
STRICT_GENERATIONAL_PROMOTION = (
    RUNTIME_DIR / "py" / "freestanding_gc_generational_promotion.py"
)
STRICT_RELOCATION_REMAP = (
    RUNTIME_DIR / "py" / "freestanding_gc_relocation_remap.py"
)
STRICT_RELOCATION_PAYLOAD = (
    RUNTIME_DIR / "py" / "freestanding_gc_relocation_payload.py"
)
STRICT_SWEEP_SLOTS = RUNTIME_DIR / "py" / "freestanding_gc_sweep_slots.py"
STRICT_TRACING_SWEEP_COLLECTOR = (
    RUNTIME_DIR / "py" / "freestanding_gc_tracing_sweep_collector.py"
)


def test_backend4_relocation_reuses_shared_slot_contract():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = STRICT_RELOCATION_PAYLOAD.read_text(encoding="utf-8")

    c_contract = c_source.split("typedef struct {\n    PyObject **from_slot;", 1)[1]
    c_contract = c_contract.split("static int pcc_gc_backend_uses_forwarding", 1)[0]
    assert "py_obj_visit_slots(from" in c_contract
    assert "py_obj_visit_slots(to" in c_contract
    assert "py_obj_update_slot" in c_contract
    c_payload = c_contract.split("static int pcc_gc_relocate_copy_payload(", 1)[1]
    assert "py_incref(" not in c_payload
    assert "pcc_gc_backend4_remembered_set_retarget_slot_unlocked(" not in c_payload

    assert "pcc_gc_visit_object_slots(from_obj, _relocate_count_slot" in py_source
    assert "pcc_gc_visit_object_slots(from_obj, _relocate_from_slot" in py_source
    assert "pcc_gc_visit_object_slots(to_obj, _relocate_to_slot" in py_source
    assert "pcc_gc_backend4_remap_heal_slot" in py_source
    py_payload = py_source.split("def pcc_gc_relocate_copy_payload(", 1)[1]
    assert "py_incref(" not in py_payload
    assert "pcc_gc_backend4_remembered_set_retarget_slot(" not in py_payload


def _cc() -> str:
    return os.environ.get("CC", "cc")


def _build_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_c_runtime()


def test_pyclass_layout_matches_pcc_python_mirror(tmp_path):
    """The C struct is the owner; the port's complete layout table is checked."""
    src = tmp_path / "pyclass_layout_probe.c"
    exe = tmp_path / "pyclass_layout_probe.out"
    fields = (
        "h",
        "name",
        "n_bases",
        "bases",
        "n_mro",
        "mro",
        "n_methods",
        "methods",
        "n_fields",
        "field_names",
        "instance_size",
        "type_tag_alloc",
        "del_method",
        "attrs",
        "metaclass",
    )
    emit_fields = "\n".join(
        f'    printf("class.{field} %zu\\n", offsetof(PyClassObject, {field}));'
        for field in fields
    )
    src.write_text(
        textwrap.dedent(
            f"""
            #include "py_internal.h"
            #include <stddef.h>
            #include <stdio.h>

            int main(void) {{
                printf("class.size %zu\\n", sizeof(PyClassObject));
            {emit_fields}
                printf("method.size %zu\\n", sizeof(PyClassMethod));
                printf("method.name %zu\\n", offsetof(PyClassMethod, name));
                printf("method.func %zu\\n", offsetof(PyClassMethod, func));
                return 0;
            }}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{RUNTIME_DIR / 'include'}",
            f"-I{RUNTIME_DIR / 'src'}",
            str(src),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    c_layout = {
        name: int(offset)
        for name, offset in (line.split() for line in result.stdout.splitlines())
    }

    # Compare against the GENERATED ABI constants, not against numbers repeated
    # in py_class.py's prose.  That module's own docstring states the rule --
    # "Numeric copies do not belong in this prose: the C headers and generator
    # are the layout authority" -- so the layout table this test used to parse
    # was deliberately removed, and the test kept requiring it.  Reading the
    # generated constants checks the same invariant (port offsets equal C
    # offsets) against the artifact that is actually authoritative.
    from pcc.py_runtime.py import py_abi_constants as abi

    mirror_layout = {
        "class.size": abi.PYCLASSOBJECT_SIZE,
        "class.h": 0,
        "class.name": abi.PYCLASSOBJECT_NAME_OFFSET,
        "class.n_bases": abi.PYCLASSOBJECT_N_BASES_OFFSET,
        "class.bases": abi.PYCLASSOBJECT_BASES_OFFSET,
        "class.n_mro": abi.PYCLASSOBJECT_N_MRO_OFFSET,
        "class.mro": abi.PYCLASSOBJECT_MRO_OFFSET,
        "class.n_methods": abi.PYCLASSOBJECT_N_METHODS_OFFSET,
        "class.methods": abi.PYCLASSOBJECT_METHODS_OFFSET,
        "class.n_fields": abi.PYCLASSOBJECT_N_FIELDS_OFFSET,
        "class.field_names": abi.PYCLASSOBJECT_FIELD_NAMES_OFFSET,
        "class.instance_size": abi.PYCLASSOBJECT_INSTANCE_SIZE_OFFSET,
        "class.type_tag_alloc": abi.PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET,
        "class.del_method": abi.PYCLASSOBJECT_DEL_METHOD_OFFSET,
        "class.attrs": abi.PYCLASSOBJECT_ATTRS_OFFSET,
        "class.metaclass": abi.PYCLASSOBJECT_METACLASS_OFFSET,
        "method.size": abi.PYCLASSMETHOD_SIZE,
        "method.name": abi.PYCLASSMETHOD_NAME_OFFSET,
        "method.func": abi.PYCLASSMETHOD_FUNC_OFFSET,
    }

    assert set(mirror_layout) == set(c_layout)
    assert mirror_layout == c_layout

    substrate_source = (RUNTIME_DIR / "py" / "py_substrate.py").read_text(
        encoding="utf-8"
    )
    object_root = substrate_source.split("def py_subs_object_root():", 1)[1]
    object_root = object_root.split("\ndef ", 1)[0]
    # The port allocates through the named ABI constant, not a literal, so pin
    # the constant's value (already checked equal to the C offset above) and
    # assert the named form.  Requiring the literal made this fail as soon as
    # the magic number was replaced by the generated constant.
    class_size = c_layout["class.size"]
    assert abi.PYCLASSOBJECT_SIZE == class_size
    assert "r = malloc(PYCLASSOBJECT_SIZE)" in object_root
    assert "memset(r, 0, PYCLASSOBJECT_SIZE)" in object_root

    visitor_source = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    visitor = visitor_source.split("def _visit_class_slots(", 1)[1].split(
        "\ndef ", 1
    )[0]
    # The visitor reads its offsets through abi_constant("object.class.<field>")
    # rather than literals.  Assert the named form and separately pin each
    # constant's value against the C offset, which is strictly stronger than
    # matching a literal: a wrong constant now fails, and a renamed-but-correct
    # spelling no longer does.
    for field, accessor in (
        ("n_bases", "load_i32"),
        ("bases", "load_ptr"),
        ("n_mro", "load_i32"),
        ("mro", "load_ptr"),
        ("n_methods", "load_i32"),
        ("methods", "load_ptr"),
    ):
        literal = f"{accessor}(o, {c_layout[f'class.{field}']})"
        named = f'{accessor}(o, abi_constant("object.class.{field}_offset"))'
        assert literal in visitor or named in visitor, (
            f"{field}: neither {literal!r} nor {named!r} found"
        )
    for field in ("del_method", "attrs", "metaclass"):
        literal = f"{c_layout[f'class.{field}']},"
        named = f'abi_constant("object.class.{field}_offset")'
        assert literal in visitor or named in visitor, (
            f"{field}: neither literal offset nor named constant found"
        )

    internal_header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(
        encoding="utf-8"
    )
    for field in fields:
        assert f"PCC_ASSERT_CLASS_OFFSET({field}," in internal_header


def test_del_method_is_update_only_in_both_runtime_sources():
    c_dunder = (RUNTIME_DIR / "src" / "py_dunder.c").read_text(encoding="utf-8")
    py_dunder = (RUNTIME_DIR / "py" / "py_dunder.py").read_text(encoding="utf-8")
    c_class = (RUNTIME_DIR / "src" / "py_class.c").read_text(encoding="utf-8")
    py_class = (RUNTIME_DIR / "py" / "py_class.py").read_text(encoding="utf-8")
    py_gc = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")

    c_dispatch = c_dunder.split("void py_user_del_dispatch(PyObject *o)", 1)[1]
    c_dispatch = c_dispatch.split("\n}", 1)[0]
    py_dispatch = py_dunder.split("def py_user_del_dispatch(o) -> None:", 1)[1]
    py_dispatch = py_dispatch.split("\n@c_abi_export", 1)[0]
    assert 'py_class_lookup(cls, "__del__")' in c_dispatch
    assert 'py_class_lookup(cls, cstr("__del__"))' in py_dispatch
    assert "cls->del_method" not in c_dispatch
    assert "load_ptr(cls, 96)" not in py_dispatch
    assert "store_ptr(cls, 96" not in py_dispatch

    c_add = c_class.split("void py_class_add_method(", 1)[1].split("\n}", 1)[0]
    py_add = py_class.split("def py_class_add_method(cls, name, func) -> None:", 1)[1]
    py_add = py_add.split("\n@c_abi_export", 1)[0]
    assert "cls->del_method = func" in c_add
    assert "class_note_borrowed_metadata_slot_store" in c_add
    assert "store_ptr(cls, PYCLASSOBJECT_DEL_METHOD_OFFSET, func)" in py_add
    assert "_class_note_borrowed_metadata_slot_store" in py_add

    py_gc_class = py_gc.split("def _visit_class_slots(", 1)[1]
    py_gc_class = py_gc_class.split("\ndef ", 1)[0]
    assert 'abi_constant("object.class.del_method_offset")' in py_gc_class


def test_c_finalizer_ignores_stale_update_only_del_alias(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "del_method_update_only_probe.c"
    exe = tmp_path / "del_method_update_only_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_internal.h"
            #include <stdio.h>

            static int semantic_hits = 0;
            static int stale_hits = 0;

            static PyObject *semantic_del(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                semantic_hits++;
                return py_int_from_i64(1);
            }

            static PyObject *stale_del(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                stale_hits++;
                return py_int_from_i64(2);
            }

            int main(void) {
                PyClassObject *cls = py_class_new("Probe", NULL, 0, NULL, 0);
                PyObject *semantic = py_func_new_named(
                    (void *)semantic_del, NULL, "semantic_del"
                );
                PyObject *stale = py_func_new_named(
                    (void *)stale_del, NULL, "stale_del"
                );
                if (cls == NULL || semantic == NULL || stale == NULL) return 10;
                py_class_add_method(cls, "__del__", semantic);

                /* The alias participates in GC pointer updating but is not the
                 * semantic lookup owner.  Poison it to make cache reads fail. */
                cls->del_method = stale;
                PyObject *inst = py_instance_new(cls);
                if (inst == NULL) return 11;
                py_user_del_dispatch(inst);

                printf("%d\\n", semantic_hits == 1);
                printf("%d\\n", stale_hits == 0);
                printf("%d\\n", cls->del_method == stale);
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["1", "1", "1"]


_PROBE = """
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void pcc_gc_update_referents(PyObject *o, void (*update)(PyObject **slot));

static int64_t g_count = 0;
static PyObject *g_sentinel = NULL;
static PyObject *g_rewrite_target = NULL;

static void count_and_rewrite(PyObject **slot) {
    g_count++;
    if (g_rewrite_target != NULL && *slot == g_rewrite_target) {
        *slot = g_sentinel;
    }
}

int main(void) {
    g_sentinel = py_str_new("SENTINEL", 8);

    /* list [10, 20, 30]: 3 slots; rewrite the 20 in place */
    PyObject *lst = py_list_new(4);
    PyObject *twenty = py_str_new("twenty", 6);
    py_list_append(lst, py_int_from_i64(10));
    py_list_append(lst, twenty);
    py_list_append(lst, py_int_from_i64(30));
    g_count = 0;
    g_rewrite_target = twenty;
    pcc_gc_update_referents(lst, count_and_rewrite);
    printf("%d\\n", g_count == 3);
    PyObject *got = py_list_getitem(lst, 1);
    printf("%d\\n", got == g_sentinel);

    /* tuple (1, 2): 2 slots */
    PyObject *tup = py_tuple_new(2);
    py_tuple_set_item(tup, 0, py_int_from_i64(1));
    py_tuple_set_item(tup, 1, py_int_from_i64(2));
    g_count = 0;
    g_rewrite_target = NULL;
    pcc_gc_update_referents(tup, count_and_rewrite);
    printf("%d\\n", g_count == 2);

    /* dict {"a": 1, "b": 2}: 2 entries -> 4 slots */
    PyObject *d = py_dict_new();
    py_dict_set(d, py_str_new("a", 1), py_int_from_i64(1));
    py_dict_set(d, py_str_new("b", 1), py_int_from_i64(2));
    g_count = 0;
    pcc_gc_update_referents(d, count_and_rewrite);
    printf("%d\\n", g_count == 4);

    /* set {"x"}: 1 occupied key slot */
    PyObject *s = py_set_new();
    py_set_add(s, py_str_new("x", 1));
    g_count = 0;
    pcc_gc_update_referents(s, count_and_rewrite);
    printf("%d\\n", g_count == 1);

    /* non-container (str): zero slots, no crash */
    g_count = 0;
    pcc_gc_update_referents(g_sentinel, count_and_rewrite);
    printf("%d\\n", g_count == 0);

    /* instance: borrowed cls + declared fields + dynamic attrs slot */
    const char *field_names[2] = {"a", "b"};
    PyClassObject *cls = py_class_new("Probe", NULL, 0, field_names, 2);
    PyObject *inst_obj = py_instance_new(cls);
    PyInstanceObject *inst = (PyInstanceObject *)inst_obj;
    PyObject *field_value = py_str_new("field", 5);
    py_instance_set_field(inst, 0, field_value);
    py_instance_setattr(inst, "dyn", py_str_new("dyn", 3));
    g_count = 0;
    g_rewrite_target = field_value;
    pcc_gc_update_referents(inst_obj, count_and_rewrite);
    printf("%d\\n", g_count == 4);
    PyObject *field_got = py_instance_get_field(inst, 0);
    printf("%d\\n", field_got == g_sentinel);

    /* class: borrowed traced + borrowed update-only + owned attrs */
    PyClassObject *base_cls = py_class_new("Base", NULL, 0, NULL, 0);
    PyClassObject *meta_cls = py_class_new("Meta", NULL, 0, NULL, 0);
    PyClassObject *klass = (PyClassObject *)pcc_gc_alloc(
        sizeof(PyClassObject), PY_TYPE_CLASS, 0
    );
    if (base_cls == NULL || meta_cls == NULL || klass == NULL) return 10;
    memset((char *)klass + sizeof(PyObjectHeader), 0,
           sizeof(PyClassObject) - sizeof(PyObjectHeader));
    klass->n_bases = 1;
    klass->bases = (PyClassObject **)calloc(1, sizeof(PyClassObject *));
    klass->n_mro = 1;
    klass->mro = (PyClassObject **)calloc(1, sizeof(PyClassObject *));
    klass->n_methods = 1;
    klass->methods = (PyClassMethod *)calloc(1, sizeof(PyClassMethod));
    if (klass->bases == NULL || klass->mro == NULL || klass->methods == NULL) {
        return 11;
    }
    klass->bases[0] = base_cls;
    klass->mro[0] = base_cls;
    klass->methods[0].func = py_str_new("method", 6);
    klass->del_method = py_str_new("del", 3);
    klass->attrs = py_dict_new();
    klass->metaclass = meta_cls;
    g_count = 0;
    g_rewrite_target = klass->attrs;
    pcc_gc_update_referents((PyObject *)klass, count_and_rewrite);
    printf("%d\\n", g_count == 6);
    printf("%d\\n", klass->attrs == g_sentinel);
    return 0;
}
"""


def test_update_referents_counts_and_rewrites(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "update_referents_probe.c"
    exe = tmp_path / "update_referents_probe.out"
    src.write_text(textwrap.dedent(_PROBE).lstrip(), encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
    ]


def test_update_referents_routes_capi_extension_object_slots(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "update_referents_cext_probe.c"
    exe = tmp_path / "update_referents_cext_probe.out"
    src.write_text(
        textwrap.dedent("""
        #include "Python.h"
        #include <stdio.h>
        #include <stddef.h>

        void pcc_gc_update_referents(PyObject *o, void (*update)(PyObject **slot));

        typedef struct ProbeCextObject {
            PyObject ob_base;
            PyObject *child;
        } ProbeCextObject;

        static int probe_traverse(PyObject *self, visitproc visit, void *arg) {
            ProbeCextObject *obj = (ProbeCextObject *)self;
            Py_VISIT(obj->child);
            return 0;
        }

        static PyTypeObject ProbeType = {
            .tp_name = "pcc_probe.Cext",
            .tp_basicsize = sizeof(ProbeCextObject),
            .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
            .tp_traverse = probe_traverse,
        };

        static int64_t g_count = 0;
        static PyObject *g_sentinel = NULL;
        static PyObject *g_rewrite_target = NULL;

        static void count_and_rewrite(PyObject **slot) {
            g_count++;
            if (g_rewrite_target != NULL && *slot == g_rewrite_target) {
                *slot = g_sentinel;
            }
        }

        int main(void) {
            if (PyType_Ready(&ProbeType) != 0) return 2;
            g_sentinel = py_str_new("SENTINEL", 8);
            PyObject *child = py_str_new("cext-child", 10);
            ProbeCextObject *obj = (
                ProbeCextObject *
            )PyType_GenericAlloc(&ProbeType, 0);
            if (g_sentinel == NULL || child == NULL || obj == NULL) return 3;

            pcc_gc_store_ptr((PyObject *)obj, &obj->child, child);
            g_count = 0;
            g_rewrite_target = child;
            pcc_gc_update_referents((PyObject *)obj, count_and_rewrite);

            printf("%d\\n", g_count == 1);
            printf("%d\\n", obj->child == g_sentinel);
            printf("%d\\n", obj->ob_base.ob_type == &ProbeType);
            return 0;
        }
        """).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{REPO_ROOT / 'utils' / 'fake_libc_include'}",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["1", "1", "1"]


def test_capi_extension_dynamic_object_finalizer_and_dealloc_dispatch(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "cext_dynamic_dealloc_probe.c"
    exe = tmp_path / "cext_dynamic_dealloc_probe.out"
    src.write_text(
        textwrap.dedent("""
        #define PY_SSIZE_T_CLEAN
        #include "Python.h"
        #include <stdint.h>
        #include <stdio.h>

        void py_user_del_dispatch(PyObject *o);

        typedef struct ProbeManagedObject {
            PyObject_HEAD
            long value;
        } ProbeManagedObject;

        static long dealloc_hits = 0;

        static void probe_managed_dealloc(PyObject *self) {
            (void)self;
            dealloc_hits += 1;
        }

        static PyTypeObject ProbeManagedType = {
            PyVarObject_HEAD_INIT(NULL, 0)
            .tp_name = "pcc_probe.Managed",
            .tp_basicsize = sizeof(ProbeManagedObject),
            .tp_flags = Py_TPFLAGS_DEFAULT | PCC_TPFLAGS_MANAGED_DEALLOC,
            .tp_dealloc = probe_managed_dealloc,
            .tp_new = PyType_GenericNew,
        };

        int main(void) {
            if (PyType_Ready(&ProbeManagedType) != 0) return 2;
            PyObject *obj = PyType_GenericNew(&ProbeManagedType, NULL, NULL);
            if (obj == NULL) return 3;

            PyObjectHeader *header = (PyObjectHeader *)obj;
            int32_t tag_before = header->type_tag;
            py_user_del_dispatch(obj);

            printf("%d\\n", header->type_tag == tag_before);
            printf("%d\\n", (header->flags & 4) == 0);  /* PY_FLAG_FINALIZED */
            printf("%d\\n", dealloc_hits == 0);

            Py_DECREF(obj);
            printf("%d\\n", dealloc_hits == 1);
            return 0;
        }
        """).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{REPO_ROOT / 'utils' / 'fake_libc_include'}",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["1", "1", "1", "1"]


def test_capi_extension_object_slot_contract_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")
    shim_source = (RUNTIME_DIR / "src" / "py_capi_shim.c").read_text(encoding="utf-8")
    visit_port = (RUNTIME_DIR / "py" / "py_capi_visit_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "int pcc_capi_visit_cext_object_slots(" in header

    visit_start = source.index("int py_obj_visit_slots(")
    visit_end = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        visit_start,
    )
    visit_body = source[visit_start:visit_end]
    cext_visit = "pcc_capi_visit_cext_object_slots(o, visit, ctx)"
    assert cext_visit in visit_body
    assert visit_body.index("pcc_gc_visit_object_slots_slice(") < (
        visit_body.index(cext_visit)
    )
    slice_body = source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    assert slice_body.index("pcc_capi_is_cext_type_tag") < slice_body.index(
        "tag == PY_TYPE_INSTANCE"
    )

    instance_start = source.index("static int pcc_gc_visit_instance_owner_slots(")
    instance_end = source.index(
        "static int pcc_gc_visit_class_slots(",
        instance_start,
    )
    instance_body = source[instance_start:instance_end]
    assert "pcc_capi_is_cext_type_tag((int64_t)tag) != 0" in instance_body

    relocate_start = source.index(
        "static int pcc_gc_colored_relocate_copy_supported_tag("
    )
    relocate_end = source.index(
        "return pcc_gc_relocate_copy_supported_tag(tag);",
        relocate_start,
    )
    relocate_body = source[relocate_start:relocate_end]
    cext_exclude = "pcc_capi_is_cext_type_tag((int64_t)tag) != 0"
    user_instance_catchall = (
        "tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START"
    )
    assert cext_exclude in relocate_body
    assert relocate_body.index(cext_exclude) < relocate_body.index(
        user_instance_catchall
    )

    adapter_start = shim_source.index("static int pcc_capi_visit_cext_object_slot_ref(")
    shim_start = shim_source.index(
        "int pcc_capi_visit_cext_object_slots(",
        adapter_start,
    )
    shim_end = shim_source.index(
        "typedef struct PccCapiModuleStateVisitCtx",
        shim_start,
    )
    adapter_body = shim_source[adapter_start:shim_start]
    shim_body = shim_source[shim_start:shim_end]
    assert "type->tp_traverse" in shim_body
    assert "pcc_capi_visit_cext_object_slot_ref" in shim_body
    assert "PY_OBJ_SLOT_OWNED" in adapter_body

    assert '@c_abi_typed_export("pcc_capi_visit_cext_object_slots"' in visit_port


def test_pcc_python_cext_object_slot_bridge_source():
    header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")
    shim_source = (RUNTIME_DIR / "src" / "py_capi_shim.c").read_text(encoding="utf-8")
    visit_port = (RUNTIME_DIR / "py" / "py_capi_visit_runtime.py").read_text(
        encoding="utf-8"
    )
    strict_slots_py = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    gc_backend_py = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    obj_gc_py = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")
    collector_py = STRICT_BACKEND0_COLLECTOR.read_text(encoding="utf-8")

    assert "PccPyObjSlotVisitorI64" in header
    assert "int pcc_capi_visit_cext_object_slots_i64(" in header
    assert "int pcc_capi_visit_cext_object_slots_i64(" in shim_source
    assert "pcc_capi_visit_cext_object_slot_i64_adapter" in shim_source
    assert '@c_abi_typed_export("pcc_capi_visit_cext_object_slots_i64"' in visit_port
    assert "pcc_capi_visit_cext_object_slot_i64_adapter" in visit_port
    strict_i64_bridge = visit_port.split(
        'def pcc_capi_visit_cext_object_slots_i64(', 1
    )[1]
    assert 'function_addr("pcc_capi_visit_cext_object_slot_i64_adapter")' in (
        strict_i64_bridge
    )
    assert 'function_addr("pcc_capi_visit_cext_object_slot_ref")' not in (
        strict_i64_bridge
    )

    assert "pcc_capi_visit_cext_object_slots_i64 = extern(" in strict_slots_py
    strict_dispatch = strict_slots_py.split(
        # The port migrated its return annotations from `int` to explicit
        # `i64`; split on the signature without the annotation so the marker
        # survives the next such migration too.
        "def pcc_gc_visit_object_slots(o, visitor, context)", 1
    )[1]
    cext_call = "pcc_capi_visit_cext_object_slots_i64("
    assert cext_call in strict_dispatch
    assert strict_dispatch.index("pcc_gc_visit_object_slots_slice(") < (
        strict_dispatch.index(cext_call)
    )

    assert "pcc_gc_visit_object_slots = extern(" in gc_backend_py
    covered_body = gc_backend_py.split(
        "def _py_obj_visit_covered_slots(",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_visit_object_slots(" in covered_body
    assert "_py_obj_visit_" in covered_body
    assert "pcc_capi_visit_cext_object_slots_i64" not in gc_backend_py

    assert "pcc_gc_visit_object_slots = extern(" in obj_gc_py
    append_body = obj_gc_py.split(
        "def _append_referents_to(o, out) -> None:", 1
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_visit_object_slots(" in append_body
    assert "_py_obj_gc_visit_append_slot" in append_body
    assert "pcc_capi_visit_cext_object_slots_i64" not in obj_gc_py

    backend0_slots = STRICT_BACKEND0_SLOTS.read_text(encoding="utf-8")
    assert "pcc_gc_visit_object_slots = extern(" in backend0_slots
    assert "pcc_capi_visit_cext_object_slots_i64" not in backend0_slots


def test_capi_extension_dynamic_tags_do_not_use_instance_layout_source():
    gc_backend_c = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    obj_gc_c = (RUNTIME_DIR / "src" / "py_obj_gc.c").read_text(encoding="utf-8")
    dunder_c = (RUNTIME_DIR / "src" / "py_dunder.c").read_text(encoding="utf-8")
    gc_backend_py = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(
        encoding="utf-8"
    )
    obj_gc_py = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")
    collector_py = STRICT_BACKEND0_COLLECTOR.read_text(encoding="utf-8")
    tracing_collector_py = STRICT_TRACING_SWEEP_COLLECTOR.read_text(
        encoding="utf-8"
    )
    obj_dealloc_py = (RUNTIME_DIR / "py" / "py_obj_dealloc.py").read_text(
        encoding="utf-8"
    )
    dunder_py = (RUNTIME_DIR / "py" / "py_dunder.py").read_text(encoding="utf-8")
    strict_slots_py = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    relocation_payload = STRICT_RELOCATION_PAYLOAD.read_text(encoding="utf-8")

    obj_maybe_body = obj_gc_c[
        obj_gc_c.index("static int py_gc_maybe_finalize_unreachable(") : obj_gc_c.index(
            "static void py_gc_clear_slot(",
        )
    ]
    obj_maybe_guard = "pcc_capi_is_cext_type_tag((int64_t)tag) != 0"
    assert obj_maybe_guard in obj_maybe_body
    assert obj_maybe_body.index(obj_maybe_guard) < obj_maybe_body.index(
        "py_user_del_dispatch(obj)"
    )

    obj_dealloc_body = obj_gc_c[
        obj_gc_c.index("static void py_gc_dealloc_unreachable(") : obj_gc_c.index(
            "void py_gc_init(void)",
        )
    ]
    obj_dealloc_cext = "pcc_capi_dealloc_cext_object(o, (int64_t)h->type_tag) != 0"
    assert obj_dealloc_cext in obj_dealloc_body
    assert obj_dealloc_body.index(obj_dealloc_cext) < obj_dealloc_body.index(
        "h->type_tag >= PY_TYPE_USER_CLASS_START"
    )

    tracing_dealloc_body = gc_backend_c[
        gc_backend_c.index(
            "static void pcc_gc_finalize_unreachable("
        ) : gc_backend_c.index(
            "static void pcc_gc_recheck_reachability_after_finalizers"
        )
    ]
    tracing_cext = "pcc_capi_dealloc_cext_object(o, (int64_t)h->type_tag) != 0"
    assert tracing_cext in tracing_dealloc_body
    assert tracing_dealloc_body.index(tracing_cext) < tracing_dealloc_body.index(
        "h->type_tag >= PY_TYPE_USER_CLASS_START"
    )

    sweep_body = gc_backend_c[
        gc_backend_c.index(
            "static int64_t pcc_gc_sweep_unreachable("
        ) : gc_backend_c.index("pcc_gc_recheck_reachability_after_finalizers();")
    ]
    sweep_guard = "pcc_capi_is_cext_type_tag((int64_t)py_header(o)->type_tag) == 0"
    assert sweep_guard in sweep_body
    assert sweep_body.index(sweep_guard) < sweep_body.index("py_user_del_dispatch(o)")

    c_del_start = dunder_c.index("void py_user_del_dispatch(PyObject *o)")
    c_del_body = dunder_c[
        c_del_start : dunder_c.index("PyInstanceObject *inst =", c_del_start)
    ]
    assert obj_maybe_guard in c_del_body

    assert "pcc_capi_is_cext_type_tag = extern(" in gc_backend_py
    assert "pcc_capi_dealloc_cext_object = extern(" in tracing_collector_py
    py_instance_body = strict_slots_py.split(
        "def _visit_instance_slots(o, visitor, context)", 1
    )[1].split("\ndef ", 1)[0]
    assert "pcc_capi_is_cext_type_tag(tag) != 0" in py_instance_body
    assert py_instance_body.index("pcc_capi_is_cext_type_tag(tag) != 0") < (
        py_instance_body.index('tag != abi_constant("object.type.instance")')
    )
    assert 'tag != abi_constant("object.type.valuebox")' in py_instance_body
    assert 'tag < abi_constant("object.type.user_class_start")' in py_instance_body

    relocation_remap = STRICT_RELOCATION_REMAP.read_text(encoding="utf-8")
    py_colored_body = relocation_remap.split(
        "def pcc_gc_backend4_relocate_copy_supported_tag", 1
    )[1].split('@c_abi_export("pcc_gc_backend4_remap_heal_slot")', 1)[0]
    py_cext_guard = "if pcc_capi_is_cext_type_tag(tag) != 0:"
    assert py_cext_guard in py_colored_body
    assert py_colored_body.index(py_cext_guard) < py_colored_body.index(
        'tag == abi_constant("object.type.instance")'
    )
    assert 'tag >= abi_constant("object.type.user_class_start")' in py_colored_body

    py_relocate_start = relocation_payload.index(
        "def pcc_gc_relocate_copy_payload_prepared_locked("
    )
    py_relocate_body = relocation_payload[
        py_relocate_start : relocation_payload.index(
            '@c_abi_export("pcc_gc_relocate_copy_payload")', py_relocate_start
        )
    ]
    assert py_cext_guard in py_relocate_body
    assert py_relocate_body.index(py_cext_guard) < py_relocate_body.index(
        'tag == abi_constant("object.type.instance")'
    )
    assert 'tag >= abi_constant("object.type.user_class_start")' in py_relocate_body
    assert "cls = pcc_gc_load_ptr(" in py_relocate_body
    assert "_relocate_copy_payload_finish(" in py_relocate_body
    assert (
        "child = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, offset))"
        not in py_relocate_body
    )
    py_wrapper = relocation_payload.split(
        "def pcc_gc_relocate_copy_payload(from_obj, to_obj, tag: i64, size: i64)",
        1,
    )[1]
    assert "_relocate_slot_pairs_prepare(from_obj, to_obj, size)" in py_wrapper
    assert "pcc_gc_relocate_copy_payload_prepared_locked(" in py_wrapper
    assert "_relocate_slot_pairs_dispose(ctx)" in py_wrapper

    c_relocate_start = gc_backend_c.index(
        "static int pcc_gc_relocate_copy_payload_prepared_locked"
    )
    c_relocate_body = gc_backend_c[
        c_relocate_start : gc_backend_c.index(
            "/* GC3 oldification still owns", c_relocate_start
        )
    ]
    assert "PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(" in c_relocate_body
    assert "pcc_gc_relocate_copy_slots(from, to, pairs)" in c_relocate_body
    assert "PyObject *child = pcc_gc_load_ptr(from, &src->fields[i])" not in (
        c_relocate_body
    )
    c_wrapper = gc_backend_c.split(
        "static int pcc_gc_relocate_copy_payload(\n", 1
    )[1].split("static int pcc_gc_backend_uses_forwarding", 1)[0]
    assert "pcc_gc_relocate_slot_count_locked(from)" in c_wrapper
    assert "pcc_gc_relocate_slot_pairs_prepare(count, &pairs)" in c_wrapper
    assert "pcc_gc_relocate_copy_payload_prepared_locked(" in c_wrapper
    assert "pcc_gc_relocate_slot_pairs_finish(&pairs)" in c_wrapper

    py_finalize_body = tracing_collector_py[
        tracing_collector_py.index(
            "def pcc_gc_tracing_finalize_unreachable(obj) -> None:"
        ) : tracing_collector_py.index(
            '@c_abi_export("pcc_gc_tracing_recheck_reachability_after_finalizers")'
        )
    ]
    assert "pcc_capi_dealloc_cext_object(obj, tag) == 0" in py_finalize_body
    assert py_finalize_body.index(
        "pcc_capi_dealloc_cext_object(obj, tag) == 0"
    ) < py_finalize_body.index(
        'if tag >= abi_constant("object.type.user_class_start"):'
    )

    py_sweep_body = tracing_collector_py[
        tracing_collector_py.index(
            "def pcc_gc_tracing_sweep_unreachable("
        ) : tracing_collector_py.index(
            "    pcc_gc_tracing_recheck_reachability_after_finalizers()"
        )
    ]
    assert "pcc_capi_is_cext_type_tag(tag) == 0" in py_sweep_body
    assert py_sweep_body.index("pcc_capi_is_cext_type_tag(tag) == 0") < (
        py_sweep_body.index("py_user_del_dispatch(obj)")
    )

    assert "pcc_capi_is_cext_type_tag = extern(" in collector_py
    assert "pcc_capi_dealloc_cext_object = extern(" in collector_py
    assert "pcc_gc_visit_object_slots = extern(" in obj_gc_py
    py_obj_maybe_body = collector_py[
        collector_py.index("def _maybe_finalize_unreachable(") : collector_py.index(
            "def _dealloc_unreachable(obj)"
        )
    ]
    assert "pcc_capi_is_cext_type_tag(tag) == 0" in py_obj_maybe_body
    py_obj_dealloc_body = collector_py[
        collector_py.index("def _dealloc_unreachable(obj) -> None:") : collector_py.index(
            '@c_abi_export("py_gc_collect")'
        )
    ]
    assert "pcc_capi_dealloc_cext_object(obj, tag) != 0" in py_obj_dealloc_body
    assert py_obj_dealloc_body.index(
        "pcc_capi_dealloc_cext_object(obj, tag) != 0"
    ) < py_obj_dealloc_body.index(
        'elif tag >= abi_constant("object.type.user_class_start"):'
    )

    py_refcount_body = obj_dealloc_py[
        obj_dealloc_py.index(
            "def _dealloc_dispatch(o, tag: int) -> None:"
        ) : obj_dealloc_py.index("def _trash_enqueue(")
    ]
    assert "pcc_capi_dealloc_cext_object(o, tag) != 0" in py_refcount_body
    assert py_refcount_body.index(
        "pcc_capi_dealloc_cext_object(o, tag) != 0"
    ) < py_refcount_body.index("if tag >= PY_TYPE_USER_CLASS_START:")

    # The header offset moved from a literal 12 to PYOBJECTHEADER_FLAGS_OFFSET;
    # slice on the assignment target, which survives both spellings, and pin the
    # constant's value separately so the offset itself is still checked.
    from pcc.py_runtime.py.py_abi_constants import PYOBJECTHEADER_FLAGS_OFFSET

    assert PYOBJECTHEADER_FLAGS_OFFSET == 12
    py_del_body = dunder_py[
        dunder_py.index("def py_user_del_dispatch(o)") : dunder_py.index(
            "    flags: int = load_i32(o, "
        )
    ]
    assert "pcc_capi_is_cext_type_tag(tag) != 0" in py_del_body


def test_trace_and_update_share_core_container_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_core_container_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        helper_start,
    )
    helper_body = source[helper_start:promote_start]
    for tag in (
        "PY_TYPE_LIST",
        "PY_TYPE_TUPLE",
        "PY_TYPE_DICT",
        "PY_TYPE_SET",
    ):
        assert tag in helper_body

    trace_start = source.index(
        "static void pcc_gc_trace_referents(",
        promote_start,
    )
    update_comment_start = source.index(
        "/* Slot-ADDRESS flavored sibling of pcc_gc_trace_referents"
    )
    visit_start = source.index("int py_obj_visit_slots(", helper_start)
    visit_body = source[
        visit_start : source.index(
            "typedef struct {\n    void (*visit)(PyObject *child);", visit_start
        )
    ]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body

    trace_body = source[trace_start:update_comment_start]
    assert "py_obj_visit_slots(" in trace_body
    assert "pcc_gc_trace_owner_slot" in trace_body
    assert "&trace_ctx" in trace_body

    update_start = source.index("void pcc_gc_update_referents(")
    next_fn_start = source.index(
        "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
        update_start,
    )
    update_body = source[update_start:next_fn_start]
    assert "py_obj_visit_slots(" in update_body
    assert "pcc_gc_update_owner_slot" in update_body
    assert "&update_ctx" in update_body


def test_trace_update_and_promotion_share_fixed_owner_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_fixed_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "static int pcc_gc_visit_weakref_slots(", helper_start
    )
    helper_body = source[helper_start:trace_adapter_start]
    for tag in (
        "PY_TYPE_FUNC",
        "PY_TYPE_ITER",
        "PY_TYPE_GEN",
        "PY_TYPE_COROUTINE",
        "PY_TYPE_TASK",
        "PY_TYPE_VIRTUAL_THREAD",
        "PY_TYPE_EXC",
        "PY_TYPE_PROPERTY",
        "PY_TYPE_CLASSMETHOD",
        "PY_TYPE_STATICMETHOD",
        "PY_TYPE_MEMORYVIEW",
        "PY_TYPE_THREAD",
    ):
        assert tag in helper_body
    for intentionally_excluded in (
        "PY_TYPE_CLASS",
        "PY_TYPE_INSTANCE",
        "PY_TYPE_CONTINUATION",
        "PY_TYPE_WEAKREF",
        "PY_TYPE_USER_CLASS_START",
    ):
        assert f"if (tag == {intentionally_excluded})" not in helper_body

    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        helper_start,
    )
    trace_start = source.index(
        "static void pcc_gc_trace_referents(",
        promote_start,
    )
    update_start = source.index("void pcc_gc_update_referents(")
    promote_body = source[promote_start:trace_start]
    trace_body = source[
        trace_start : source.index("/* Slot-ADDRESS flavored sibling", trace_start)
    ]
    update_body = source[
        update_start : source.index(
            "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
            update_start,
        )
    ]
    visit_start = source.index("int py_obj_visit_slots(", helper_start)
    visit_body = source[
        visit_start : source.index(
            "typedef struct {\n    void (*visit)(PyObject *child);", visit_start
        )
    ]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body
    assert "pcc_gc_backend3_enqueue_promotion_owner" in promote_body
    assert "py_obj_visit_slots(" not in promote_body
    for body in (trace_body, update_body):
        assert "py_obj_visit_slots(" in body
    slice_body = source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    for tag in (
        "PY_TYPE_FUNC",
        "PY_TYPE_ITER",
        "PY_TYPE_GEN",
        "PY_TYPE_COROUTINE",
        "PY_TYPE_TASK",
        "PY_TYPE_VIRTUAL_THREAD",
        "PY_TYPE_EXC",
        "PY_TYPE_PROPERTY",
        "PY_TYPE_CLASSMETHOD",
        "PY_TYPE_STATICMETHOD",
        "PY_TYPE_MEMORYVIEW",
        "PY_TYPE_THREAD",
    ):
        assert tag in slice_body


def test_trace_update_and_promotion_share_continuation_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_continuation_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "static int pcc_gc_visit_instance_owner_slots(", helper_start
    )
    helper_body = source[helper_start:trace_adapter_start]
    assert "PY_TYPE_CONTINUATION" in helper_body
    assert "stack_chunk" in helper_body
    assert "slot_count" in helper_body
    assert "&chunk->slots[i]" in helper_body

    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        helper_start,
    )
    trace_start = source.index(
        "static void pcc_gc_trace_referents(",
        promote_start,
    )
    update_start = source.index("void pcc_gc_update_referents(")
    promote_body = source[promote_start:trace_start]
    trace_body = source[
        trace_start : source.index("/* Slot-ADDRESS flavored sibling", trace_start)
    ]
    update_body = source[
        update_start : source.index(
            "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
            update_start,
        )
    ]
    visit_start = source.index("int py_obj_visit_slots(", helper_start)
    visit_body = source[
        visit_start : source.index(
            "typedef struct {\n    void (*visit)(PyObject *child);", visit_start
        )
    ]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body
    assert "pcc_gc_backend3_enqueue_promotion_owner" in promote_body
    assert "py_obj_visit_slots(" not in promote_body
    for body in (trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "if (tag == PY_TYPE_CONTINUATION)" not in body
    slice_body = source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    assert "tag == PY_TYPE_CONTINUATION" in slice_body
    assert "chunk->slots[cursor]" in slice_body


def test_trace_update_and_promotion_share_instance_owner_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_instance_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "static int pcc_gc_visit_class_slots(", helper_start
    )
    helper_body = source[helper_start:trace_adapter_start]
    assert "PY_TYPE_INSTANCE" in helper_body
    assert "PY_TYPE_VALUEBOX" in helper_body
    assert "PY_TYPE_USER_CLASS_START" in helper_body
    assert "(PyObject **)&inst->cls" in helper_body
    assert "&inst->fields[i]" in helper_body
    assert "&inst->fields[n_fields]" in helper_body

    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        helper_start,
    )
    trace_start = source.index(
        "static void pcc_gc_trace_referents(",
        promote_start,
    )
    update_start = source.index("void pcc_gc_update_referents(")
    promote_body = source[promote_start:trace_start]
    trace_body = source[
        trace_start : source.index("/* Slot-ADDRESS flavored sibling", trace_start)
    ]
    update_body = source[
        update_start : source.index(
            "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
            update_start,
        )
    ]
    visit_start = source.index("int py_obj_visit_slots(", helper_start)
    visit_body = source[
        visit_start : source.index(
            "typedef struct {\n    void (*visit)(PyObject *child);", visit_start
        )
    ]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body
    assert "pcc_gc_backend3_enqueue_promotion_owner" in promote_body
    assert "py_obj_visit_slots(" not in promote_body
    for body in (trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START" not in body
    slice_body = source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    assert "tag == PY_TYPE_INSTANCE" in slice_body
    assert "slot = &inst->fields[cursor - 1]" in slice_body


def test_trace_update_and_promotion_share_class_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_class_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "typedef struct {\n    PyObjSlotVisitor visit;", helper_start
    )
    helper_body = source[helper_start:trace_adapter_start]
    assert "PY_TYPE_CLASS" in helper_body
    assert "visit_owned" in helper_body
    assert "visit_borrowed_traced" in helper_body
    assert "visit_borrowed_update_only" in helper_body
    assert "(PyObject **)&cls->bases[i]" in helper_body
    assert "(PyObject **)&cls->mro[i]" in helper_body
    assert "&cls->methods[i].func" in helper_body
    assert "&cls->del_method" in helper_body
    assert "&cls->attrs" in helper_body
    assert "(PyObject **)&cls->metaclass" in helper_body

    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        helper_start,
    )
    trace_start = source.index(
        "static void pcc_gc_trace_referents(",
        promote_start,
    )
    update_start = source.index("void pcc_gc_update_referents(")
    promote_body = source[promote_start:trace_start]
    trace_body = source[
        trace_start : source.index("/* Slot-ADDRESS flavored sibling", trace_start)
    ]
    update_body = source[
        update_start : source.index(
            "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
            update_start,
        )
    ]
    visit_start = source.index("int py_obj_visit_slots(", helper_start)
    visit_body = source[
        visit_start : source.index(
            "typedef struct {\n    void (*visit)(PyObject *child);", visit_start
        )
    ]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body
    assert "pcc_gc_backend3_enqueue_promotion_owner" in promote_body
    assert "py_obj_visit_slots(" not in promote_body
    for body in (trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "if (tag == PY_TYPE_CLASS)" not in body

    drain_body = source.split(
        "static int64_t pcc_gc_backend3_drain_promotion_worklist(int64_t budget) {",
        1,
    )[1].split("static void pcc_gc_promote_owner_referents", 1)[0]
    assert "pcc_gc_promote_owner_slot" in drain_body
    assert "pcc_gc_visit_object_slots_slice" in drain_body
    assert "pcc_gc_update_owner_slot" in update_body
    assert "visit(cls->methods[i].func)" not in trace_body
    assert "visit(cls->del_method)" not in trace_body


def test_weakref_target_is_update_only_slot_contract_source():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    py_obj_gc_source = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")
    strict_source = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")

    c_helper_start = c_source.index("static int pcc_gc_visit_weakref_slots(")
    c_helper_end = c_source.index(
        "typedef struct {\n    PyObjSlotVisitor visit;", c_helper_start
    )
    c_helper_body = c_source[c_helper_start:c_helper_end]
    assert "PY_TYPE_WEAKREF" in c_helper_body
    assert "visit_borrowed_update_only(&wr->target" in c_helper_body
    assert "visit_owned(&wr->callback" in c_helper_body

    c_fixed_start = c_source.index("static int pcc_gc_visit_fixed_owner_slots(")
    c_fixed_end = c_source.index(
        "static int pcc_gc_visit_weakref_slots(",
        c_fixed_start,
    )
    c_fixed_body = c_source[c_fixed_start:c_fixed_end]
    assert "PY_TYPE_WEAKREF" not in c_fixed_body

    c_visit_start = c_source.index("int py_obj_visit_slots(")
    c_visit_end = c_source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        c_visit_start,
    )
    c_visit_body = c_source[c_visit_start:c_visit_end]
    assert "pcc_gc_visit_object_slots_slice(" in c_visit_body
    c_slice = c_source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    assert "tag == PY_TYPE_WEAKREF" in c_slice
    assert "PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in c_slice

    weak_body = strict_source.split(
        "def _visit_weakref_slots(o, visitor, context)", 1
    )[1].split("\ndef ", 1)[0]
    weak_compact = "".join(weak_body.split())
    assert '!=abi_constant("object.type.weakref")' in weak_compact
    assert "_visit_slot(o,16,3,visitor,context)" in weak_compact
    assert "_visit_slot(o,24,1,visitor,context)" in weak_compact

    fixed_body = strict_source.split(
        "def _visit_fixed_owner_slots(o, visitor, context)", 1
    )[1].split("def _visit_weakref_slots(", 1)[0]
    assert 'tag == abi_constant("object.type.weakref")' not in fixed_body

    dispatch = strict_source.split(
        # The port migrated its return annotations from `int` to explicit
        # `i64`; split on the signature without the annotation so the marker
        # survives the next such migration too.
        "def pcc_gc_visit_object_slots(o, visitor, context)", 1
    )[1]
    assert "pcc_gc_visit_object_slots_slice(" in dispatch
    strict_slice = strict_source.split(
        "def pcc_gc_visit_object_slots_slice(", 1
    )[1].split('@c_abi_export("pcc_gc_visit_object_slots")', 1)[0]
    assert 'tag == abi_constant("object.type.weakref")' in strict_slice
    assert "role = 3" in strict_slice
    assert "pcc_gc_visit_object_slots = extern(" in py_source
    covered_body = py_source.split(
        "def _py_obj_visit_covered_slots(",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_visit_object_slots(" in covered_body

    backend0_slots = STRICT_BACKEND0_SLOTS.read_text(encoding="utf-8")
    sweep_slots = STRICT_SWEEP_SLOTS.read_text(encoding="utf-8")
    assert "pcc_gc_visit_object_slots = extern(" in backend0_slots
    for callback in (
        "pcc_gc_backend0_subtract_slot",
        "pcc_gc_backend0_mark_slot",
    ):
        assert callback in backend0_slots
    assert "pcc_gc_backend0_clear_slot" in sweep_slots
    append_body = py_obj_gc_source.split(
        "def _append_referents_to(o, out) -> None:", 1
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_visit_object_slots(" in append_body


def test_object_slot_contract_has_named_visit_and_update_entrypoints_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")

    assert "typedef void (*PyObjSlotVisitor)(" in header
    assert "PY_OBJ_SLOT_OWNED" in header
    assert "PY_OBJ_SLOT_BORROWED_TRACED" in header
    assert "PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in header
    assert "int py_obj_visit_slots(" in header
    assert "int64_t pcc_gc_visit_object_slots_slice(" in header
    assert "void py_obj_update_slot(PyObject **slot)" in header

    visit_start = source.index("int py_obj_visit_slots(")
    trace_ctx_start = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        visit_start,
    )
    visit_body = source[visit_start:trace_ctx_start]
    assert "pcc_gc_visit_object_slots_slice(" in visit_body
    assert "INT64_MAX" in visit_body
    role_adapter_start = source.index("static void py_obj_visit_role_slot(")
    role_adapter_body = source[role_adapter_start:visit_start]
    for role in (
        "PY_OBJ_SLOT_OWNED",
        "PY_OBJ_SLOT_BORROWED_TRACED",
        "PY_OBJ_SLOT_BORROWED_UPDATE_ONLY",
    ):
        assert role in role_adapter_body

    promote_start = source.index(
        "static void pcc_gc_promote_owner_referents(",
        visit_start,
    )
    trace_start = source.index("static void pcc_gc_trace_referents(", promote_start)
    update_start = source.index("void pcc_gc_update_referents(")
    promote_body = source[promote_start:trace_start]
    trace_body = source[
        trace_start : source.index("/* Slot-ADDRESS flavored sibling", trace_start)
    ]
    update_body = source[
        update_start : source.index(
            "static int64_t pcc_gc_cms_trace_gray_object_unlocked",
            update_start,
        )
    ]
    assert "pcc_gc_backend3_enqueue_promotion_owner" in promote_body
    assert "py_obj_visit_slots(" not in promote_body
    for body in (trace_body, update_body):
        assert "py_obj_visit_slots(" in body
    drain_body = source.split(
        "static int64_t pcc_gc_backend3_drain_promotion_worklist(int64_t budget) {",
        1,
    )[1].split("static void pcc_gc_promote_owner_referents", 1)[0]
    assert "pcc_gc_visit_object_slots_slice(" in drain_body

    update_slot_start = source.index("void py_obj_update_slot(PyObject **slot)")
    remap_start = source.index(
        "static void pcc_gc_backend4_remap_and_retire_unlocked(",
        update_slot_start,
    )
    update_slot_body = source[update_slot_start:remap_start]
    assert "pcc_gc_backend4_remap_heal_slot(slot)" in update_slot_body
    remap_body = source[
        remap_start : source.index("static void pcc_gc_seed_roots(", remap_start)
    ]
    assert "pcc_gc_update_referents(n->obj, py_obj_update_slot)" in remap_body


def test_backend0_cycle_collector_consumes_object_slot_contract_source():
    source = (RUNTIME_DIR / "src" / "py_obj_gc.c").read_text(encoding="utf-8")

    visit_helper_start = source.index("static void py_gc_visit_referent_slot(")
    visit_start = source.index(
        "static void py_gc_visit_referents(",
        visit_helper_start,
    )
    visit_helper_body = source[visit_helper_start:visit_start]
    assert "role == PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in visit_helper_body
    assert "pcc_gc_load_ptr(NULL, slot)" in visit_helper_body
    assert "visit_ctx->visit(child, visit_ctx->ctx)" in visit_helper_body

    subtract_start = source.index("static void py_gc_subtract_child", visit_start)
    visit_body = source[visit_start:subtract_start]
    assert "py_obj_visit_slots(o, py_gc_visit_referent_slot, &visit_ctx)" in visit_body
    for old_direct_case in (
        "PY_TYPE_LIST",
        "PY_TYPE_TUPLE",
        "PY_TYPE_DICT",
        "PY_TYPE_SET",
        "PY_TYPE_FUNC",
        "PY_TYPE_CONTINUATION",
        "PY_TYPE_INSTANCE",
    ):
        assert old_direct_case not in visit_body

    clear_slot_start = source.index("static void py_gc_clear_slot(")
    clear_start = source.index(
        "static void py_gc_clear_referents(",
        clear_slot_start,
    )
    clear_helper_body = source[clear_slot_start:clear_start]
    assert "static void py_gc_clear_owned_slot(" in clear_helper_body
    assert "role != PY_OBJ_SLOT_OWNED" in clear_helper_body
    assert "py_gc_clear_slot(slot)" in clear_helper_body

    dealloc_start = source.index(
        "static void py_gc_dealloc_unreachable(",
        clear_start,
    )
    clear_body = source[clear_start:dealloc_start]
    assert "py_obj_visit_slots(o, py_gc_clear_owned_slot, NULL)" in clear_body
    assert "py_gc_clear_slot(&" not in clear_body
    for old_direct_case in (
        "PY_TYPE_FUNC",
        "PY_TYPE_ITER",
        "PY_TYPE_GEN",
        "PY_TYPE_COROUTINE",
        "PY_TYPE_CONTINUATION",
        "PY_TYPE_TASK",
        "PY_TYPE_VIRTUAL_THREAD",
        "PY_TYPE_EXC",
        "PY_TYPE_INSTANCE",
    ):
        assert old_direct_case not in clear_body


def test_trace_and_subtract_slot_visitors_read_through_load_barrier_source():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    mark_source = STRICT_COMMON_MARK_CYCLE.read_text(encoding="utf-8")

    c_trace_start = c_source.index("static void pcc_gc_trace_owner_slot(")
    c_update_start = c_source.index(
        "typedef struct {\n    void (*update)(PyObject **slot);",
        c_trace_start,
    )
    c_trace_body = c_source[c_trace_start:c_update_start]
    assert "PyObject *child = pcc_gc_load_ptr(NULL, slot)" in c_trace_body
    assert "trace_ctx->visit(child)" in c_trace_body
    assert "trace_ctx->visit(*slot)" not in c_trace_body

    trace_case = mark_source.split("def pcc_gc_trace_slot(", 1)[1].split(
        "@c_abi_export", 1
    )[0]
    assert "child = pcc_gc_load_ptr(null(), slot)" in trace_case
    assert "pcc_gc_trace_mark_gray_if_known(child)" in trace_case
    assert "load_ptr(slot, 0)" not in trace_case

    py_visit_body = py_source.split("def _py_obj_visit_slot(", 1)[1].split(
        "def _py_obj_visit_update_slot(", 1
    )[0]
    subtract_case = py_visit_body.split("if mode == 4:", 1)[1].split(
        "if mode == 6:",
        1,
    )[0]
    assert "child = pcc_gc_load_ptr_extern(" in subtract_case
    assert "null()," in subtract_case
    assert "ptr_add(slot_base, slot_offset)" in subtract_case
    assert "load_ptr(slot_base, slot_offset)" not in subtract_case


def test_no_pointer_slot_families_are_explicitly_classified_source():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    strict_source = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    mark_source = STRICT_COMMON_MARK_CYCLE.read_text(encoding="utf-8")

    c_helper_start = c_source.index("static int py_obj_has_no_pointer_slots(")
    c_helper_end = c_source.index("int py_obj_visit_slots(", c_helper_start)
    c_helper_body = c_source[c_helper_start:c_helper_end]
    for token in (
        "PY_TYPE_NONE",
        "PY_TYPE_BOOL",
        "PY_TYPE_INT",
        "PY_TYPE_FLOAT",
        "PY_TYPE_STR",
        "PY_TYPE_COMPLEX",
        "PY_TYPE_BYTES",
        "PY_TYPE_BYTEARRAY",
        "PY_TYPE_FILE",
        "PY_TYPE_THREAD_LOCK",
        "PY_TYPE_THREAD_RLOCK",
        "PY_TYPE_THREAD_EVENT",
        "PY_TYPE_THREAD_CONDITION",
        "PY_TYPE_THREAD_SEMAPHORE",
        "PY_TYPE_CPY_HANDLE",
    ):
        assert token in c_helper_body

    c_visit_start = c_source.index("int py_obj_visit_slots(")
    c_visit_end = c_source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);", c_visit_start
    )
    c_visit_body = c_source[c_visit_start:c_visit_end]
    assert "pcc_gc_visit_object_slots_slice(" in c_visit_body
    c_slice = c_source.split(
        "int64_t pcc_gc_visit_object_slots_slice(", 1
    )[1].split("typedef struct {\n    int recurse;", 1)[0]
    assert "if (py_obj_has_no_pointer_slots(o)) return 1;" in c_slice

    py_helper_start = strict_source.index("def _has_no_pointer_slots(o)")
    py_helper_end = strict_source.index(
        "def pcc_gc_visit_object_slots(", py_helper_start
    )
    py_helper_body = strict_source[py_helper_start:py_helper_end]
    for token in (
        'abi_constant("object.type.none")',
        'abi_constant("object.type.bool")',
        'abi_constant("object.type.int")',
        'abi_constant("object.type.float")',
        'abi_constant("object.type.str")',
        'abi_constant("object.type.complex")',
        'abi_constant("object.type.bytes")',
        'abi_constant("object.type.bytearray")',
        'abi_constant("object.type.file")',
        'abi_constant("object.type.cpy_handle")',
        'abi_constant("object.type.thread_lock")',
        'abi_constant("object.type.thread_rlock")',
        'abi_constant("object.type.thread_event")',
        'abi_constant("object.type.thread_condition")',
        'abi_constant("object.type.thread_semaphore")',
    ):
        assert token in py_helper_body

    py_covered_body = strict_source.split(
        # The port migrated its return annotations from `int` to explicit
        # `i64`; split on the signature without the annotation so the marker
        # survives the next such migration too.
        "def pcc_gc_visit_object_slots(o, visitor, context)", 1
    )[1]
    assert "pcc_gc_visit_object_slots_slice(" in py_covered_body
    strict_slice = strict_source.split(
        'def pcc_gc_visit_object_slots_slice(', 1
    )[1].split('@c_abi_export("pcc_gc_visit_object_slots")', 1)[0]
    assert "if _has_no_pointer_slots(o) != 0:" in strict_slice

    trace_body = mark_source.split("def pcc_gc_trace_referents(obj)", 1)[1].split(
        "\n@c_abi_export", 1
    )[0]
    assert "pcc_gc_visit_object_slots(obj," in trace_body
    assert "_has_no_pointer_slots(obj)" not in trace_body

    subtract_body = py_source.split("def _subtract_referent_refs(o)", 1)[1].split(
        "\n@c_abi_export", 1
    )[0]
    assert "_py_obj_visit_covered_slots(o," in subtract_body
    assert "_has_no_pointer_slots(o)" not in subtract_body
    remap_source = STRICT_RELOCATION_REMAP.read_text(encoding="utf-8")
    remap_body = remap_source.split(
        "def pcc_gc_backend4_remap_referents(obj)", 1
    )[1]
    assert "pcc_gc_visit_object_slots(" in remap_body
    assert "_has_no_pointer_slots(obj)" not in remap_body

    promotion_source = STRICT_GENERATIONAL_PROMOTION.read_text(encoding="utf-8")
    promotion_body = promotion_source.split(
        "def pcc_gc_trace_referents_for_promotion_mode", 1
    )[1].split(
        '@c_abi_export("pcc_gc_trace_referents_for_promotion")', 1
    )[0]
    assert "pcc_gc_visit_object_slots(" in promotion_body
    assert "_has_no_pointer_slots(" not in promotion_body


def test_current_runtime_type_tags_have_a_finite_slot_classification_source():
    """A new concrete runtime tag must declare whether it owns object slots."""
    public_header = (RUNTIME_DIR / "include" / "py_runtime.h").read_text(
        encoding="utf-8"
    )
    internal_header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(
        encoding="utf-8"
    )
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )

    type_enum = public_header.split("enum {", 1)[1].split("};", 1)[0]
    current_tags = set(re.findall(r"\b(PY_TYPE_[A-Z0-9_]+)\s*=", type_enum))

    pointerless = {
        "PY_TYPE_NONE",
        "PY_TYPE_BOOL",
        "PY_TYPE_INT",
        "PY_TYPE_FLOAT",
        "PY_TYPE_STR",
        "PY_TYPE_COMPLEX",
        "PY_TYPE_BYTES",
        "PY_TYPE_BYTEARRAY",
        "PY_TYPE_FILE",
        "PY_TYPE_THREAD_LOCK",
        "PY_TYPE_THREAD_RLOCK",
        "PY_TYPE_THREAD_EVENT",
        "PY_TYPE_THREAD_CONDITION",
        "PY_TYPE_THREAD_SEMAPHORE",
        "PY_TYPE_CPY_HANDLE",
    }
    slot_bearing = {
        "PY_TYPE_LIST",
        "PY_TYPE_DICT",
        "PY_TYPE_TUPLE",
        "PY_TYPE_SET",
        "PY_TYPE_FUNC",
        "PY_TYPE_CLASS",
        "PY_TYPE_INSTANCE",
        "PY_TYPE_EXC",
        "PY_TYPE_ITER",
        "PY_TYPE_GEN",
        "PY_TYPE_MEMORYVIEW",
        "PY_TYPE_COROUTINE",
        "PY_TYPE_WEAKREF",
        "PY_TYPE_THREAD",
        "PY_TYPE_TASK",
        "PY_TYPE_CONTINUATION",
        "PY_TYPE_VIRTUAL_THREAD",
        "PY_TYPE_PROPERTY",
        "PY_TYPE_CLASSMETHOD",
        "PY_TYPE_STATICMETHOD",
        "PY_TYPE_VALUEBOX",
        # PyVThreadChannelEndpointObject carries `PyObject *core`, so the tag is
        # slot-bearing, and the runtime does visit it (freestanding_gc_object_
        # slots.py dispatches on object.type.vthread_channel; py_obj.c lists the
        # tag in its slot-visiting switch).  The classification table simply had
        # not been extended when the tag was added -- this test is the guard
        # against a tag whose slots nobody traces, so a missing entry here is
        # exactly what it should report.
        "PY_TYPE_VTHREAD_CHANNEL",
    }
    # These names mark the reserved user-tag range; descriptors and concrete
    # user-class tags are classified separately above / by the instance walker.
    dynamic_boundaries = {"PY_TYPE_USER", "PY_TYPE_USER_CLASS_START"}
    assert current_tags == pointerless | slot_bearing | dynamic_boundaries

    no_pointer_body = c_source.split(
        "static int py_obj_has_no_pointer_slots(", 1
    )[1].split("int py_obj_visit_slots(", 1)[0]
    for token in pointerless:
        assert token in no_pointer_body

    visit_contract = c_source.split("int py_obj_visit_slots(", 1)[1].split(
        "typedef struct {\n    void (*visit)(PyObject *child);", 1
    )[0]
    assert "pcc_gc_visit_object_slots_slice(" in visit_contract
    assert "pcc_capi_visit_cext_object_slots(" in visit_contract

    for descriptor_tag in (
        "PY_TYPE_PROPERTY",
        "PY_TYPE_CLASSMETHOD",
        "PY_TYPE_STATICMETHOD",
    ):
        assert descriptor_tag in type_enum
        assert descriptor_tag in c_source
    assert "PY_TYPE_USER_CLASS_START" in type_enum
    assert "tag < PY_TYPE_USER_CLASS_START" in c_source


def test_unreachable_file_uses_file_deallocator_in_c_and_python_mirror():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = STRICT_TRACING_SWEEP_COLLECTOR.read_text(encoding="utf-8")

    c_start = c_source.index("static void pcc_gc_finalize_unreachable(")
    c_end = c_source.index("static void pcc_gc_seed_roots(", c_start)
    c_body = c_source[c_start:c_end]
    assert "case PY_TYPE_FILE:      py_dealloc_file(o);" in c_body

    py_start = py_source.index(
        "def pcc_gc_tracing_finalize_unreachable(obj) -> None:"
    )
    py_end = py_source.index(
        '@c_abi_export("pcc_gc_tracing_recheck_reachability_after_finalizers")',
        py_start,
    )
    py_body = py_source[py_start:py_end]
    # The dispatch moved from literal tags to abi_constant("object.type.*").
    # Slice on the named form and pin the tag's numeric value separately, so a
    # renamed-but-correct spelling passes while a wrong tag still fails.
    from pcc.py_runtime.py.py_abi_constants import PY_TYPE_FILE

    assert PY_TYPE_FILE == 13
    file_case = py_body.split(
        'elif tag == abi_constant("object.type.file"):', 1
    )[1].split("elif tag ==", 1)[0]
    assert "py_dealloc_file(obj)" in file_case
    assert "if delay_zpage_freeing_note != 0:" in py_body


def test_pcc_python_gc_backend_consumers_share_slot_family_helper_source():
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    mark_source = STRICT_COMMON_MARK_CYCLE.read_text(encoding="utf-8")
    promotion_source = STRICT_GENERATIONAL_PROMOTION.read_text(encoding="utf-8")
    sweep_source = STRICT_SWEEP_SLOTS.read_text(encoding="utf-8")
    relocation_payload = STRICT_RELOCATION_PAYLOAD.read_text(encoding="utf-8")

    def body_after(signature: str) -> str:
        return py_source.split(signature, 1)[1].split("\ndef ", 1)[0]

    helper_body = body_after(
        "def _py_obj_visit_covered_slots("
    )
    assert "pcc_gc_visit_object_slots(" in helper_body
    for callback in (
        "_py_obj_visit_update_slot",
        "_py_obj_visit_subtract_slot",
    ):
        assert callback in helper_body
    assert "pcc_gc_visit_object_slots(from_obj, _relocate_count_slot" in (
        relocation_payload
    )
    assert "pcc_gc_visit_object_slots(from_obj, _relocate_from_slot" in (
        relocation_payload
    )
    assert "pcc_gc_visit_object_slots(to_obj, _relocate_to_slot" in (
        relocation_payload
    )

    promotion_body = promotion_source.split(
        "def pcc_gc_trace_referents_for_promotion_mode", 1
    )[1].split(
        '@c_abi_export("pcc_gc_trace_referents_for_promotion")', 1
    )[0]
    assert "pcc_gc_visit_object_slots(" in promotion_body
    assert "_enqueue_promotion_owner(obj)" in promotion_body
    assert "pcc_gc_generational_promote_shallow_slot" in promotion_body
    promotion_drain = promotion_source.split(
        "def pcc_gc_backend3_drain_promotion_worklist", 1
    )[1].split("\n@c_abi_export", 1)[0]
    assert "pcc_gc_visit_object_slots_slice(" in promotion_drain
    assert "pcc_gc_generational_promote_slot" in promotion_drain

    trace_body = mark_source.split("def pcc_gc_trace_referents(obj)", 1)[1].split(
        "\n@c_abi_export", 1
    )[0]
    assert "pcc_gc_visit_object_slots(obj, pcc_gc_trace_slot, null())" in trace_body
    clear_body = sweep_source.split(
        "def pcc_gc_tracing_clear_referents(obj)", 1
    )[1].split("\n@c_abi_export", 1)[0]
    assert (
        "pcc_gc_visit_object_slots(\n        obj, pcc_gc_tracing_clear_slot, null()"
        in clear_body
    )

    subtract_body = body_after("def _subtract_referent_refs(o)")
    assert "_py_obj_visit_covered_slots(o, 4, 0)" in subtract_body
    assert "pcc_gc_visit_object_slots(" not in subtract_body
    remap_source = STRICT_RELOCATION_REMAP.read_text(encoding="utf-8")
    remap_body = remap_source.split(
        "def pcc_gc_backend4_remap_referents(obj)", 1
    )[1]
    assert "pcc_gc_visit_object_slots(" in remap_body
    assert "pcc_gc_backend4_remap_slot" in remap_body


def test_pcc_python_backend0_cycle_collector_reuses_slot_helpers_source():
    py_source = STRICT_BACKEND0_COLLECTOR.read_text(encoding="utf-8")
    strict_source = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    backend0_slots = STRICT_BACKEND0_SLOTS.read_text(encoding="utf-8")
    sweep_slots = STRICT_SWEEP_SLOTS.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in backend0_slots
    assert "pcc_gc_visit_object_slots = extern(" in backend0_slots
    for action in (
        "pcc_gc_backend0_visit_subtract",
        "pcc_gc_backend0_mark_reachable",
        "pcc_gc_backend0_clear_referents",
    ):
        assert f"{action} = extern(" in py_source
        assert f'"{action}"' in py_source
    recompute_body = py_source.split(
        "def _recompute_reachability() -> None:", 1
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_backend0_visit_subtract(" in recompute_body
    assert "pcc_gc_backend0_mark_reachable(" in recompute_body
    collect_body = py_source.split(
        '@c_abi_export("py_gc_collect")', 1
    )[1]
    assert "pcc_gc_backend0_clear_referents(obj)" in collect_body

    subtract_body = backend0_slots.split(
        "def pcc_gc_backend0_subtract_slot(", 1
    )[1].split("\ndef ", 1)[0]
    mark_body = backend0_slots.split(
        "def pcc_gc_backend0_mark_slot(", 1
    )[1].split("\ndef ", 1)[0]
    clear_body = sweep_slots.split(
        "def pcc_gc_backend0_clear_slot(", 1
    )[1].split("\ndef ", 1)[0]
    assert "role == 3" in subtract_body
    assert "pcc_gc_load_ptr(" in subtract_body
    assert "role == 3" in mark_body
    assert "pcc_gc_backend0_mark_reachable(" in mark_body
    assert "role != 1" in clear_body
    assert "py_decref(child)" in clear_body

    continuation_body = strict_source.split(
        "def _visit_continuation_slots(o, visitor, context)", 1
    )[1].split("\ndef ", 1)[0]
    assert "if ptr_is_null(slots) == 0:" in continuation_body
    assert "_visit_slot(slots, index * 8, 1, visitor, context)" in continuation_body

    clear_metadata_body = sweep_slots.split(
        "def pcc_gc_clear_container_metadata(",
        1,
    )[1].split("\n@c_abi_export", 1)[0]
    set_clear_body = clear_metadata_body.split("if tag == PY_TYPE_SET:", 1)[1]
    assert "store_ptr(entries, index * 16 + 8, null())" in set_clear_body
    assert "store_i64(entries, index * 16, 0)" in set_clear_body


def test_pcc_python_function_slot_walkers_match_current_layout_source():
    """Backend #0 and the tracing backends must see every owned function slot."""
    expected_offsets = (24, 32, 40, 64, 80, 88)
    source = STRICT_OBJECT_SLOTS.read_text(encoding="utf-8")
    body = source.split(
        "def _visit_fixed_owner_slots(o, visitor, context)", 1
    )[1].split('if tag == abi_constant("object.type.iter"):', 1)[0]
    func_case = body.split('if tag == abi_constant("object.type.func"):', 1)[1]
    for offset in expected_offsets:
        assert f"_visit_slot(o, {offset}, 1, visitor, context)" in func_case


def test_pcc_python_backend0_runtime_roots_reuse_root_slot_helpers_source():
    py_source = STRICT_BACKEND0_COLLECTOR.read_text(encoding="utf-8")

    for helper_name in (
        "_mapped_root_count",
        "_mark_root_slot",
        "_mark_root_slots",
        "_visit_mapped_root_slots",
        "_visit_scheduler_root_slots",
    ):
        assert f"def {helper_name}(" in py_source

    count_body = py_source.split(
        "def _mapped_root_count(frame_map)",
        1,
    )[1].split("\n@c_abi_export", 1)[0]
    assert "if root_count < 0:" in count_body
    assert "root_count = 0 - root_count" in count_body
    assert "if root_count > 100000:" in count_body

    root_slot_body = py_source.split(
        "def _mark_root_slot(",
        1,
    )[1].split("\n@c_abi_export", 1)[0]
    assert "pcc_gc_load_ptr(" in root_slot_body
    assert "ptr_add(slot_base, slot_offset)" in root_slot_body
    assert "pcc_gc_backend0_mark_reachable(child)" in root_slot_body

    mapped_body = py_source.split(
        "def _visit_mapped_root_slots(frame_map, root_slots)",
        1,
    )[1].split("\n@c_abi_export", 1)[0]
    assert "_mapped_root_count(frame_map)" in mapped_body
    assert "_mark_root_slots(root_slots, root_count)" in mapped_body

    root_slots_body = py_source.split(
        "def _mark_root_slots(",
        1,
    )[1].split("\n@c_abi_export", 1)[0]
    assert "_mark_root_slot(root_slots, i * 8)" in root_slots_body

    scheduler_body = py_source.split(
        "def _visit_scheduler_root_slots()",
        1,
    )[
        1
    ].split("\n@c_abi_export", 1)[0]
    assert 'global_load_ptr("pcc_gc_scheduler_root_head")' in scheduler_body
    assert "_mark_root_slot(slot, 0)" in scheduler_body
    assert "node = load_ptr(node, 8)" in scheduler_body

    runtime_body = py_source.split("def _mark_runtime_roots() -> None:", 1)[1]
    runtime_body = runtime_body.split("\n@c_abi_export", 1)[0]
    assert "_mark_root_slots(" in runtime_body
    assert "_visit_mapped_root_slots(frame_map, slots)" in runtime_body
    assert "_visit_scheduler_root_slots()" in runtime_body
    assert "_mark_reachable(load_ptr(slot, 0))" not in runtime_body


def test_frame_and_continuation_roots_share_mapped_root_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_mapped_root_slots_unlocked"
    helper_start = source.index(f"static int64_t {helper_name}(")
    gray_adapter_start = source.index(
        "static void pcc_gc_gray_mapped_root_slot",
        helper_start,
    )
    helper_body = source[helper_start:gray_adapter_start]
    assert "int64_t root_count" in helper_body
    assert "int64_t n_slots = root_count" in helper_body
    assert "&slots[i]" in helper_body
    assert "stable_values == NULL ? NULL : &stable_values[i]" in helper_body
    assert "borrowed" in helper_body

    promote_start = source.index(
        "void pcc_gc_generational_promote_frame_roots("
    )
    scheduler_promote_start = source.index(
        "void pcc_gc_generational_promote_scheduler_roots(",
        promote_start,
    )
    promote_body = source[promote_start:scheduler_promote_start]
    assert "f->root_count" in promote_body
    assert "c->root_count" in promote_body
    assert "pcc_gc_promote_mapped_root_slot" in promote_body
    assert "pcc_gc_promote_cached_frame_slot(" not in promote_body

    gray_start = source.index("static void pcc_gc_gray_current_roots(")
    subtract_start = source.index(
        "static void pcc_gc_subtract_known_child_ref",
        gray_start,
    )
    gray_body = source[gray_start:subtract_start]
    assert helper_name in gray_body
    assert "pcc_gc_gray_mapped_root_slot" in gray_body
    assert "pcc_gc_gray_mapped_roots_unlocked" not in gray_body

    visit_start = source.index("void pcc_gc_visit_runtime_roots(")
    remap_comment_start = source.index(
        "/* ----- backend-4 remap phase",
        visit_start,
    )
    visit_body = source[visit_start:remap_comment_start]
    assert "pcc_gc_runtime_root_snapshot_fill_batch_unlocked" in visit_body
    assert "PCC_GC_SAFEPOINT_BATCH" in visit_body
    assert visit_body.index("pcc_gc_graph_unlock();") < visit_body.index(
        "visit(roots[index], ctx);"
    )
    assert "pcc_gc_visit_mapped_roots_unlocked" not in visit_body

    remap_start = source.index(
        "static void pcc_gc_backend4_remap_and_retire_unlocked("
    )
    seed_start = source.index("static void pcc_gc_seed_roots(", remap_start)
    remap_body = source[remap_start:seed_start]
    assert helper_name in remap_body
    assert "pcc_gc_rewrite_mapped_root_slot" in remap_body
    assert "pcc_gc_rewrite_mapped_roots_unlocked" not in remap_body


def test_scheduler_roots_share_single_root_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_scheduler_root_slots_unlocked"
    helper_start = source.index(f"static int64_t {helper_name}(")
    promote_start = source.index(
        "void pcc_gc_generational_promote_frame_roots(",
        helper_start,
    )
    helper_body = source[helper_start:promote_start]
    assert "pcc_gc_scheduler_roots" in helper_body
    assert "r->slot" in helper_body
    assert "visit(r->slot, NULL, 0, ctx)" in helper_body

    scheduler_promote_start = source.index(
        "void pcc_gc_generational_promote_scheduler_roots("
    )
    remembered_start = source.index(
        "static void pcc_gc_promote_remembered_owner_referents",
        scheduler_promote_start,
    )
    promote_body = source[scheduler_promote_start:remembered_start]
    assert "pcc_gc_promote_mapped_root_slot" in promote_body
    assert "pcc_gc_promote_young_slot(r->slot)" not in promote_body

    gray_start = source.index("static void pcc_gc_gray_current_roots(")
    subtract_start = source.index(
        "static void pcc_gc_subtract_known_child_ref",
        gray_start,
    )
    gray_body = source[gray_start:subtract_start]
    assert helper_name in gray_body
    assert "pcc_gc_gray_mapped_root_slot" in gray_body
    assert "pcc_gc_resolve_root_slot_unlocked(r->slot)" not in gray_body

    visit_start = source.index("void pcc_gc_visit_runtime_roots(")
    remap_comment_start = source.index(
        "/* ----- backend-4 remap phase",
        visit_start,
    )
    visit_body = source[visit_start:remap_comment_start]
    assert "pcc_gc_runtime_root_snapshot_fill_batch_unlocked" in visit_body
    assert "PCC_GC_SAFEPOINT_BATCH" in visit_body
    assert "visit(*r->slot, ctx)" not in visit_body

    remap_start = source.index(
        "static void pcc_gc_backend4_remap_and_retire_unlocked("
    )
    seed_start = source.index("static void pcc_gc_seed_roots(", remap_start)
    remap_body = source[remap_start:seed_start]
    assert helper_name in remap_body
    assert "pcc_gc_rewrite_mapped_root_slot" in remap_body
    assert "pcc_gc_resolve_root_slot_unlocked(r->slot)" not in remap_body


def test_runtime_root_extension_traversal_runs_after_graph_unlock_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    visit = source.split("void pcc_gc_visit_runtime_roots(", 1)[1].split(
        "/* ----- backend-4 remap phase", 1
    )[0]
    assert visit.index("pcc_gc_graph_unlock();") < visit.index(
        "pcc_capi_visit_extension_module_state_roots(visit, ctx);"
    )


def test_runtime_root_caller_visitor_uses_unlocked_owned_snapshot_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    visit = source.split("void pcc_gc_visit_runtime_roots(", 1)[1].split(
        "/* ----- backend-4 remap phase", 1
    )[0]
    assert "pcc_gc_runtime_root_snapshot_reset_unlocked()" in visit
    assert "pcc_gc_runtime_root_snapshot_fill_batch_unlocked(" in visit
    assert visit.index("pcc_gc_graph_unlock();") < visit.index("malloc(")
    final_unlock = visit.rindex("pcc_gc_graph_unlock();")
    assert final_unlock < visit.index("visit(roots[index], ctx);")
    assert final_unlock < visit.index("py_decref(roots[index]);")
    assert "pcc_runtime_tripwire_fail(" in visit


def test_runtime_root_snapshot_fill_has_bounded_graph_tenures_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    visit = source.split("void pcc_gc_visit_runtime_roots(", 1)[1].split(
        "/* ----- backend-4 remap phase", 1
    )[0]
    assert "pcc_gc_runtime_root_snapshot_fill_batch_unlocked(" in visit
    assert "PCC_GC_SAFEPOINT_BATCH" in visit
    assert "pcc_gc_runtime_root_snapshot_count_unlocked()" not in visit
    assert visit.count("pcc_gc_graph_lock();") >= 2
    assert visit.count("pcc_gc_graph_unlock();") >= 2
    assert visit.index("pcc_gc_graph_unlock();") < visit.index(
        "pcc_gc_runtime_root_snapshot_probe_wait();"
    )
    assert visit.rindex("pcc_gc_graph_unlock();") < visit.index(
        "visit(roots[index], ctx);"
    )

    fill = source.split(
        "static int64_t pcc_gc_runtime_root_snapshot_fill_batch_unlocked(", 1
    )[1].split("void pcc_gc_visit_runtime_roots(", 1)[0]
    assert "examined < budget" in fill
    assert "pcc_gc_runtime_root_snapshot_owner" in fill


def test_capi_py_visit_routes_native_module_state_slots_through_load_barrier_source():
    python_h = (REPO_ROOT / "utils" / "fake_libc_include" / "Python.h").read_text(
        encoding="utf-8"
    )
    # The visit + module-state-roots helpers are owned by pcc-Python runtime
    # modules now (py_capi_visit_runtime.py / py_capi_module_state_runtime.py);
    # the C shim only carries guarded externs.
    visit_source = (RUNTIME_DIR / "py" / "py_capi_visit_runtime.py").read_text(
        encoding="utf-8"
    )
    module_state_source = (
        RUNTIME_DIR / "py" / "py_capi_module_state_runtime.py"
    ).read_text(encoding="utf-8")

    assert "int pcc_capi_visit_slot(" in python_h
    assert "PyObject **slot" in python_h
    assert "pcc_capi_visit_slot((PyObject **)&(op), visit, arg)" in python_h
    assert "visit((PyObject *)(op), arg)" not in python_h

    assert 'def pcc_capi_visit_slot(slot, visit, arg) -> int:' in visit_source
    assert "pcc_gc_load_ptr(null(), slot)" in visit_source
    assert "call_i64_ptr2(visit, obj, arg)" in visit_source

    assert (
        "def pcc_capi_visit_extension_module_state_roots(visit, ctx) -> None:"
        in module_state_source
    )
    assert "pcc_capi_visit_module_state_ref" in module_state_source
    assert "call_i64_ptr3(" in module_state_source


def test_builtin_exception_cache_uses_the_shared_runtime_root_slot_contract():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    mapped_source = (
        RUNTIME_DIR / "py" / "freestanding_gc_mapped_roots.py"
    ).read_text(encoding="utf-8")

    c_helper = c_source.split(
        "static int64_t pcc_gc_visit_builtin_exception_cache_slots_unlocked(",
        1,
    )[1].split("void pcc_gc_generational_promote_frame_roots", 1)[0]
    assert "py_subs_exc_cache_slot(tag)" in c_helper
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split("static void pcc_gc_gray_current_roots(", 1)[1].split(
            "static void pcc_gc_subtract_known_child_ref", 1
        )[0]
    )
    assert (
        "py_subs_exc_cache_slot("
        in c_source.split(
            "void pcc_gc_generational_promote_scheduler_roots(", 1
        )[1].split(
            "static void pcc_gc_promote_remembered_owner_referents", 1
        )[0]
    )
    snapshot_fill = c_source.split(
        "static int64_t pcc_gc_runtime_root_snapshot_fill_batch_unlocked(", 1
    )[1].split("void pcc_gc_visit_runtime_roots(", 1)[0]
    assert "py_subs_exc_cache_slot(" in snapshot_fill
    assert "pcc_gc_snapshot_runtime_mapped_root_slot(" in snapshot_fill
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split(
            "static void pcc_gc_backend4_remap_and_retire_unlocked(", 1
        )[
            1
        ].split("static void pcc_gc_seed_roots", 1)[0]
    )

    py_helper = mapped_source.split(
        "def pcc_gc_visit_builtin_exception_cache_slots(", 1
    )[1].split('@c_abi_export("pcc_gc_gray_mapped_roots")', 1)[0]
    assert "py_subs_exc_cache_slot(0)" in py_helper
    assert "pcc_gc_visit_mapped_root_slots(" in py_helper
    object_root_seeding = (
        RUNTIME_DIR / "py" / "freestanding_gc_object_root_seeding.py"
    ).read_text(encoding="utf-8")
    assert "pcc_gc_visit_registered_root_slots(1, 1)" in object_root_seeding
    generational_scheduler = (
        RUNTIME_DIR / "py" / "freestanding_gc_generational_scheduler.py"
    ).read_text(encoding="utf-8")
    forwarding_retirement = (
        RUNTIME_DIR / "py" / "freestanding_gc_forwarding_retirement.py"
    ).read_text(encoding="utf-8")
    assert "py_subs_exc_cache_slot(slot_index)" in generational_scheduler
    assert "pcc_gc_visit_registered_root_slots(3, 0)" in forwarding_retirement


def test_builtin_exception_cache_is_visible_as_a_runtime_root(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "builtin_exception_cache_root_probe.c"
    exe = tmp_path / "builtin_exception_cache_root_probe.out"
    src.write_text(
        textwrap.dedent(r"""
            #include "py_internal.h"

            static PyObject *expected = NULL;
            static int seen = 0;

            static void find_expected(PyObject *root, void *ctx) {
                (void)ctx;
                if (root == expected) seen++;
            }

            int main(void) {
                pcc_gc_set_backend(PCC_GC_KIND_INCREMENTAL_TRICOLOR);
                expected = (PyObject *)py_exc_builtin_class(
                    PY_EXC_STOPITERATION
                );
                if (expected == NULL) return 10;
                pcc_gc_visit_runtime_roots(find_expected, NULL);
                if (seen != 1) return 11;
                (void)pcc_gc_collect(0);
                PyObject *cached = (PyObject *)py_exc_builtin_class(
                    PY_EXC_STOPITERATION
                );
                if (cached != expected) return 12;
                if (py_header(cached)->type_tag != PY_TYPE_CLASS) return 13;
                return 0;
            }
            """).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_root_snapshot_heap_preserves_all_scheduler_roots(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "runtime_root_snapshot_heap_probe.c"
    exe = tmp_path / "runtime_root_snapshot_heap_probe.out"
    src.write_text(
        textwrap.dedent(r"""
            #include "py_internal.h"

            static PyObject *roots[80];
            static void *handles[80];
            static int seen[80];

            static void observe_root(PyObject *root, void *ctx) {
                (void)ctx;
                for (int i = 0; i < 80; i++) {
                    if (root == roots[i]) seen[i]++;
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_INCREMENTAL_TRICOLOR
                    ) != 0) return 2;
                for (int i = 0; i < 80; i++) {
                    roots[i] = py_list_new(0);
                    if (roots[i] == 0) return 10 + i;
                    handles[i] = pcc_gc_scheduler_root_register_handle(
                        &roots[i]
                    );
                    if (handles[i] == 0) return 100 + i;
                }
                pcc_gc_visit_runtime_roots(observe_root, 0);
                for (int i = 0; i < 80; i++) {
                    if (seen[i] != 1) return 200 + i;
                    pcc_gc_scheduler_root_unregister_handle(handles[i]);
                    py_decref(roots[i]);
                }
                return 0;
            }
            """).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
