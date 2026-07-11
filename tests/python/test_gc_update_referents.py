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
import shutil
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cache_runtime_build

REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


def test_backend4_relocation_reuses_shared_slot_contract():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

    c_contract = c_source.split("typedef struct {\n    PyObject ***from_slots;", 1)[1]
    c_contract = c_contract.split("static int pcc_gc_backend_uses_forwarding", 1)[0]
    assert "py_obj_visit_slots(from" in c_contract
    assert "py_obj_visit_slots(to" in c_contract
    assert "py_obj_update_slot" in c_contract
    c_payload = c_contract.split("static int pcc_gc_relocate_copy_payload(", 1)[1]
    assert "py_incref(" not in c_payload
    assert "pcc_gc_backend4_remembered_set_retarget_slot_unlocked(" not in c_payload

    py_contract = py_source.split("def _relocate_slot_pairs_prepare(", 1)[1]
    py_contract = py_contract.split("def _backend_uses_forwarding", 1)[0]
    assert "_py_obj_visit_covered_slots(from_obj" in py_contract
    assert "_py_obj_visit_covered_slots(to_obj" in py_contract
    assert "_remap_heal_slot" in py_contract
    py_payload = py_contract.split("def _relocate_copy_payload(", 1)[1]
    assert "py_incref(" not in py_payload
    assert "_backend4_remembered_set_retarget_" not in py_payload


def _cc() -> str:
    return os.environ.get("CC", "cc")


@cache_runtime_build
def _build_runtime(tmp_path: Path) -> Path:
    work_runtime = tmp_path / "py_runtime"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    result = subprocess.run(
        ["make", "-B", "-C", str(work_runtime), "libpy_runtime.a"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work_runtime


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

    mirror_source = (RUNTIME_DIR / "py" / "py_class.py").read_text(encoding="utf-8")
    layout_match = re.search(
        r"PyClassObject layout \((\d+) bytes\):(?P<class_body>.*?)"
        r"PyClassMethod \((\d+) bytes\):(?P<method_body>.*?)"
        r"PyInstanceObject:",
        mirror_source,
        re.DOTALL,
    )
    assert layout_match is not None
    mirror_layout = {"class.size": int(layout_match.group(1))}
    for offset, field in re.findall(
        r"offset\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)",
        layout_match.group("class_body"),
    ):
        mirror_layout[f"class.{('h' if field == 'PyObjectHeader' else field)}"] = int(
            offset
        )
    mirror_layout["method.size"] = int(layout_match.group(3))
    for offset, field in re.findall(
        r"offset\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)",
        layout_match.group("method_body"),
    ):
        mirror_layout[f"method.{field}"] = int(offset)

    assert set(mirror_layout) == set(c_layout)
    assert mirror_layout == c_layout

    substrate_source = (RUNTIME_DIR / "py" / "py_substrate.py").read_text(
        encoding="utf-8"
    )
    object_root = substrate_source.split("def py_subs_object_root():", 1)[1]
    object_root = object_root.split("\ndef ", 1)[0]
    class_size = c_layout["class.size"]
    assert f"r = malloc({class_size})" in object_root
    assert f"memset(r, 0, {class_size})" in object_root

    for visitor_path, signature in (
        ("py_obj_gc.py", "def _py_obj_gc_visit_class_slots("),
        ("py_gc_backend.py", "def _py_obj_visit_class_slots("),
    ):
        visitor_source = (RUNTIME_DIR / "py" / visitor_path).read_text(
            encoding="utf-8"
        )
        visitor = visitor_source.split(signature, 1)[1].split("\ndef ", 1)[0]
        for field, accessor in (
            ("n_bases", "load_i32"),
            ("bases", "load_ptr"),
            ("n_mro", "load_i32"),
            ("mro", "load_ptr"),
            ("n_methods", "load_i32"),
            ("methods", "load_ptr"),
        ):
            assert f"{accessor}(o, {c_layout[f'class.{field}']})" in visitor
        for field in ("del_method", "attrs", "metaclass"):
            assert f"{c_layout[f'class.{field}']}," in visitor

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
    py_gc = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

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
    assert "store_ptr(cls, 96, func)" in py_add
    assert "_class_note_borrowed_metadata_slot_store" in py_add

    py_gc_class = py_gc.split("def _py_obj_visit_class_slots(", 1)[1]
    py_gc_class = py_gc_class.split("\ndef ", 1)[0]
    assert "96," in py_gc_class
    assert "_PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in py_gc_class


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
    libpython_source = (RUNTIME_DIR / "src" / "py_libpython.c").read_text(
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
    assert visit_body.index(cext_visit) < visit_body.index(
        "pcc_gc_visit_instance_owner_slots("
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

    assert "int pcc_capi_visit_cext_object_slots(" in libpython_source


def test_pcc_python_cext_object_slot_bridge_source():
    header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")
    shim_source = (RUNTIME_DIR / "src" / "py_capi_shim.c").read_text(encoding="utf-8")
    libpython_source = (RUNTIME_DIR / "src" / "py_libpython.c").read_text(
        encoding="utf-8"
    )
    gc_backend_py = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(
        encoding="utf-8"
    )
    obj_gc_py = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")

    assert "PccPyObjSlotVisitorI64" in header
    assert "int pcc_capi_visit_cext_object_slots_i64(" in header
    assert "int pcc_capi_visit_cext_object_slots_i64(" in shim_source
    assert "pcc_capi_visit_cext_object_slot_i64_adapter" in shim_source
    assert "int pcc_capi_visit_cext_object_slots_i64(" in libpython_source

    assert "pcc_capi_visit_cext_object_slots_i64 = extern(" in gc_backend_py
    assert "pcc_capi_visit_cext_object_slots_i64 = extern(" in obj_gc_py

    gc_callback = gc_backend_py[
        gc_backend_py.index(
            "def _py_obj_visit_cext_object_slot("
        ) : gc_backend_py.index("def _py_obj_visit_cext_object_slots(")
    ]
    gc_bridge = gc_backend_py[
        gc_backend_py.index(
            "def _py_obj_visit_cext_object_slots("
        ) : gc_backend_py.index("def _py_obj_visit_instance_owner_slots(")
    ]
    assert "_py_obj_visit_slot(slot, 0, role, mode, recurse)" in gc_callback
    assert "pcc_capi_visit_cext_object_slots_i64(" in gc_bridge
    assert "_py_obj_visit_cext_object_slot" in gc_bridge

    covered_body = gc_backend_py[
        gc_backend_py.index("def _py_obj_visit_covered_slots(") : gc_backend_py.index(
            "def _trace_referents("
        )
    ]
    cext_call = "_py_obj_visit_cext_object_slots("
    assert cext_call in covered_body
    assert covered_body.index(cext_call) < covered_body.index(
        "_py_obj_visit_instance_owner_slots("
    )

    obj_callback = obj_gc_py[
        obj_gc_py.index("def _py_obj_gc_visit_cext_object_slot(") : obj_gc_py.index(
            "def _py_obj_gc_visit_cext_object_slots("
        )
    ]
    obj_bridge = obj_gc_py[
        obj_gc_py.index("def _py_obj_gc_visit_cext_object_slots(") : obj_gc_py.index(
            "def _py_obj_gc_visit_instance_owner_slots("
        )
    ]
    assert "_py_obj_gc_visit_slot(slot, 0, role, mode, out)" in obj_callback
    assert "pcc_capi_visit_cext_object_slots_i64(" in obj_bridge
    assert "_py_obj_gc_visit_cext_object_slot" in obj_bridge

    obj_covered = obj_gc_py[
        obj_gc_py.index("def _py_obj_gc_visit_covered_slots(") : obj_gc_py.index(
            "def _visit_subtract("
        )
    ]
    cext_call = "_py_obj_gc_visit_cext_object_slots("
    assert cext_call in obj_covered
    assert obj_covered.index(cext_call) < obj_covered.index(
        "_py_obj_gc_visit_instance_owner_slots("
    )


def test_capi_extension_dynamic_tags_do_not_use_instance_layout_source():
    gc_backend_c = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    obj_gc_c = (RUNTIME_DIR / "src" / "py_obj_gc.c").read_text(encoding="utf-8")
    dunder_c = (RUNTIME_DIR / "src" / "py_dunder.c").read_text(encoding="utf-8")
    gc_backend_py = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(
        encoding="utf-8"
    )
    obj_gc_py = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")
    obj_dealloc_py = (RUNTIME_DIR / "py" / "py_obj_dealloc.py").read_text(
        encoding="utf-8"
    )
    dunder_py = (RUNTIME_DIR / "py" / "py_dunder.py").read_text(encoding="utf-8")

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
        "h->type_tag >= PY_TYPE_USER"
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
    assert "pcc_capi_dealloc_cext_object = extern(" in gc_backend_py
    py_instance_body = gc_backend_py[
        gc_backend_py.index(
            "def _py_obj_visit_instance_owner_slots("
        ) : gc_backend_py.index("def _py_obj_has_no_pointer_slots(")
    ]
    assert "pcc_capi_is_cext_type_tag(tag) != 0" in py_instance_body

    py_colored_body = gc_backend_py[
        gc_backend_py.index(
            "def _colored_relocate_copy_supported_tag("
        ) : gc_backend_py.index("def _relocate_copy_payload(")
    ]
    py_cext_guard = "if pcc_capi_is_cext_type_tag(tag) != 0:"
    assert py_cext_guard in py_colored_body
    assert py_colored_body.index(py_cext_guard) < py_colored_body.index(
        "if tag == 11 or tag >= 104:"
    )

    py_relocate_start = gc_backend_py.index("def _relocate_copy_payload(")
    py_relocate_body = gc_backend_py[
        py_relocate_start : gc_backend_py.index(
            "if tag == 6:  # PY_TYPE_DICT", py_relocate_start
        )
    ]
    assert py_cext_guard in py_relocate_body
    assert py_relocate_body.index(py_cext_guard) < py_relocate_body.index(
        "if tag == 11 or tag >= 104:"
    )
    assert "cls = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 16))" in (
        py_relocate_body
    )
    assert "_relocate_slot_pairs_prepare(from_obj, to_obj, size)" in py_relocate_body
    assert "_relocate_copy_payload_finish(" in py_relocate_body
    assert (
        "child = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, offset))"
        not in py_relocate_body
    )

    c_relocate_start = gc_backend_c.index("static int pcc_gc_relocate_copy_payload")
    c_relocate_body = gc_backend_c[
        c_relocate_start : gc_backend_c.index(
            "if (tag == PY_TYPE_DICT)", c_relocate_start
        )
    ]
    assert "PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(" in c_relocate_body
    assert "pcc_gc_relocate_slot_pairs_prepare(from, to, size" in c_relocate_body
    assert "pcc_gc_relocate_copy_slots(from, to, &pairs)" not in c_relocate_body
    assert "PyObject *child = pcc_gc_load_ptr(from, &src->fields[i])" not in (
        c_relocate_body
    )

    py_finalize_body = gc_backend_py[
        gc_backend_py.index(
            "def _finalize_unreachable(o) -> None:"
        ) : gc_backend_py.index("def _recheck_reachability_after_finalizers")
    ]
    assert "pcc_capi_dealloc_cext_object(o, tag) != 0" in py_finalize_body
    assert py_finalize_body.index(
        "pcc_capi_dealloc_cext_object(o, tag) != 0"
    ) < py_finalize_body.index("if tag >= 104:")

    py_sweep_body = gc_backend_py[
        gc_backend_py.index(
            "def _sweep_unreachable(budget: int) -> int:"
        ) : gc_backend_py.index("    _recheck_reachability_after_finalizers()")
    ]
    assert "pcc_capi_is_cext_type_tag(tag) == 0" in py_sweep_body
    assert py_sweep_body.index("pcc_capi_is_cext_type_tag(tag) == 0") < (
        py_sweep_body.index("py_user_del_dispatch(o)")
    )

    assert "pcc_capi_is_cext_type_tag = extern(" in obj_gc_py
    assert "pcc_capi_dealloc_cext_object = extern(" in obj_gc_py
    py_obj_instance_body = obj_gc_py[
        obj_gc_py.index("def _py_obj_gc_visit_instance_owner_slots(") : obj_gc_py.index(
            "def _py_obj_gc_has_no_pointer_slots("
        )
    ]
    assert "pcc_capi_is_cext_type_tag(tag) != 0" in py_obj_instance_body
    assert py_obj_instance_body.index(
        "pcc_capi_is_cext_type_tag(tag) != 0"
    ) < py_obj_instance_body.index("tag != 11 and tag != 200 and tag < 104")
    py_obj_maybe_body = obj_gc_py[
        obj_gc_py.index("def _maybe_finalize_unreachable(") : obj_gc_py.index(
            "def _visit_mark(o)"
        )
    ]
    assert "pcc_capi_is_cext_type_tag(tag) == 0" in py_obj_maybe_body
    py_obj_dealloc_body = obj_gc_py[
        obj_gc_py.index("def _dealloc_unreachable(o) -> None:") : obj_gc_py.index(
            '@c_abi_export("py_gc_init")'
        )
    ]
    assert "pcc_capi_dealloc_cext_object(o, tag) != 0" in py_obj_dealloc_body
    assert py_obj_dealloc_body.index(
        "pcc_capi_dealloc_cext_object(o, tag) != 0"
    ) < py_obj_dealloc_body.index("elif tag >= 100:")

    py_refcount_body = obj_dealloc_py[
        obj_dealloc_py.index(
            "def _dealloc_dispatch(o, tag: int) -> None:"
        ) : obj_dealloc_py.index("def _trash_enqueue(o, tag: int)")
    ]
    assert "pcc_capi_dealloc_cext_object(o, tag) != 0" in py_refcount_body
    assert py_refcount_body.index(
        "pcc_capi_dealloc_cext_object(o, tag) != 0"
    ) < py_refcount_body.index("if tag >= 100:")

    py_del_body = dunder_py[
        dunder_py.index("def py_user_del_dispatch(o) -> None:") : dunder_py.index(
            "    flags: int = load_i32(o, 12)"
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
    assert helper_name in visit_body

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
        "typedef struct {\n    void (*visit)(PyObject *child);",
        helper_start,
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
    assert helper_name in visit_body
    for body in (promote_body, trace_body, update_body):
        assert "py_obj_visit_slots(" in body


def test_trace_update_and_promotion_share_continuation_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_continuation_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        helper_start,
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
    assert helper_name in visit_body
    for body in (promote_body, trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "if (tag == PY_TYPE_CONTINUATION)" not in body


def test_trace_update_and_promotion_share_instance_owner_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_instance_owner_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        helper_start,
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
    assert helper_name in visit_body
    for body in (promote_body, trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START" not in body


def test_trace_update_and_promotion_share_class_slot_walker_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_class_slots"
    helper_start = source.index(f"static int {helper_name}(")
    trace_adapter_start = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        helper_start,
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
    assert helper_name in visit_body
    for body in (promote_body, trace_body, update_body):
        assert "py_obj_visit_slots(" in body
        assert "if (tag == PY_TYPE_CLASS)" not in body

    assert "pcc_gc_promote_owner_slot" in promote_body
    assert "pcc_gc_update_owner_slot" in update_body
    assert "visit(cls->methods[i].func)" not in trace_body
    assert "visit(cls->del_method)" not in trace_body


def test_weakref_target_is_update_only_slot_contract_source():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    py_obj_gc_source = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")

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
    assert "pcc_gc_visit_weakref_slots(" in c_visit_body
    assert "py_obj_visit_borrowed_update_only_slot" in c_visit_body

    for source, prefix, covered_name, fixed_name, weak_name in (
        (
            py_source,
            "_py_obj",
            "def _py_obj_visit_covered_slots(o, mode: int, recurse: int)",
            "_py_obj_visit_fixed_owner_slots",
            "_py_obj_visit_weakref_slots",
        ),
        (
            py_obj_gc_source,
            "_py_obj_gc",
            "def _py_obj_gc_visit_covered_slots(o, mode: int, out)",
            "_py_obj_gc_visit_fixed_owner_slots",
            "_py_obj_gc_visit_weakref_slots",
        ),
    ):
        helper_start = source.index(f"def {weak_name}(")
        helper_end = source.index("\ndef ", helper_start + 1)
        helper_body = source[helper_start:helper_end]
        helper_compact = "".join(helper_body.split())
        assert "tag != 21" in helper_body
        assert f"{prefix}_visit_slot(o,16,3," in helper_compact
        assert f"{prefix}_visit_slot(o,24,1," in helper_compact

        fixed_start = source.index(f"def {fixed_name}(")
        fixed_end = source.index(f"def {weak_name}(", fixed_start)
        fixed_body = source[fixed_start:fixed_end]
        assert "tag == 21" not in fixed_body

        covered_body = source.split(covered_name, 1)[1]
        covered_body = covered_body.split("\ndef ", 1)[0]
        assert f"{weak_name}(o," in covered_body


def test_object_slot_contract_has_named_visit_and_update_entrypoints_source():
    source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")

    assert "typedef void (*PyObjSlotVisitor)(" in header
    assert "PY_OBJ_SLOT_OWNED" in header
    assert "PY_OBJ_SLOT_BORROWED_TRACED" in header
    assert "PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in header
    assert "int py_obj_visit_slots(" in header
    assert "void py_obj_update_slot(PyObject **slot)" in header

    visit_start = source.index("int py_obj_visit_slots(")
    trace_ctx_start = source.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        visit_start,
    )
    visit_body = source[visit_start:trace_ctx_start]
    for helper in (
        "pcc_gc_visit_core_container_owner_slots",
        "pcc_gc_visit_fixed_owner_slots",
        "pcc_gc_visit_weakref_slots",
        "pcc_gc_visit_continuation_owner_slots",
        "pcc_gc_visit_class_slots",
        "pcc_gc_visit_instance_owner_slots",
    ):
        assert helper in visit_body
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
    for body in (promote_body, trace_body, update_body):
        assert "py_obj_visit_slots(" in body

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

    c_trace_start = c_source.index("static void pcc_gc_trace_owner_slot(")
    c_update_start = c_source.index(
        "typedef struct {\n    void (*update)(PyObject **slot);",
        c_trace_start,
    )
    c_trace_body = c_source[c_trace_start:c_update_start]
    assert "PyObject *child = pcc_gc_load_ptr(NULL, slot)" in c_trace_body
    assert "trace_ctx->visit(child)" in c_trace_body
    assert "trace_ctx->visit(*slot)" not in c_trace_body

    py_visit_start = py_source.index("def _py_obj_visit_slot(")
    py_core_start = py_source.index(
        "def _py_obj_visit_core_container_owner_slots",
        py_visit_start,
    )
    py_visit_body = py_source[py_visit_start:py_core_start]
    assert "pcc_gc_load_ptr_extern(" in py_visit_body
    assert "ptr_add(slot_base, slot_offset)" in py_visit_body
    trace_case = py_visit_body.split("if mode == 1:", 1)[1].split(
        "if mode == 2:",
        1,
    )[0]
    subtract_case = py_visit_body.split("if mode == 4:", 1)[1].split(
        "if mode == 5:",
        1,
    )[0]
    for case in (trace_case, subtract_case):
        assert "child = pcc_gc_load_ptr_extern(" in case
        assert "null()," in case
        assert "ptr_add(slot_base, slot_offset)" in case
        assert "load_ptr(slot_base, slot_offset)" not in case


def test_no_pointer_slot_families_are_explicitly_classified_source():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

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
    assert "if (py_obj_has_no_pointer_slots(o)) return 1;" in c_visit_body

    py_helper_start = py_source.index("def _py_obj_has_no_pointer_slots(o) -> int:")
    py_helper_end = py_source.index("def _trace_referents(o)", py_helper_start)
    py_helper_body = py_source[py_helper_start:py_helper_end]
    for token in (
        "tag == 0:  # PY_TYPE_NONE",
        "tag == 1:  # PY_TYPE_BOOL",
        "tag == 2:  # PY_TYPE_INT",
        "tag == 3:  # PY_TYPE_FLOAT",
        "tag == 4:  # PY_TYPE_STR",
        "tag == 16:  # PY_TYPE_COMPLEX",
        "tag == 17:  # PY_TYPE_BYTES",
        "tag == 18:  # PY_TYPE_BYTEARRAY",
        "tag == 13:  # PY_TYPE_FILE",
        "tag == 22:  # PY_TYPE_THREAD_LOCK",
        "tag == 23:  # PY_TYPE_THREAD_RLOCK",
        "tag == 24:  # PY_TYPE_THREAD_EVENT",
        "tag == 25:  # PY_TYPE_THREAD_CONDITION",
        "tag == 26:  # PY_TYPE_THREAD_SEMAPHORE",
        "tag == 32:  # PY_TYPE_CPY_HANDLE",
    ):
        assert token in py_helper_body

    py_covered_start = py_source.index(
        "def _py_obj_visit_covered_slots(o, mode: int, recurse: int) -> int:"
    )
    py_covered_end = py_source.index("def _trace_referents(o)", py_covered_start)
    py_covered_body = py_source[py_covered_start:py_covered_end]
    assert "if _py_obj_has_no_pointer_slots(o) != 0:" in py_covered_body
    assert "return 1" in py_covered_body

    for func_name in (
        "def _trace_referents(o)",
        "def _subtract_referent_refs(o)",
        "def _trace_referents_for_promotion_mode(o, recurse: int)",
        "def _remap_referents(o)",
        "def _clear_referents(o)",
    ):
        body = py_source.split(func_name, 1)[1].split("\ndef ", 1)[0]
        assert "_py_obj_has_no_pointer_slots(o)" in body


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
        "PY_TYPE_VALUEBOX",
    }
    # PY_TYPE_USER is the dynamic-tag threshold, not a concrete allocation tag.
    assert current_tags == pointerless | slot_bearing | {"PY_TYPE_USER"}

    no_pointer_body = c_source.split(
        "static int py_obj_has_no_pointer_slots(", 1
    )[1].split("int py_obj_visit_slots(", 1)[0]
    for token in pointerless:
        assert token in no_pointer_body

    visit_contract = c_source.split("int py_obj_visit_slots(", 1)[1].split(
        "typedef struct {\n    void (*visit)(PyObject *child);", 1
    )[0]
    for helper in (
        "pcc_gc_visit_core_container_owner_slots",
        "pcc_gc_visit_fixed_owner_slots",
        "pcc_gc_visit_weakref_slots",
        "pcc_gc_visit_continuation_owner_slots",
        "pcc_gc_visit_class_slots",
        "pcc_capi_visit_cext_object_slots",
        "pcc_gc_visit_instance_owner_slots",
    ):
        assert helper in visit_contract

    for descriptor_tag in (
        "PY_TYPE_PROPERTY",
        "PY_TYPE_CLASSMETHOD",
        "PY_TYPE_STATICMETHOD",
    ):
        assert f"#define {descriptor_tag}" in internal_header
        assert descriptor_tag in c_source
    assert "#define PY_TYPE_USER_CLASS_START" in internal_header
    assert "tag < PY_TYPE_USER_CLASS_START" in c_source


def test_unreachable_file_uses_file_deallocator_in_c_and_python_mirror():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

    c_start = c_source.index("static void pcc_gc_finalize_unreachable(")
    c_end = c_source.index("static void pcc_gc_seed_roots(", c_start)
    c_body = c_source[c_start:c_end]
    assert "case PY_TYPE_FILE:      py_dealloc_file(o);" in c_body

    py_start = py_source.index("def _finalize_unreachable(o) -> None:")
    py_end = py_source.index("def _recheck_reachability_after_finalizers", py_start)
    py_body = py_source[py_start:py_end]
    file_case = py_body.split("if tag == 13:", 1)[1].split("if tag ==", 1)[0]
    assert "py_dealloc_file(o)" in file_case
    assert "_finish_delayed_zpage_freeing_note" in file_case
    assert "return" in file_case


def test_pcc_python_gc_backend_consumers_share_slot_family_helper_source():
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

    def body_after(signature: str) -> str:
        return py_source.split(signature, 1)[1].split("\ndef ", 1)[0]

    helper_body = body_after(
        "def _py_obj_visit_covered_slots(o, mode: int, recurse: int)"
    )
    helper_sequence = (
        "_py_obj_visit_core_container_owner_slots(o, mode, recurse)",
        "_py_obj_visit_fixed_owner_slots(o, mode, recurse)",
        "_py_obj_visit_weakref_slots(o, mode, recurse)",
        "_py_obj_visit_continuation_owner_slots(o, mode, recurse)",
        "_py_obj_visit_class_slots(o, mode, recurse)",
        "_py_obj_visit_cext_object_slots(o, mode, recurse)",
        "_py_obj_visit_instance_owner_slots(o, mode, recurse)",
    )
    last_pos = -1
    for helper_call in helper_sequence:
        pos = helper_body.index(helper_call)
        assert pos > last_pos
        last_pos = pos

    consumer_modes = (
        ("def _trace_referents(o)", "1", "0"),
        ("def _subtract_referent_refs(o)", "4", "0"),
        ("def _trace_referents_for_promotion_mode(o, recurse: int)", "2", "recurse"),
        ("def _remap_referents(o)", "3", "0"),
        ("def _clear_referents(o)", "5", "0"),
    )
    direct_helpers = tuple(name.split("(", 1)[0] for name in helper_sequence)
    for signature, mode, recurse in consumer_modes:
        consumer_body = body_after(signature)
        assert f"_py_obj_visit_covered_slots(o, {mode}, {recurse})" in consumer_body
        for helper_name in direct_helpers:
            assert helper_name not in consumer_body


def test_pcc_python_backend0_cycle_collector_reuses_slot_helpers_source():
    py_source = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")

    slot_adapter_start = py_source.index("def _py_obj_gc_visit_slot(")
    helper_start = py_source.index(
        "def _py_obj_gc_visit_core_container_owner_slots(",
        slot_adapter_start,
    )
    slot_adapter_body = py_source[slot_adapter_start:helper_start]
    assert "pcc_gc_load_ptr_extern(" in slot_adapter_body
    assert "ptr_add(slot_base, slot_offset)" in slot_adapter_body
    assert "role != 3" in slot_adapter_body
    assert "role == 1" in slot_adapter_body

    for helper_name in (
        "_py_obj_gc_visit_core_container_owner_slots",
        "_py_obj_gc_visit_fixed_owner_slots",
        "_py_obj_gc_visit_weakref_slots",
        "_py_obj_gc_visit_continuation_owner_slots",
        "_py_obj_gc_visit_class_slots",
        "_py_obj_gc_visit_instance_owner_slots",
    ):
        assert f"def {helper_name}(" in py_source

    covered_body = py_source.split(
        "def _py_obj_gc_visit_covered_slots(o, mode: int, out)",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "_py_obj_gc_has_no_pointer_slots(o)" in covered_body
    for helper_name in (
        "_py_obj_gc_visit_core_container_owner_slots",
        "_py_obj_gc_visit_fixed_owner_slots",
        "_py_obj_gc_visit_weakref_slots",
        "_py_obj_gc_visit_continuation_owner_slots",
        "_py_obj_gc_visit_class_slots",
        "_py_obj_gc_visit_instance_owner_slots",
    ):
        assert f"{helper_name}(o, mode, out)" in covered_body

    for func_name, mode in (
        ("def _visit_subtract(o)", "1"),
        ("def _append_referents_to(o, out)", "2"),
        ("def _visit_mark(o)", "3"),
        ("def _clear_referents(o)", "4"),
    ):
        body = py_source.split(func_name, 1)[1].split("\ndef ", 1)[0]
        assert f"_py_obj_gc_visit_covered_slots(o, {mode}," in body

    subtract_body = py_source.split("def _visit_subtract(o)", 1)[1].split(
        "\ndef ",
        1,
    )[0]
    for old_direct_case in (
        "if tag == 5:",
        "if tag == 6:",
        "if tag == 8:",
        "if tag == 29:",
        "if tag == 11 or tag >= 100:",
    ):
        assert old_direct_case not in subtract_body

    continuation_body = py_source.split(
        "def _py_obj_gc_visit_continuation_owner_slots(o, mode: int, out)",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "if ptr_is_null(slots) != 0:" in continuation_body
    assert "_py_obj_gc_visit_slot(slots, i * 8, 1, mode, out)" in continuation_body

    clear_metadata_body = py_source.split(
        "def _py_obj_gc_clear_container_metadata(o, tag: int)",
        1,
    )[1].split("\ndef _clear_referents(o)", 1)[0]
    set_clear_body = clear_metadata_body.split("if tag == 8:", 1)[1]
    assert "store_ptr(key_slot, 0, null())" in set_clear_body
    assert "store_i64(entries, i * 16, 0)" in set_clear_body
    assert "ptr_eq(key, dummy)" not in set_clear_body
    assert "if ptr_is_null(key)" not in set_clear_body


def test_pcc_python_function_slot_walkers_match_current_layout_source():
    """Backend #0 and the tracing backends must see every owned function slot."""
    expected_offsets = (24, 32, 40, 64, 80, 88)
    sources_and_signatures = (
        (
            RUNTIME_DIR / "py" / "py_obj_gc.py",
            "def _py_obj_gc_visit_fixed_owner_slots(o, mode: int, out) -> int:",
            "_py_obj_gc_visit_slot",
        ),
        (
            RUNTIME_DIR / "py" / "py_gc_backend.py",
            "def _py_obj_visit_fixed_owner_slots(o, mode: int, recurse: int) -> int:",
            "_py_obj_visit_slot",
        ),
    )
    for path, signature, visitor in sources_and_signatures:
        source = path.read_text(encoding="utf-8")
        body = source.split(signature, 1)[1].split("\n    if tag == 14:", 1)[0]
        func_case = body.split("if tag == 9:", 1)[1]
        for offset in expected_offsets:
            assert f"{visitor}(o, {offset}, 1, mode," in func_case


def test_pcc_python_backend0_runtime_roots_reuse_root_slot_helpers_source():
    py_source = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")

    for helper_name in (
        "_py_obj_gc_mapped_root_count",
        "_py_obj_gc_mark_root_slot",
        "_py_obj_gc_mark_root_slots",
        "_py_obj_gc_visit_mapped_root_slots",
        "_py_obj_gc_visit_scheduler_root_slots",
    ):
        assert f"def {helper_name}(" in py_source

    count_body = py_source.split(
        "def _py_obj_gc_mapped_root_count(frame_map) -> int:",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "if root_count < 0:" in count_body
    assert "root_count = 0 - root_count" in count_body
    assert "if root_count > 100000:" in count_body

    root_slot_body = py_source.split(
        "def _py_obj_gc_mark_root_slot(slot_base, slot_offset: int)",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "pcc_gc_load_ptr_extern(" in root_slot_body
    assert "ptr_add(slot_base, slot_offset)" in root_slot_body
    assert "_mark_reachable(child)" in root_slot_body

    mapped_body = py_source.split(
        "def _py_obj_gc_visit_mapped_root_slots(frame_map, root_slots)",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "_py_obj_gc_mapped_root_count(frame_map)" in mapped_body
    assert "_py_obj_gc_mark_root_slots(root_slots, root_count)" in mapped_body

    root_slots_body = py_source.split(
        "def _py_obj_gc_mark_root_slots(root_slots, root_count: int)",
        1,
    )[1].split("\ndef ", 1)[0]
    assert "_py_obj_gc_mark_root_slot(root_slots, i * 8)" in root_slots_body

    scheduler_body = py_source.split(
        "def _py_obj_gc_visit_scheduler_root_slots()",
        1,
    )[
        1
    ].split("\ndef ", 1)[0]
    assert 'global_load_ptr("pcc_gc_scheduler_root_head")' in scheduler_body
    assert "_py_obj_gc_mark_root_slot(slot, 0)" in scheduler_body
    assert "node = load_ptr(node, 8)" in scheduler_body

    runtime_body = py_source.split("def _mark_runtime_roots() -> None:", 1)[1]
    runtime_body = runtime_body.split("\ndef ", 1)[0]
    assert "_py_obj_gc_mark_root_slots(root_slots, load_i32(" in runtime_body
    assert "_py_obj_gc_visit_mapped_root_slots(frame_map, slots)" in runtime_body
    assert "_py_obj_gc_visit_scheduler_root_slots()" in runtime_body
    assert "def _mark_root_slots(" not in py_source
    assert "\n        _mark_root_slots(" not in runtime_body
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

    promote_start = source.index("static void pcc_gc_promote_frame_roots(")
    scheduler_promote_start = source.index(
        "static void pcc_gc_promote_scheduler_roots(",
        promote_start,
    )
    promote_body = source[promote_start:scheduler_promote_start]
    assert helper_name in promote_body
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
    assert helper_name in visit_body
    assert "pcc_gc_visit_runtime_mapped_root_slot" in visit_body
    assert "pcc_gc_visit_mapped_roots_unlocked" not in visit_body

    remap_start = source.index("static void pcc_gc_backend4_remap_and_retire_unlocked(")
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
        "static void pcc_gc_promote_frame_roots(",
        helper_start,
    )
    helper_body = source[helper_start:promote_start]
    assert "pcc_gc_scheduler_roots" in helper_body
    assert "r->slot" in helper_body
    assert "visit(r->slot, NULL, 0, ctx)" in helper_body

    scheduler_promote_start = source.index(
        "static void pcc_gc_promote_scheduler_roots("
    )
    remembered_start = source.index(
        "static void pcc_gc_promote_remembered_owner_referents",
        scheduler_promote_start,
    )
    promote_body = source[scheduler_promote_start:remembered_start]
    assert helper_name in promote_body
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
    assert helper_name in visit_body
    assert "pcc_gc_visit_runtime_mapped_root_slot" in visit_body
    assert "visit(*r->slot, ctx)" not in visit_body

    remap_start = source.index("static void pcc_gc_backend4_remap_and_retire_unlocked(")
    seed_start = source.index("static void pcc_gc_seed_roots(", remap_start)
    remap_body = source[remap_start:seed_start]
    assert helper_name in remap_body
    assert "pcc_gc_rewrite_mapped_root_slot" in remap_body
    assert "pcc_gc_resolve_root_slot_unlocked(r->slot)" not in remap_body


def test_capi_py_visit_routes_native_module_state_slots_through_load_barrier_source():
    python_h = (REPO_ROOT / "utils" / "fake_libc_include" / "Python.h").read_text(
        encoding="utf-8"
    )
    shim_source = (RUNTIME_DIR / "src" / "py_capi_shim.c").read_text(encoding="utf-8")

    assert "int pcc_capi_visit_slot(" in python_h
    assert "PyObject **slot" in python_h
    assert "pcc_capi_visit_slot((PyObject **)&(op), visit, arg)" in python_h
    assert "visit((PyObject *)(op), arg)" not in python_h

    helper_start = shim_source.index("int pcc_capi_visit_slot(")
    state_visit_start = shim_source.index(
        "typedef struct PccCapiModuleStateVisitCtx",
        helper_start,
    )
    helper_body = shim_source[helper_start:state_visit_start]
    assert "PyObject **slot" in helper_body
    assert "pcc_gc_load_ptr(NULL, slot)" in helper_body
    assert "visit(obj, arg)" in helper_body

    roots_start = shim_source.index("void pcc_capi_visit_extension_module_state_roots(")
    module_exec_start = shim_source.index(
        "PyObject *pcc_capi_module_from_def(",
        roots_start,
    )
    roots_body = shim_source[roots_start:module_exec_start]
    assert "pcc_capi_visit_module_state_ref" in roots_body
    assert (
        "traverse(n->module, pcc_capi_visit_module_state_ref, &visit_ctx)" in roots_body
    )


def test_builtin_exception_cache_uses_the_shared_runtime_root_slot_contract():
    c_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_source = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

    c_helper = c_source.split(
        "static int64_t pcc_gc_visit_builtin_exception_cache_slots_unlocked(",
        1,
    )[1].split("static void pcc_gc_promote_frame_roots", 1)[0]
    assert "py_subs_exc_cache_slot(tag)" in c_helper
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split("static void pcc_gc_gray_current_roots(", 1)[1].split(
            "static void pcc_gc_subtract_known_child_ref", 1
        )[0]
    )
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split("static void pcc_gc_promote_scheduler_roots(", 1)[1].split(
            "static void pcc_gc_promote_remembered_owner_referents", 1
        )[0]
    )
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split("void pcc_gc_visit_runtime_roots(", 1)[1].split(
            "/* ----- backend-4 remap phase", 1
        )[0]
    )
    assert (
        "pcc_gc_visit_builtin_exception_cache_slots_unlocked("
        in c_source.split("static void pcc_gc_backend4_remap_and_retire_unlocked(", 1)[
            1
        ].split("static void pcc_gc_seed_roots", 1)[0]
    )

    py_helper = py_source.split("def _py_visit_builtin_exception_cache_slots(", 1)[
        1
    ].split("def _gray_mapped_roots", 1)[0]
    assert 'global_addr("py_exc_classes")' in py_helper
    assert "_py_visit_mapped_root_slots(" in py_helper
    assert "_py_visit_builtin_exception_cache_slots(1, 1)" in py_source
    assert "_py_visit_builtin_exception_cache_slots(2, 0)" in py_source
    assert "_py_visit_builtin_exception_cache_slots(3, 0)" in py_source


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
