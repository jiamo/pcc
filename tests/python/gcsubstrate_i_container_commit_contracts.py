"""Dict/set commit contracts under the graph lock.

Split from ``test_gc_threading_substrate.py``; collected only through
that facade so pytest node ids stay stable.
"""
from _gc_substrate_common import *  # noqa: F401,F403




def test_dict_set_commits_key_value_index_and_size_under_graph_lock():
    """Proposal No.23: dict fresh insert and value replacement publish their
    structural state inside one graph-locked transaction and release the
    displaced value only after the lock is dropped."""
    c_source = (RUNTIME_DIR / "src" / "py_dict.c").read_text(encoding="utf-8")
    c_insert = c_source.split("static int py_dict_insert_rooted_slot(", 1)[1].split(
        "static int py_dict_replace_value_rooted_slot(", 1
    )[0]
    key_plan = c_insert.index("pcc_gc_store_ptr_plan_init(&key_plan")
    value_plan = c_insert.index("pcc_gc_store_ptr_plan_init(&value_plan")
    lock = c_insert.index("pcc_gc_root_slot_lock()", value_plan)
    key_commit = c_insert.index("&key_plan, dict, &entry->key", lock)
    value_commit = c_insert.index("&value_plan, dict, &entry->value", key_commit)
    index_publish = c_insert.index("indices[slot] = ei", value_commit)
    size = c_insert.index("d->size++", index_publish)
    unlock = c_insert.index("pcc_gc_root_slot_unlock()", size)
    key_finish = c_insert.index("pcc_gc_store_ptr_plan_finish(&key_plan)", unlock)
    value_finish = c_insert.index("pcc_gc_store_ptr_plan_finish(&value_plan)", unlock)
    assert (
        key_plan < value_plan < lock < key_commit < value_commit
        < index_publish < size < unlock < key_finish
    )
    assert value_finish > unlock
    assert "py_decref" not in c_insert[lock:unlock]

    c_replace = c_source.split(
        "static int py_dict_replace_value_rooted_slot(", 1
    )[1].split("static int py_dict_del_rooted_slot(", 1)[0]
    plan = c_replace.index("pcc_gc_store_ptr_plan_init")
    lock = c_replace.index("pcc_gc_root_slot_lock()", plan)
    commit = c_replace.index("&entry->value", lock)
    unlock = c_replace.index("pcc_gc_root_slot_unlock()", commit)
    finish = c_replace.index("pcc_gc_store_ptr_plan_finish", unlock)
    assert plan < lock < commit < unlock < finish
    # Replacement keeps the original stored key: the key slot is never written.
    assert "&entry->key" not in c_replace
    assert "py_decref" not in c_replace[lock:unlock]

    c_public_set = c_source.split("void py_dict_set(", 1)[1].split(
        "\nPyObject *py_dict_get(", 1
    )[0]
    assert "py_dict_rooted_op(dict, key, value, 2" in c_public_set
    assert "py_dict_lookup(" not in c_public_set
    assert "pcc_gc_store_ptr(" not in c_public_set

    py_source = (RUNTIME_DIR / "py" / "py_dict.py").read_text(encoding="utf-8")
    py_insert = py_source.split("def _dict_insert_rooted_slot(", 1)[1].split(
        "def _dict_replace_value_rooted_slot(", 1
    )[0]
    key_plan = py_insert.index("pcc_gc_store_ptr_plan_init(key_plan")
    value_plan = py_insert.index("pcc_gc_store_ptr_plan_init(value_plan")
    lock = py_insert.index("pcc_py_gc_minor_graph_lock()", value_plan)
    key_commit = py_insert.index("pcc_gc_store_ptr_plan_commit_locked(", lock)
    value_commit = py_insert.index(
        "pcc_gc_store_ptr_plan_commit_locked(", key_commit + 1
    )
    index_publish = py_insert.index("store_i64(indices, slot * 8, ei)", value_commit)
    size = py_insert.index("PYDICTOBJECT_ITEM_COUNT_OFFSET, size + 1)", index_publish)
    unlock = py_insert.index("pcc_py_gc_minor_graph_unlock()", size)
    finish = py_insert.index("pcc_gc_store_ptr_plan_finish", unlock)
    assert (
        key_plan < value_plan < lock < key_commit < value_commit
        < index_publish < size < unlock < finish
    )
    assert "py_decref" not in py_insert[lock:unlock]

    py_replace = py_source.split(
        "def _dict_replace_value_rooted_slot(", 1
    )[1].split("def _dict_del_rooted_slot(", 1)[0]
    plan = py_replace.index("pcc_gc_store_ptr_plan_init")
    lock = py_replace.index("pcc_py_gc_minor_graph_lock()", plan)
    commit = py_replace.index("pcc_gc_store_ptr_plan_commit_locked(", lock)
    unlock = py_replace.index("pcc_py_gc_minor_graph_unlock()", commit)
    finish = py_replace.index("pcc_gc_store_ptr_plan_finish", unlock)
    assert plan < lock < commit < unlock < finish
    # Replacement keeps the original stored key: the key slot is never written.
    assert "DICTENTRY_KEY_OFFSET" not in py_replace.split(
        "pcc_py_gc_minor_graph_lock()", 1
    )[1]
    assert "store_ptr(entries, entry_off + DICTENTRY_KEY_OFFSET" not in py_replace
    assert "py_decref" not in py_replace[lock:unlock]


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_backend4_dict_set_survives_callback_relocation_and_commits_before_finalizer(
    tmp_path: Path,
    kind: str,
) -> None:
    """Proposal No.23 dynamic proof.

    A pcc-native ``__hash__`` relocates the dict during a fresh insert; a
    C-extension ``tp_richcompare`` relocates it during a replacement.  The
    replacement keeps the original stored key object, and the displaced
    value's finalizer re-enters the dict and observes the fully committed
    replacement rather than a half-published table.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="backend4_dict_set_callback_commit",
        source_text=r'''
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            typedef struct ProbeKeyObject {
                PyObject_HEAD
            } ProbeKeyObject;

            static PyObject *hash_dict_root;
            static PyObject *eq_dict_root;
            static PyObject *del_dict_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *del_class;
            static int64_t mode;
            static int64_t hash_relocations;
            static int64_t equality_relocations;
            static int64_t del_calls;
            static int64_t del_seen_len = -1;
            static int64_t del_seen_value = -1;
            static int64_t del_seen_error = -1;

            extern int64_t pcc_gc_backend4_relocation_set_add(PyObject *obj);

            static int relocate_dict(PyObject **slot) {
                PyObject *dict = pcc_gc_load_ptr(NULL, slot);
                pcc_gc_reset_relocation_set();
                if (pcc_gc_backend4_relocation_set_add(dict) != 1) return 0;
                PyObject *target = pcc_gc_relocate_copy(dict, 56);
                if (target == NULL || target == dict) return 0;
                py_decref(target);
                return 1;
            }

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                if (mode == 1 && hash_relocations == 0) {
                    if (!relocate_dict(&hash_dict_root)) return NULL;
                    hash_relocations++;
                }
                return py_int_from_i64(7);
            }

            /* Displaced-value finalizer: must observe the committed table. */
            static PyObject *probe_del(PyObject *self) {
                (void)self;
                if (del_calls == 0) {
                    del_calls++;
                    PyObject *dict = pcc_gc_load_ptr(NULL, &del_dict_root);
                    del_seen_len = py_dict_len(dict);
                    PyObject *key = py_dict_entry_key_at(dict, 0);
                    PyObject *seen = key == NULL
                        ? NULL : py_dict_get(dict, key);
                    del_seen_value = seen == NULL
                        ? -1 : py_int_to_i64(seen, NULL);
                    if (seen != NULL) py_decref(seen);
                    if (key != NULL) py_decref(key);
                    del_seen_error = py_err_occurred() != NULL;
                }
                Py_INCREF(Py_None);
                return Py_None;
            }

            static PyObject *probe_eq(
                PyObject *self,
                PyObject *other,
                int op
            ) {
                (void)self;
                (void)other;
                if (op != Py_EQ) {
                    Py_INCREF(Py_NotImplemented);
                    return Py_NotImplemented;
                }
                if (mode == 2 && equality_relocations == 0) {
                    if (!relocate_dict(&eq_dict_root)) return NULL;
                    equality_relocations++;
                }
                Py_INCREF(Py_True);
                return Py_True;
            }

            static PyTypeObject ProbeKeyType = {
                PyVarObject_HEAD_INIT(NULL, 0)
                .tp_name = "pcc_probe.DictSetKey",
                .tp_basicsize = sizeof(ProbeKeyObject),
                .tp_flags = Py_TPFLAGS_DEFAULT,
                .tp_richcompare = probe_eq,
            };

            static PyObject *new_key(void) {
                return (PyObject *)PyObject_New(ProbeKeyObject, &ProbeKeyType);
            }

            int main(void) {
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_COLORED_RELOCATING
                    ) != 0) return 2;
                if (PyType_Ready(&ProbeKeyType) != 0) return 3;

                /* 1. fresh insert whose user __hash__ relocates the dict */
                hash_class = py_class_new(
                    "DictSetHashMovingKey", NULL, 0, NULL, 0
                );
                if (hash_class == NULL) return 4;
                pcc_gc_pin((PyObject *)hash_class);
                py_class_add_method(
                    hash_class,
                    "__hash__",
                    (PyObject *)(uintptr_t)probe_hash
                );
                hash_dict_root = py_dict_new();
                PyObject *hash_key = py_instance_new(hash_class);
                if (hash_dict_root == NULL || hash_key == NULL) return 5;
                void *hash_handle = pcc_gc_scheduler_root_register_handle(
                    &hash_dict_root
                );
                if (hash_handle == NULL) return 6;
                mode = 1;
                py_dict_set(hash_dict_root, hash_key, py_int_from_i64(11));
                hash_dict_root = pcc_gc_load_ptr(NULL, &hash_dict_root);
                mode = 0;
                if (py_dict_len(hash_dict_root) != 1) return 7;
                PyObject *hash_got = py_dict_get(hash_dict_root, hash_key);
                if (hash_got == NULL
                    || py_int_to_i64(hash_got, NULL) != 11) return 8;
                py_decref(hash_got);

                /* 2. replacement whose equality callback relocates the dict;
                 *    the stored key object must survive unchanged. */
                eq_dict_root = py_dict_new();
                PyObject *eq_stored = new_key();
                PyObject *eq_query = new_key();
                if (eq_dict_root == NULL || eq_stored == NULL
                    || eq_query == NULL) return 9;
                py_dict_set(eq_dict_root, eq_stored, py_int_from_i64(11));
                void *eq_handle = pcc_gc_scheduler_root_register_handle(
                    &eq_dict_root
                );
                if (eq_handle == NULL) return 10;
                mode = 2;
                py_dict_set(eq_dict_root, eq_query, py_int_from_i64(22));
                eq_dict_root = pcc_gc_load_ptr(NULL, &eq_dict_root);
                mode = 0;
                if (py_dict_len(eq_dict_root) != 1) return 11;
                PyObject *eq_got = py_dict_get(eq_dict_root, eq_stored);
                if (eq_got == NULL
                    || py_int_to_i64(eq_got, NULL) != 22) return 12;
                py_decref(eq_got);
                PyObject *stored_now = py_dict_entry_key_at(eq_dict_root, 0);
                if (stored_now != eq_stored) {
                    printf("stored key drifted: %p != %p\n",
                           (void *)stored_now, (void *)eq_stored);
                    return 13;
                }
                py_decref(stored_now);

                /* 3. the displaced value's finalizer re-enters the dict */
                del_class = py_class_new("DictDisplacedValue", NULL, 0, NULL, 0);
                if (del_class == NULL) return 14;
                pcc_gc_pin((PyObject *)del_class);
                py_class_add_method(
                    del_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );
                del_dict_root = py_dict_new();
                PyObject *del_key = py_str_new("k", 1);
                PyObject *displaced = py_instance_new(del_class);
                if (del_dict_root == NULL || del_key == NULL
                    || displaced == NULL) return 15;
                void *del_handle = pcc_gc_scheduler_root_register_handle(
                    &del_dict_root
                );
                if (del_handle == NULL) return 16;
                py_dict_set(del_dict_root, del_key, displaced);
                py_decref(displaced);   /* only the dict holds it now */
                del_dict_root = pcc_gc_load_ptr(NULL, &del_dict_root);
                py_dict_set(del_dict_root, del_key, py_int_from_i64(33));
                del_dict_root = pcc_gc_load_ptr(NULL, &del_dict_root);
                if (del_calls != 1
                    || del_seen_len != 1
                    || del_seen_value != 33
                    || del_seen_error != 0) {
                    printf(
                        "finalizer view: calls=%lld len=%lld value=%lld "
                        "error=%lld\n",
                        (long long)del_calls,
                        (long long)del_seen_len,
                        (long long)del_seen_value,
                        (long long)del_seen_error
                    );
                    return 17;
                }
                if (hash_relocations != 1 || equality_relocations != 1) {
                    printf("relocations: hash=%lld eq=%lld\n",
                           (long long)hash_relocations,
                           (long long)equality_relocations);
                    return 18;
                }
                if (pcc_gc_scheduler_root_count() != 3) return 19;

                py_decref(hash_key);
                Py_DECREF(eq_stored);
                Py_DECREF(eq_query);
                py_decref(del_key);
                pcc_gc_scheduler_root_unregister_handle(hash_handle);
                pcc_gc_scheduler_root_unregister_handle(eq_handle);
                pcc_gc_scheduler_root_unregister_handle(del_handle);
                py_decref(pcc_gc_load_ptr(NULL, &hash_dict_root));
                py_decref(pcc_gc_load_ptr(NULL, &eq_dict_root));
                py_decref(pcc_gc_load_ptr(NULL, &del_dict_root));
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)del_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)del_class);
                return 0;
            }
        ''',
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, (
        f"{kind} dict set callback commit returned {run.returncode}: "
        + run.stdout + run.stderr
    )


def test_dict_del_commits_tombstone_and_size_before_releasing_key_and_value():
    """Proposal No.24: dict delete publishes key->NULL, value->NULL, the index
    tombstone and the decremented size inside one graph-locked transaction, and
    releases the detached key and value only after the lock is dropped."""
    c_source = (RUNTIME_DIR / "src" / "py_dict.c").read_text(encoding="utf-8")
    c_del = c_source.split("static int py_dict_del_rooted_slot(", 1)[1].split(
        "/* mode 0: get, returning an owned value.", 1
    )[0]
    key_plan = c_del.index("pcc_gc_store_ptr_plan_init(&key_plan")
    value_plan = c_del.index("pcc_gc_store_ptr_plan_init(&value_plan")
    lock = c_del.index("pcc_gc_root_slot_lock()", value_plan)
    key_commit = c_del.index("&key_plan, dict, &entry->key, NULL", lock)
    value_commit = c_del.index("&value_plan, dict, &entry->value, NULL", key_commit)
    tombstone = c_del.index("indices[slot] = PY_DICT_TOMBSTONE", value_commit)
    size = c_del.index("d->size--", tombstone)
    unlock = c_del.index("pcc_gc_root_slot_unlock()", size)
    key_finish = c_del.index("pcc_gc_store_ptr_plan_finish(&key_plan)", unlock)
    value_finish = c_del.index("pcc_gc_store_ptr_plan_finish(&value_plan)", unlock)
    assert (
        key_plan < value_plan < lock < key_commit < value_commit
        < tombstone < size < unlock < key_finish < value_finish
    )
    # A release inside the locked transaction would let a finalizer see a freed
    # key behind a still-live index — the exact defect this slice removes.
    assert "py_decref" not in c_del

    c_public_del = c_source.split("int64_t py_dict_del(", 1)[1].split(
        "\nvoid py_dict_clear(", 1
    )[0]
    assert "py_dict_rooted_op(dict, key, NULL, 1, &status)" in c_public_del
    assert "py_decref" not in c_public_del
    # The legacy raw probe is gone from both mirrors, not merely bypassed.
    assert "py_dict_lookup" not in c_source

    py_source = (RUNTIME_DIR / "py" / "py_dict.py").read_text(encoding="utf-8")
    py_del = py_source.split("def _dict_del_rooted_slot(", 1)[1].split(
        "def _dict_rooted_op(", 1
    )[0]
    key_plan = py_del.index("pcc_gc_store_ptr_plan_init(key_plan")
    value_plan = py_del.index("pcc_gc_store_ptr_plan_init(value_plan")
    lock = py_del.index("pcc_py_gc_minor_graph_lock()", value_plan)
    key_commit = py_del.index("pcc_gc_store_ptr_plan_commit_locked(", lock)
    value_commit = py_del.index(
        "pcc_gc_store_ptr_plan_commit_locked(", key_commit + 1
    )
    tombstone = py_del.index("store_i64(indices, slot * 8, -2)", value_commit)
    size = py_del.index("PYDICTOBJECT_ITEM_COUNT_OFFSET, size - 1)", tombstone)
    unlock = py_del.index("pcc_py_gc_minor_graph_unlock()", size)
    finish = py_del.index("pcc_gc_store_ptr_plan_finish", unlock)
    assert (
        key_plan < value_plan < lock < key_commit < value_commit
        < tombstone < size < unlock < finish
    )
    assert "py_decref" not in py_del

    py_public_del = py_source.split('def py_dict_del(d, key) -> int:', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]
    assert "_dict_rooted_op(d, key, null(), 1, status)" in py_public_del
    assert "py_decref" not in py_public_del
    assert "_lookup(" not in py_source


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_backend4_dict_del_relocates_then_finalizer_observes_committed_absence(
    tmp_path: Path,
    kind: str,
) -> None:
    """Proposal No.24 dynamic proof.

    A C-extension ``tp_richcompare`` relocates the dict during the delete
    probe, and the detached value's pcc-native ``__del__`` re-enters the dict:
    it must observe length 0 and the key already absent, proving the release
    ran against the committed table rather than a half-detached entry.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="backend4_dict_del_split_commit",
        source_text=(
            r'''
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            typedef struct ProbeKeyObject {
                PyObject_HEAD
            } ProbeKeyObject;

            static PyObject *del_dict_root;
            static struct PyClassObject *value_class;
            static int64_t mode;
            static int64_t equality_relocations;
            static int64_t del_calls;
            static int64_t del_seen_len = -1;
            static int64_t del_seen_used = -1;
            static int64_t del_seen_key = -1;
            static int64_t del_seen_error = -1;

            extern int64_t pcc_gc_backend4_relocation_set_add(PyObject *obj);
            extern int64_t pcc_gc_backend4_remap_and_retire_stopped_world(void);

            static int relocate_dict(PyObject **slot) {
                PyObject *dict = pcc_gc_load_ptr(NULL, slot);
                pcc_gc_reset_relocation_set();
                if (pcc_gc_backend4_relocation_set_add(dict) != 1) return 0;
                PyObject *target = pcc_gc_relocate_copy(dict, 56);
                if (target == NULL || target == dict) return 0;
                py_decref(target);
                return 1;
            }

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                if (del_calls == 0) {
                    del_calls++;
                    PyObject *dict = pcc_gc_load_ptr(NULL, &del_dict_root);
                    del_seen_len = py_dict_len(dict);
                    del_seen_used = py_dict_entries_used(dict);
                    PyObject *key = py_dict_entry_key_at(dict, 0);
                    del_seen_key = key == NULL ? 0 : 1;
                    if (key != NULL) py_decref(key);
                    del_seen_error = py_err_occurred() != NULL;
                }
                Py_INCREF(Py_None);
                return Py_None;
            }

            static PyObject *probe_eq(
                PyObject *self,
                PyObject *other,
                int op
            ) {
                (void)self;
                (void)other;
                if (op != Py_EQ) {
                    Py_INCREF(Py_NotImplemented);
                    return Py_NotImplemented;
                }
                if (mode == 1 && equality_relocations == 0) {
                    if (!relocate_dict(&del_dict_root)) return NULL;
                    equality_relocations++;
                }
                Py_INCREF(Py_True);
                return Py_True;
            }

            static PyTypeObject ProbeKeyType = {
                PyVarObject_HEAD_INIT(NULL, 0)
                .tp_name = "pcc_probe.DictDelKey",
                .tp_basicsize = sizeof(ProbeKeyObject),
                .tp_flags = Py_TPFLAGS_DEFAULT,
                .tp_richcompare = probe_eq,
            };

            static PyObject *new_key(void) {
                return (PyObject *)PyObject_New(ProbeKeyObject, &ProbeKeyType);
            }

            int main(void) {
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_COLORED_RELOCATING
                    ) != 0) return 2;
                if (PyType_Ready(&ProbeKeyType) != 0) return 3;

                value_class = py_class_new(
                    "DictDeletedValue", NULL, 0, NULL, 0
                );
                if (value_class == NULL) return 4;
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    value_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                del_dict_root = py_dict_new();
                PyObject *stored = new_key();
                PyObject *query = new_key();
                PyObject *value = py_instance_new(value_class);
                if (del_dict_root == NULL || stored == NULL
                    || query == NULL || value == NULL) return 5;
                py_dict_set(del_dict_root, stored, value);
                py_decref(value);   /* only the dict holds it now */
                void *handle = pcc_gc_scheduler_root_register_handle(
                    &del_dict_root
                );
                if (handle == NULL) return 6;
                if (py_dict_len(del_dict_root) != 1) return 7;

                mode = 1;
                if (py_dict_del(del_dict_root, query) != 0) return 8;
                del_dict_root = pcc_gc_load_ptr(NULL, &del_dict_root);
                mode = 0;

                if (py_dict_len(del_dict_root) != 0) return 9;
                if (py_dict_contains(del_dict_root, stored)) return 10;
                if (equality_relocations != 1) {
                    printf("relocations=%lld\n",
                           (long long)equality_relocations);
                    return 11;
                }
                /* The detached value was released while the dict carried a
                 * forwarding shell, so its finalizer runs on a retirement
                 * epoch rather than inline.  Two epochs match the proven set
                 * remove precedent. */
                pcc_gc_reset_relocation_set();
                if (pcc_gc_backend4_remap_and_retire_stopped_world() <= 0) {
                    return 15;
                }
                if (pcc_gc_backend4_remap_and_retire_stopped_world() <= 0) {
                    return 16;
                }
                if (del_calls != 1
                    || del_seen_len != 0
                    || del_seen_key != 0
                    || del_seen_error != 0) {
                    printf(
                        "finalizer view: calls=%lld len=%lld used=%lld "
                        "key=%lld error=%lld\n",
                        (long long)del_calls,
                        (long long)del_seen_len,
                        (long long)del_seen_used,
                        (long long)del_seen_key,
                        (long long)del_seen_error
                    );
                    return 12;
                }
                /* deleting an absent key must still report -1 */
                if (py_dict_del(del_dict_root, query) != -1) return 13;
                if (pcc_gc_scheduler_root_count() != 1) return 14;

                Py_DECREF(stored);
                Py_DECREF(query);
                pcc_gc_scheduler_root_unregister_handle(handle);
                py_decref(pcc_gc_load_ptr(NULL, &del_dict_root));
                pcc_gc_unpin((PyObject *)value_class);
                py_decref((PyObject *)value_class);
                return 0;
            }
        '''),
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, (
        f"{kind} dict del split commit returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_dict_raising_equality_leaves_the_dict_unmodified(
    tmp_path: Path,
    kind: str,
) -> None:
    """A raising ``__eq__`` during a dict probe must abort the operation.

    ``py_obj_eq`` returns 0 and sets the thread-local exception when the user
    comparison raises.  Without a post-call ``py_err_occurred()`` check the
    probe reads that 0 as "not equal", keeps probing, and in set mode inserts a
    new entry -- so ``d[k] = v`` raises *and* mutates the dict, which Python
    does not allow.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="dict_raising_equality_abort",
        source_text=r'''
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            typedef struct ProbeKeyObject {
                PyObject_HEAD
            } ProbeKeyObject;

            extern void *py_tls_exc_get(void);
            extern PyObject *py_dict_pop(PyObject *d, PyObject *key);

            static int64_t eq_calls;

            /* Same hash for every probe key, so the two keys collide and the
             * stored key's comparison is reached. */
            static Py_hash_t probe_hash(PyObject *self) {
                (void)self;
                return 7;
            }

            static PyObject *probe_eq(PyObject *self, PyObject *other, int op) {
                (void)self;
                (void)other;
                if (op != Py_EQ) {
                    Py_INCREF(Py_NotImplemented);
                    return Py_NotImplemented;
                }
                eq_calls++;
                PyErr_SetString(PyExc_ValueError, "comparison exploded");
                return NULL;
            }

            static PyTypeObject ProbeKeyType = {
                PyVarObject_HEAD_INIT(NULL, 0)
                .tp_name = "pcc_probe.RaisingEqKey",
                .tp_basicsize = sizeof(ProbeKeyObject),
                .tp_flags = Py_TPFLAGS_DEFAULT,
                .tp_hash = probe_hash,
                .tp_richcompare = probe_eq,
            };

            static PyObject *new_key(void) {
                return (PyObject *)PyObject_New(ProbeKeyObject, &ProbeKeyType);
            }

            int main(void) {
                if (PyType_Ready(&ProbeKeyType) != 0) return 2;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                PyObject *d = py_dict_new();
                PyObject *stored = new_key();
                PyObject *query = new_key();
                PyObject *v1 = py_int_from_i64(11);
                PyObject *v2 = py_int_from_i64(22);
                if (d == NULL || stored == NULL || query == NULL
                    || v1 == NULL || v2 == NULL) return 3;

                py_dict_set(d, stored, v1);
                if (py_dict_len(d) != 1) return 4;
                if (py_err_occurred()) return 5;

                /* The colliding insert must reach the stored key's __eq__. */
                eq_calls = 0;
                py_clear_exception();
                py_dict_set(d, query, v2);

                if (eq_calls < 1) {
                    printf("equality never ran: eq_calls=%lld\n",
                           (long long)eq_calls);
                    return 6;
                }
                if (!py_err_occurred()) {
                    printf("exception did not propagate\n");
                    return 7;
                }
                if (py_dict_len(d) != 1) {
                    printf("dict mutated by a failed set: len=%lld\n",
                           (long long)py_dict_len(d));
                    return 8;
                }
                py_clear_exception();

                /* The original mapping must be intact and reachable. */
                PyObject *got = py_dict_get(d, stored);
                py_clear_exception();
                if (got == NULL || py_int_to_i64(got, NULL) != 11) {
                    printf("original entry damaged\n");
                    return 9;
                }
                py_decref(got);
                py_clear_exception();

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("scheduler roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 10;
                }
                return 0;
            }
        ''',
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, (
        f"{kind} dict raising equality returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", [
    "PCC_GC_KIND_REFCOUNT_CYCLE",
    "PCC_GC_KIND_COLORED_RELOCATING",
])
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_user_instance_raising_eq_aborts_set_list_and_tuple_operations(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """Every py_obj_eq consumer must preserve a user equality exception."""
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="raising_instance_eq_consumers_" + gc_kind.lower(),
        source_text=r'''
            #include "py_internal.h"

            static struct PyClassObject *key_class;
            static int64_t eq_calls;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                return py_int_from_i64(7);
            }

            static PyObject *probe_eq(PyObject *self, PyObject *other) {
                (void)self; (void)other;
                eq_calls++;
                py_raise_owned(py_exc_new(PY_EXC_RUNTIMEERROR, "eq boom"));
                py_incref(py_False);
                return py_False;
            }

            static PyObject *probe_callable(
                PyObject *captures, PyObject *args
            ) {
                (void)args;
                return py_tuple_get(captures, 0);
            }

            extern int PyObject_RichCompareBool(
                PyObject *, PyObject *, int
            );

            static int is_runtime_error(void) {
                PyObject *exc = py_current_exception();
                struct PyClassObject *cls =
                    py_exc_builtin_class(PY_EXC_RUNTIMEERROR);
                return exc != NULL &&
                    py_exc_matches(exc, (PyObject *)cls);
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;
                key_class = py_class_new("RaisingEqKey", NULL, 0, NULL, 0);
                if (key_class == NULL) return 3;
                pcc_gc_pin((PyObject *)key_class);
                py_class_add_method(
                    key_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    key_class, "__eq__", (PyObject *)(uintptr_t)probe_eq
                );
                PyObject *stored = py_instance_new(key_class);
                PyObject *query = py_instance_new(key_class);
                if (stored == NULL || query == NULL) return 4;

                PyObject *set = py_set_new();
                py_set_add(set, stored);
                py_set_add(set, query);
                if (!is_runtime_error() || py_set_len(set) != 1) return 10;
                py_clear_exception();

                PyObject *list = py_list_new(0);
                py_list_append(list, stored);
                if (py_list_contains(list, query) != 0 || !is_runtime_error()) {
                    return 11;
                }
                py_clear_exception();
                py_list_remove(list, query);
                if (!is_runtime_error() || py_list_len(list) != 1) return 12;
                py_clear_exception();

                PyObject *tuple = py_tuple_new(1);
                py_tuple_set_item(tuple, 0, stored);
                if (py_tuple_index(tuple, query) != -1 || !is_runtime_error()) {
                    return 13;
                }
                py_clear_exception();

                if (
                    PyObject_RichCompareBool(stored, query, 2) != -1
                    || !is_runtime_error()
                ) return 14;
                py_clear_exception();

                PyObject *captures = py_tuple_new(1);
                py_tuple_set_item(captures, 0, query);
                PyObject *callable = py_func_new(
                    (void *)(uintptr_t)probe_callable, captures
                );
                PyObject *iterator = py_iter_callable_new(callable, stored);
                if (callable == NULL || iterator == NULL) return 15;
                PyObject *next = py_obj_next(iterator);
                if (next != NULL || !is_runtime_error()) return 16;
                py_clear_exception();

                int64_t before_callback_eq = eq_calls;
                py_gc_callbacks_append(stored);
                py_gc_callbacks_remove(query);
                if (eq_calls == before_callback_eq) return 170;
                if (!is_runtime_error()) return 171;
                py_clear_exception();
                PyObject *callbacks = py_gc_callbacks_list();
                if (callbacks == NULL) return 172;
                if (py_list_len(callbacks) != 1) return 173;
                py_gc_callbacks_remove(stored);
                if (py_list_len(callbacks) != 0) return 18;
                if (eq_calls < 7) return 19;

                py_decref(callbacks);
                py_decref(iterator);
                py_decref(callable);
                py_decref(captures);
                py_decref(stored);
                py_decref(query);
                py_decref(set);
                py_decref(list);
                py_decref(tuple);
                pcc_gc_unpin((PyObject *)key_class);
                py_decref((PyObject *)key_class);
                return 0;
            }
        '''.replace("__GC_KIND__", gc_kind),
        extra_sources=(
            (RUNTIME_DIR / "src" / "py_tuple_methods.c",)
            if kind == "c"
            else ()
        ),
        extra_compile_args=(
            ("-Doffsetof(t,m)=__builtin_offsetof(t,m)",)
            if kind == "c"
            else ()
        ),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} raising instance equality returned "
        f"{run.returncode}: " + run.stdout + run.stderr
    )


def test_generational_promotion_declines_containers_so_backend4_probes_suffice() -> None:
    """Only backend 4 moves containers, which bounds the probe obligation.

    Every dict/set callback-relocation probe pins ``COLORED_RELOCATING``, and
    that looked like a coverage gap: backend 3 also moves objects, via
    ``pcc_gc_generational_oldify_copy`` with eager slot rewrite rather than a
    read barrier, so a cached ``PyDictObject *`` held across a user callback
    would be stale there for a different reason.

    It is not a gap.  Backend 3's oldify gates on
    ``pcc_gc_relocate_copy_supported_tag``, which lists only the pointer-free
    payload tags.  Containers are absent, so generational promotion declines
    them outright and the stale-owner class cannot arise.  Backend 4 uses
    ``pcc_gc_colored_relocate_copy_supported_tag``, which adds the containers.

    Backends 0, 1 and 2 do not relocate at all, so backend 4 is the only
    collector that can move a dict or set.  This test exists so that the day
    a container tag is added to the generational list, it fails and names the
    obligation that comes with it, rather than the coverage silently becoming
    incomplete.
    """
    src = PY_GC_BACKEND_C.read_text(encoding="utf-8")
    plain = src.split(
        "static int pcc_gc_relocate_copy_supported_tag(int32_t tag) {", 1
    )[1].split("\n}", 1)[0]
    colored = src.split(
        "static int pcc_gc_colored_relocate_copy_supported_tag(int32_t tag) {",
        1,
    )[1].split("\n}", 1)[0]

    containers = ("PY_TYPE_DICT", "PY_TYPE_SET", "PY_TYPE_LIST", "PY_TYPE_TUPLE")
    for tag in containers:
        assert tag not in plain, (
            f"{tag} is now relocatable under generational promotion. "
            "Backend 3 can therefore move a container across a user "
            "hash/equality callback, and the rooted callback-restart contract "
            "needs a PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR probe of its own -- "
            "the existing dict/set probes all pin COLORED_RELOCATING and will "
            "not cover it."
        )
        assert tag in colored, f"{tag} left the colored relocation set"

    # The generational list must stay pointer-free payloads only: a tag with
    # pcc pointer slots would need slot fixups that a shallow copy does not do.
    assert "PY_TYPE_INT" in plain and "PY_TYPE_STR" in plain
    assert "pcc_gc_relocate_copy_supported_tag(tag)" in colored, (
        "colored support must still fall through to the plain set"
    )

    # The strict mirror gates on the same set.  A drift here would let the
    # pcc-Python runtime move a container that the C runtime refuses to move,
    # which is the mirror-drift class this repo keeps rediscovering.
    strict = (
        RUNTIME_DIR / "py" / "freestanding_gc_generational_oldification.py"
    ).read_text(encoding="utf-8")
    strict_gate = strict.split(
        '@c_abi_export("pcc_gc_generational_oldify_supported_tag")', 1
    )[1].split("\n@c_abi_export", 1)[0]
    for tag in containers:
        assert tag not in strict_gate, (
            f"{tag} is relocatable in the strict generational mirror but not "
            "in C -- the mirrors have drifted"
        )
    for tag in ("PY_TYPE_INT", "PY_TYPE_FLOAT", "PY_TYPE_STR",
                "PY_TYPE_COMPLEX", "PY_TYPE_BYTES", "PY_TYPE_BYTEARRAY",
                "PY_TYPE_CPY_HANDLE"):
        assert tag in strict_gate, f"{tag} missing from the strict gate"
        assert tag in plain, f"{tag} missing from the C gate"
