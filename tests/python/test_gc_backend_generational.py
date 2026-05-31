from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


def _compile_probe(
    tmp_path,
    source: str,
    *,
    runtime_cc: str | None = None,
    runtime_high: str | None = None,
    backend: str | None = None,
    ir_scaffold_mode: str | None = "on",
):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "probe.py"
    exe = tmp_path / "probe.out"
    src.write_text(textwrap.dedent(source).lstrip())
    env = {}
    if runtime_cc is not None:
        env["PCC_RUNTIME_CC"] = runtime_cc
    if runtime_high is not None:
        env["PCC_RUNTIME_HIGH"] = runtime_high
    with mock.patch.dict(os.environ, env, clear=False):
        compile_python(
            str(src),
            str(exe),
            ir_scaffold_mode=ir_scaffold_mode,
            libpython_mode="off",
            backend=backend,
        )
    return exe


def _cc() -> str:
    return os.environ.get("CC", "cc")


def _build_runtime(tmp_path: Path) -> Path:
    work_runtime = tmp_path / "py_runtime"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "build", "build_pcc", "build_py", "build_libpython", "*.a"
        ),
    )
    result = subprocess.run(
        ["make", "-B", "-C", str(work_runtime), "libpy_runtime.a"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work_runtime


def _build_threaded_runtime(tmp_path: Path) -> Path:
    work_runtime = tmp_path / "py_runtime_threads"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "build", "build_pcc", "build_py", "build_libpython", "*.a"
        ),
    )
    result = subprocess.run(
        ["make", "-B", "-C", str(work_runtime), "PCC_WITH_THREADS=1", "libpy_runtime.a"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work_runtime


def _build_pcc_py_runtime(tmp_path: Path) -> Path:
    work_runtime = tmp_path / "py_runtime_pcc_py"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "build", "build_pcc", "build_py", "build_libpython", "*.a"
        ),
    )
    result = subprocess.run(
        [
            "make",
            "-B",
            "-C",
            str(work_runtime),
            f"PCC={REPO_ROOT / '.venv' / 'bin' / 'pcc'}",
            f"PYTHON={REPO_ROOT / '.venv' / 'bin' / 'python3'}",
            f"PCC_REPO_ROOT={REPO_ROOT}",
            "PCC_WITH_THREADS=1",
            "libpy_runtime_pcc_py.a",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work_runtime


def _run_backend_three(exe):
    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_BACKEND": "3",
            "PCC_GC_MINOR_HEAP_SIZE": "1024",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    return subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def _assert_backend_three_non_list_slots_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_non_list_slot_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_non_list_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = py_str_new("filler", 6);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            static int check_tuple_slot(void) {
                PyObject *owner = py_tuple_new(1);
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("tuple-child", 11);
                if (child == 0) return 0;
                py_tuple_set_item(owner, 0, child);
                force_refill();

                return forwarded_slot_matches(
                    child, ((PyTupleObject *)owner)->items[0]
                );
            }

            static int check_dict_value_slot(void) {
                PyObject *owner = py_dict_new();
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("dict-child", 10);
                if (child == 0) return 0;
                py_dict_set(owner, py_tag_int(7), child);
                force_refill();

                PyDictObject *d = (PyDictObject *)owner;
                PyObject *slot = 0;
                for (int64_t i = 0; i < d->entries_used; i++) {
                    if (d->entries[i].key != 0) {
                        slot = d->entries[i].value;
                        break;
                    }
                }
                return forwarded_slot_matches(child, slot);
            }

            static int check_set_key_slot(void) {
                PyObject *owner = py_set_new();
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("set-child", 9);
                if (child == 0) return 0;
                py_set_add(owner, child);
                force_refill();

                PySetObject *s = (PySetObject *)owner;
                PyObject *slot = 0;
                for (int64_t i = 0; i < s->capacity; i++) {
                    PyObject *key = s->entries[i].key;
                    if (key != 0 && key != py_set_dummy) {
                        slot = key;
                        break;
                    }
                }
                return forwarded_slot_matches(child, slot);
            }

            static int check_instance_field_slot(void) {
                PyClassObject *cls = (PyClassObject *)pcc_gc_alloc(
                    sizeof(PyClassObject), PY_TYPE_CLASS, 0
                );
                if (cls == 0) return 0;
                memset((char *)cls + sizeof(PyObjectHeader), 0,
                       sizeof(PyClassObject) - sizeof(PyObjectHeader));
                cls->n_fields = 1;

                PyInstanceObject *owner = (PyInstanceObject *)pcc_gc_alloc(
                    sizeof(PyInstanceObject) + sizeof(PyObject *),
                    PY_TYPE_INSTANCE,
                    0
                );
                if (owner == 0) return 0;
                owner->cls = cls;
                owner->fields[0] = 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("field-child", 11);
                if (child == 0) return 0;
                pcc_gc_store_ptr((PyObject *)owner, &owner->fields[0], child);
                force_refill();

                return forwarded_slot_matches(child, owner->fields[0]);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                printf("%d\\n", check_tuple_slot());
                printf("%d\\n", check_dict_value_slot());
                printf("%d\\n", check_set_key_slot());
                printf("%d\\n", check_instance_field_slot());
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1"]


def _assert_backend_three_frame_root_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_frame_root_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_frame_root_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                int32_t frame_map[1] = {1};
                PyObject *slots[1] = {0};
                PyObject *child = py_str_new("root-child", 10);
                if (child == 0) return 3;
                pcc_gc_store_root(&slots[0], child);
                pcc_gc_frame_enter(frame_map, slots);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", slots[0] == forwarded ? 1 : 0);
                printf("%d\\n", slots[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_frame_leave(slots);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_suspended_generator_frame_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_gen_frame_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_gen_frame_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static PyObject *dummy_resume(PyObject *gen, PyObject *frame) {
                (void)gen;
                (void)frame;
                py_incref(py_None);
                return py_None;
            }

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *frame = py_list_new(1);
                if (frame == 0) return 3;
                py_list_append(frame, py_None);

                PyObject *gen = py_gen_new((void *)dummy_resume, frame);
                if (gen == 0) return 4;

                PyListObject *frame_list = (PyListObject *)frame;
                PyGenObject *gen_obj = (PyGenObject *)gen;
                pcc_gc_release(frame);

                (void)pcc_gc_step(8);
                printf("%d\\n", gen_obj->frame == (PyObject *)frame_list ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)frame_list)->flags & PY_FLAG_GC_OLD ? 1 : 0);

                PyObject *child = py_str_new("suspended-child", 15);
                if (child == 0) return 5;
                py_list_set((PyObject *)frame_list, 0, child);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", frame_list->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", frame_list->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                py_list_set((PyObject *)frame_list, 0, py_None);
                pcc_gc_release(child);
                pcc_gc_release(gen);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "0"]


def _assert_backend_three_generator_coroutine_state_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_gen_coro_state_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_gen_coro_state_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                const char *name;
                PyNativeFuncEntry entry;
                PyObject *captures;
                PyObject *args;
                PyObject *result;
                int32_t closed;
                int32_t done;
            } ProbeCoroutineObject;

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static PyObject *dummy_gen_resume(PyObject *gen, PyObject *frame) {
                (void)gen;
                (void)frame;
                py_incref(py_None);
                return py_None;
            }

            static PyObject *coro_entry(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                return py_str_new("coro-result", 11);
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            static int check_generator_send_slot(void) {
                PyObject *frame = py_list_new(0);
                if (frame == 0) return 0;
                PyObject *gen = py_gen_new((void *)dummy_gen_resume, frame);
                if (gen == 0) return 0;
                pcc_gc_release(frame);

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)gen)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 0;
                }

                PyObject *child = py_str_new("gen-send", 8);
                if (child == 0) return 0;
                py_gen_set_state(gen, 1);
                PyObject *result = py_gen_send(gen, child);
                if (result == 0) return 0;
                pcc_gc_release(result);
                force_refill();

                PyGenObject *g = (PyGenObject *)gen;
                int ok = forwarded_slot_matches(child, g->send_value);
                pcc_gc_release(child);
                pcc_gc_release(gen);
                return ok;
            }

            static int check_coroutine_result_slot(void) {
                PyObject *coro = py_coroutine_new_native(
                    "probe", (void *)coro_entry, 0, 0
                );
                if (coro == 0) return 0;

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)coro)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 0;
                }

                PyObject *result = py_coroutine_run(coro);
                if (result == 0) return 0;
                PyObject *child = result;
                py_incref(child);
                pcc_gc_release(result);
                force_refill();

                ProbeCoroutineObject *c = (ProbeCoroutineObject *)coro;
                int ok = forwarded_slot_matches(child, c->result);
                pcc_gc_release(child);
                pcc_gc_release(coro);
                return ok;
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                printf("%d\\n", check_generator_send_slot());
                printf("%d\\n", check_coroutine_result_slot());
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1"]


def _assert_backend_three_task_state_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_task_state_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_task_state_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *coro = py_coroutine_new_native("task-coro", 0, 0, 0);
                if (coro == 0) return 3;
                PyObject *task = py_task_new(coro);
                if (task == 0) return 4;
                pcc_gc_release(coro);

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)task)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 5;
                }

                PyObject *result = py_str_new("task-result", 11);
                PyObject *waiter = py_str_new("task-waiter", 11);
                if (result == 0 || waiter == 0) return 6;
                if (
                    ((((PyObjectHeader *)result)->flags & PY_FLAG_GC_YOUNG) == 0)
                    || ((((PyObjectHeader *)waiter)->flags & PY_FLAG_GC_YOUNG) == 0)
                ) {
                    return 7;
                }
                py_task_set_result(task, result);
                py_task_set_waiter(task, waiter);
                if ((((PyObjectHeader *)task)->flags & PY_FLAG_GC_REMEMBERED) == 0) {
                    return 8;
                }

                force_refill();

                PyTaskObject *t = (PyTaskObject *)task;
                printf("%d\\n", forwarded_slot_matches(result, t->result));
                printf("%d\\n", forwarded_slot_matches(waiter, t->waiter));

                py_task_set_result(task, py_None);
                py_task_set_waiter(task, 0);
                pcc_gc_release(result);
                pcc_gc_release(waiter);
                pcc_gc_release(task);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1"]


def _assert_backend_three_scheduler_root_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_scheduler_root_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_scheduler_root_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *slot = 0;
                pcc_gc_scheduler_root_register(&slot);

                PyObject *child = py_str_new("scheduler-root", 14);
                if (child == 0) return 3;
                pcc_gc_store_root(&slot, child);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", slot == forwarded ? 1 : 0);
                printf("%d\\n", slot != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_scheduler_root_unregister(&slot);
                pcc_gc_store_root(&slot, 0);
                pcc_gc_release(child);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_scheduler_queue_entry_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_scheduler_queue_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_scheduler_queue_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PccGcSchedulerQueue *queue = pcc_gc_scheduler_queue_new();
                if (queue == 0) return 3;

                PyObject *child = py_str_new("queued-task", 11);
                if (child == 0) return 4;
                if (pcc_gc_scheduler_queue_push(queue, child) != 0) return 5;
                printf("%d\\n", pcc_gc_scheduler_queue_len(queue) == 1 ? 1 : 0);

                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);

                PyObject *out = 0;
                pcc_gc_scheduler_root_register(&out);
                printf("%d\\n", pcc_gc_scheduler_queue_pop_into(queue, &out) == 1 ? 1 : 0);
                printf("%d\\n", out == forwarded ? 1 : 0);
                printf("%d\\n", pcc_gc_scheduler_queue_len(queue) == 0 ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 0 : 1);

                pcc_gc_scheduler_root_unregister(&out);
                pcc_gc_store_root(&out, 0);
                pcc_gc_scheduler_queue_free(queue);
                pcc_gc_release(child);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "1"]


def _assert_backend_three_class_metadata_slots_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_class_metadata_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_class_metadata_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyClassObject *cls = py_class_new("C", 0, 0, 0, 0);
                if (cls == 0) return 3;
                (void)pcc_gc_step(1);

                PyObject *method_child = py_str_new("method-child", 12);
                PyObject *del_child = py_str_new("del-child", 9);
                if (method_child == 0 || del_child == 0) return 4;

                py_class_add_method(cls, "m", method_child);
                py_class_add_method(cls, "__del__", del_child);
                force_refill();

                printf("%d\\n", cls->n_methods == 2 ? 1 : 0);
                printf("%d\\n", forwarded_slot_matches(method_child, cls->methods[0].func));
                printf("%d\\n", forwarded_slot_matches(del_child, cls->methods[1].func));
                printf("%d\\n", forwarded_slot_matches(del_child, cls->del_method));
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1"]


def _assert_backend_three_forwarded_minor_source_cleanup(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_forwarded_minor_cleanup_probe.c"
    exe = tmp_path / f"{archive_name}_forwarded_minor_cleanup_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;
                pcc_gc_pin((PyObject *)owner);

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);

                PyObject *replacement = pcc_gc_alloc(256, PY_TYPE_INT, 0);
                if (replacement == 0) return 6;
                int64_t replace_rc = pcc_gc_install_forwarding(child, replacement);
                printf("%d\\n", replace_rc != 0 ? 1 : 0);
                printf("%d\\n", pcc_gc_note_relocation_read(child) == forwarded ? 1 : 0);
                printf(
                    "%d\\n",
                    pcc_gc_load_ptr((PyObject *)owner, &owner->items[0]) == forwarded
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_unpin((PyObject *)owner);
                pcc_gc_release(child);
                pcc_gc_release(replacement);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1"]


def _assert_backend_three_cross_domain_remembered_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_cross_domain_remembered_probe.c"
    exe = tmp_path / f"{archive_name}_cross_domain_remembered_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            typedef struct {
                PyObject *child;
                int64_t tid;
            } WorkerResult;

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            static void *alloc_child_in_worker(void *arg) {
                WorkerResult *result = (WorkerResult *)arg;
                result->tid = pcc_current_thread_id();
                result->child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                return result->child == 0 ? (void *)(uintptr_t)1 : 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 3;
                }
                int64_t main_tid = pcc_current_thread_id();
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 4;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 5;
                pcc_gc_pin((PyObject *)owner);
                (void)pcc_gc_step(1);

                WorkerResult worker = {0, 0};
                PccThreadHandle *thread = 0;
                void *thread_result = 0;
                if (pcc_thread_start(&thread, alloc_child_in_worker, &worker) != 0) {
                    return 6;
                }
                if (pcc_thread_join(thread, &thread_result) != 0) return 7;
                if (thread_result != 0 || worker.child == 0) return 8;
                printf("%d\\n", worker.tid != main_tid ? 1 : 0);

                int32_t child_flags = py_header(worker.child)->flags;
                printf(
                    "%d\\n",
                    (child_flags & PY_FLAG_GC_YOUNG) != 0
                        && (child_flags & PY_FLAG_GC_MINOR_ARENA) != 0
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], worker.child);
                printf(
                    "%d\\n",
                    (py_header((PyObject *)owner)->flags & PY_FLAG_GC_REMEMBERED) != 0
                        ? 1
                        : 0
                );

                printf("%d\\n", pcc_gc_step(64) > 0 ? 1 : 0);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(worker.child);
                printf("%d\\n", forwarded != 0 && forwarded != worker.child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf(
                    "%d\\n",
                    (
                        (py_header(forwarded)->flags & PY_FLAG_GC_OLD) != 0
                        && (py_header(forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0
                    ) ? 1 : 0
                );

                PyObject *replacement = pcc_gc_alloc(256, PY_TYPE_INT, 0);
                if (replacement == 0) return 9;
                printf(
                    "%d\\n",
                    pcc_gc_install_forwarding(worker.child, replacement) != 0 ? 1 : 0
                );
                printf(
                    "%d\\n",
                    pcc_gc_load_ptr((PyObject *)owner, &owner->items[0]) == forwarded
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_unpin((PyObject *)owner);
                pcc_gc_release(worker.child);
                pcc_gc_release(replacement);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    )
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-pthread", "-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "1", "1", "1", "1"]


def test_generational_backend_small_alloc_uses_minor_fast_path(tmp_path):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            print(pcc_gc_backend())
            pcc_gc_telemetry_reset()
            o = pcc_gc_alloc(64, 2, 0)
            print(load_i32(o, 12) & 128)
            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(10))
            pcc_gc_release(o)

        if __name__ == "__main__":
            main()
        """,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["3", "128", "1", "64"]


def test_generational_backend_minor_heap_pressure_triggers_collection(tmp_path):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            i: int = 0
            while i < 40:
                o = pcc_gc_alloc(64, 2, 0)
                i = i + 1
            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(9) > 0)
            print(pcc_gc_telemetry(10) <= 1024)

        if __name__ == "__main__":
            main()
        """,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["40", "True", "True"]


def test_generational_backend_c_runtime_uses_minor_bump_arena(tmp_path):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_arena_probe.c"
    exe = tmp_path / "minor_arena_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            enum {
                PY_FLAG_GC_MINOR_ARENA = 0x1000,
                MINOR_ARENA_REFILLS = 19,
                MINOR_ARENA_BUMPS = 20,
                MINOR_ARENA_FALLBACKS = 21
            };

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *objects[8];
                for (int i = 0; i < 8; i++) {
                    objects[i] = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (objects[i] == 0) return 3;
                }

                PyObjectHeader *h = (PyObjectHeader *)objects[0];
                printf("%d\\n", (h->flags & PY_FLAG_GC_MINOR_ARENA) != 0);
                printf("%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_MINOR_ALLOCATIONS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_BUMPS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_FALLBACKS));

                for (int i = 0; i < 8; i++) {
                    pcc_gc_release(objects[i]);
                }
                return 0;
            }
            """
        ).lstrip()
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines == ["1", "8", "1", "8", "0"]


def test_generational_backend_pcc_python_runtime_uses_minor_bump_arena(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            print(pcc_gc_backend())
            pcc_gc_telemetry_reset()

            o0 = pcc_gc_alloc(64, 2, 0)
            i: int = 1
            while i < 8:
                o = pcc_gc_alloc(64, 2, 0)
                pcc_gc_release(o)
                i = i + 1

            print(load_i32(o0, 12) & 4096)
            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(19))
            print(pcc_gc_telemetry(20))
            print(pcc_gc_telemetry(21))
            pcc_gc_release(o0)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["3", "4096", "8", "1", "8", "0"]


def test_generational_backend_pcc_python_runtime_threaded_minor_blocks(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_thread_blocks.c"
    exe = tmp_path / "pcc_py_minor_thread_blocks.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            enum {
                MINOR_ARENA_REFILLS = 19
            };

            static void *alloc_once(void *arg) {
                (void)arg;
                PyObject *o = pcc_gc_alloc(64, PY_TYPE_NONE, 0);
                if (o == 0) return (void *)(uintptr_t)1;
                pcc_gc_release(o);
                return 0;
            }

            static int run_thread_once(void) {
                PccThreadHandle *t = 0;
                void *result = 0;
                if (pcc_thread_start(&t, alloc_once, 0) != 0) return 1;
                if (pcc_thread_join(t, &result) != 0) return 2;
                return result == 0 ? 0 : 3;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 3;
                }
                pcc_gc_telemetry_reset();

                if (run_thread_once() != 0) return 4;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                if (run_thread_once() != 0) return 5;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) return 6;
                return 0;
            }
            """
        ).lstrip()
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "2"]


def test_generational_backend_pcc_python_runtime_class_instances_deallocate_from_minor_arena(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int64

        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)

        class A:
            pass

        def make() -> None:
            a = A()

        def main() -> None:
            print("backend", pcc_gc_backend())
            i: int = 0
            while i < 100:
                make()
                i = i + 1
            print("ok")

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["backend 3", "ok"]


def test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int64, c_ptr, c_void
        from pcc.unsafe import cstr, load_i32

        py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            s = py_str_new(cstr("abc"), 3)
            flags: int = load_i32(s, 12)
            print(pcc_gc_backend())
            print(flags & 128)
            print(flags & 4096)
            pcc_gc_release(s)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["3", "128", "4096"]


def test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import calloc, load_i32, null, store_i64, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            owner = pcc_gc_alloc(40, 5, 0)
            store_i64(owner, 16, 1)
            store_i64(owner, 24, 1)
            items = calloc(1, 8)
            store_ptr(owner, 32, items)

            pcc_gc_step(1)
            child = pcc_gc_alloc(64, 2, 0)
            pcc_gc_store_ptr(owner, items, child)
            print(load_i32(child, 12) & 128)
            print(load_i32(owner, 12) & 512)

            i: int = 0
            while i < 20:
                pcc_gc_alloc(64, 2, 0)
                i = i + 1

            print(pcc_gc_telemetry(9) > 0)
            print(load_i32(child, 12) & 256)
            print(load_i32(owner, 12) & 512)

            pcc_gc_store_ptr(owner, items, null())
            pcc_gc_release(child)
            pcc_gc_release(owner)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["128", "512", "True", "256", "0"]


def test_generational_backend_minor_refill_promotes_remembered_young_child(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_refill_probe.c"
    exe = tmp_path / "minor_refill_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            enum {
                PY_FLAG_GC_YOUNG = 0x80,
                PY_FLAG_GC_OLD = 0x100,
                PY_FLAG_GC_REMEMBERED = 0x200
            };

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                printf("%d\\n", ((PyObjectHeader *)child)->flags & PY_FLAG_GC_YOUNG ? 1 : 0);
                printf("%d\\n", owner->h.flags & PY_FLAG_GC_REMEMBERED ? 1 : 0);

                PyObject *fillers[3];
                for (int i = 0; i < 3; i++) {
                    fillers[i] = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (fillers[i] == 0) return 6;
                }

                printf("%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_MINOR_COLLECTIONS));
                printf("%d\\n", ((PyObjectHeader *)child)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", owner->h.flags & PY_FLAG_GC_REMEMBERED ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)fillers[2])->flags & PY_FLAG_GC_YOUNG ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                for (int i = 0; i < 3; i++) {
                    pcc_gc_release(fillers[i]);
                }
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0", "1"]


def test_generational_backend_minor_refill_oldifies_copy_for_remembered_child(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_oldify_copy_probe.c"
    exe = tmp_path / "minor_oldify_copy_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                PyObject *loaded = pcc_gc_load_ptr(
                    (PyObject *)owner, &owner->items[0]
                );

                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", loaded == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
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
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0"]


def test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_oldify_slot_rewrite_probe.c"
    exe = tmp_path / "minor_oldify_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
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
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_non_list_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_frame_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_suspended_generator_frame_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_generator_coroutine_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_task_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_scheduler_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_scheduler_queue_entry_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_class_metadata_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_forwarded_minor_source_is_inactive_after_oldify(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_forwarded_minor_source_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_cross_domain_remembered_slot_rewrite(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)
    _assert_backend_three_cross_domain_remembered_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_oldify_copy_probe.c"
    exe = tmp_path / "pcc_py_minor_oldify_copy_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                PyObject *loaded = pcc_gc_load_ptr(
                    (PyObject *)owner, &owner->items[0]
                );

                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", loaded == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-pthread",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0"]


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_oldify_slot_rewrite_probe.c"
    exe = tmp_path / "pcc_py_minor_oldify_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-pthread",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_non_list_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_frame_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_suspended_generator_frame_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_generator_coroutine_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_task_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_scheduler_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_scheduler_queue_entry_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_class_metadata_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_forwarded_minor_source_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_cross_domain_remembered_slot_rewrite(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_cross_domain_remembered_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )
