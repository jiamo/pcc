"""Relocation copies must not inherit a finished-cycle sweep verdict.

GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED: a freshly relocated copy of a
reachable value carried PY_FLAG_GC_SWEEP_CANDIDATE inherited through the
header memcpy from stale source metadata, and the next explicit collect --
which consumes pending candidates WITHOUT re-marking
(pcc_gc_collect_tracing sweeps pending candidates verbatim) -- ran PASS-0
``__del__`` on the live copy. CONFIRMED capture:
docs/investigations/gc-backend4-concurrent-entry-loss.md.

The contract under test is narrow and deterministic, no concurrency needed:
the backend-4 colored evacuation header memcpy must clear the stale verdict
from BOTH the destination copy and the sweep-visible source shell, so a
pending-candidate sweep can never consume a verdict that predates the copy.
The probe pokes the verdict directly, exactly as a finished tracing cycle
would have left it, relocates, and then drives the same explicit-collect
boundary the mutator drain used in the capture.

Scope note: the backend-3 generational oldify copy received the identical
mask hardening (1024 added to its cleared-flag set in both mirrors), but
its focused repro is not expressible through refill-driven promotion --
refill runs minor collections that consume a poked verdict on the young
source before oldify can copy it, which is the runtime consuming a verdict
correctly, not the inheritance defect. The backend-3 hardening is exercised
by the five-backend production contract instead.

Nonclaims: this probe does not exercise the concurrent-stepping window
itself (the overlap probes own that), and it records but does not assert
finalizations of the immortal forwarding shell -- shell visibility to a
legitimately-cut sweep is a separate question from stale-verdict
inheritance.
"""
from pathlib import Path
import os
import subprocess

import pytest

from tests.runtime_build_cache import (
    cached_threaded_c_runtime,
    cached_threaded_pcc_python_runtime,
)

PROBE_SOURCE = r"""
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

static int64_t finalized;
static PyObject *watched_copy;
static int64_t del_on_copy;
static int64_t del_other;

extern struct PyClassObject *py_class_new(
    const char *, struct PyClassObject **, int32_t,
    const char **, int32_t
);
extern void py_class_add_method(
    struct PyClassObject *, const char *, PyObject *
);
extern PyObject *py_instance_new(struct PyClassObject *);

static PyObject *probe_del(PyObject *self) {
    __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
    if (watched_copy != 0 && self == watched_copy) del_on_copy++;
    else del_other++;
    return py_int_from_i64(0);
}

extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);

static int64_t instance_payload_size(PyObject *inst) {
    /* Mirror of the relocation validator's own size computation
     * (pcc_gc_relocate_copy_payload_prepared_locked): header plus one
     * slot per field and the optional __dict__ slot. */
    PyInstanceObject *obj = (PyInstanceObject *)inst;
    PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
        inst, (PyObject **)&obj->cls
    );
    if (cls == NULL) return 0;
    int32_t n_fields = cls->n_fields;
    if (n_fields < 0) n_fields = 0;
    int64_t n_slots = (int64_t)n_fields;
    if ((py_header((PyObject *)cls)->flags & 2) == 0) n_slots++;
    return (int64_t)sizeof(PyInstanceObject)
        + n_slots * (int64_t)sizeof(PyObject *);
}

static int32_t flags_of(PyObject *o) {
    return py_header_flags_load(py_header(o));
}

static int64_t refcount_of(PyObject *obj) {
    return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
}

static int fail_state(
    int code,
    const char *what,
    PyObject *source,
    PyObject *copy
) {
    fprintf(
        stderr,
        "code=%d what=%s src=%p src_flags=0x%x "
        "copy=%p copy_flags=0x%x copy_rc=%lld finalized=%lld "
        "del_on_copy=%lld del_other=%lld\n",
        code,
        what,
        (void *)source,
        source ? (unsigned)flags_of(source) : 0u,
        (void *)copy,
        copy ? (unsigned)flags_of(copy) : 0u,
        (long long)(copy ? refcount_of(copy) : -1),
        (long long)finalized,
        (long long)del_on_copy,
        (long long)del_other
    );
    return code;
}

int main(void) {
    const int32_t backend = @BACKEND@;
    if (pcc_refcount_strategy() != PCC_REFCOUNT_STRATEGY_ATOMIC) return 1;
    if (pcc_gc_set_backend(backend) != 0) return 2;

    struct PyClassObject *value_class =
        py_class_new("SweepVerdictValue", 0, 0, 0, 0);
    if (value_class == 0) return 3;
    pcc_gc_pin((PyObject *)value_class);
    py_class_add_method(
        value_class, "__del__", (PyObject *)(uintptr_t)probe_del
    );
    PyObject *source = py_instance_new(value_class);
    if (source == 0) return 4;

    static PyObject *root_slot;
    pcc_gc_store_root(&root_slot, source);
    /* Transfer the constructor reference into the owning root slot. */
    py_decref(source);
    void *root_handle = pcc_gc_scheduler_root_register_handle(&root_slot);
    if (root_handle == 0) return 5;

    /* Poke the verdict exactly as a finished tracing cycle leaves it on
     * an object it judged unreachable (white->candidate cut). */
    py_header_flags_or(py_header(source), PY_FLAG_GC_SWEEP_CANDIDATE);

    pcc_gc_reset_relocation_set();
    if (pcc_gc_select_relocation_set(1) != 1)
        return fail_state(13, "select", source, 0);
    PyObject *copy = pcc_gc_relocate_copy(
        source, instance_payload_size(source)
    );
    if (copy != 0) {
        /* relocate_copy hands back a caller-owned transfer ref. */
        py_decref(copy);
    }
    if (copy == 0 || copy == source)
        return fail_state(14, "relocate", source, copy);
    watched_copy = copy;

    /* THE CONTRACT: no published copy carries a finished-cycle verdict. */
    if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)
        return fail_state(20, "destination-inherits-verdict", source, copy);
    if (
        backend == PCC_GC_KIND_COLORED_RELOCATING
        && (flags_of(source) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0
    ) {
        /* The backend-4 shell stays sweep-visible until remap retirement,
         * so its own stale verdict must die with the old identity. */
        return fail_state(21, "shell-keeps-verdict", source, copy);
    }

    /* The capture's consumer: an explicit collect consumes pending
     * candidates verbatim. The live copy must survive untouched. */
    (void)pcc_gc_collect(0);
    if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)
        return fail_state(22, "collect-recut-live-copy", source, copy);
    if (del_on_copy != 0)
        return fail_state(23, "finalizer-ran-on-live-copy", source, copy);
    if (pcc_gc_object_is_known_no_lock(copy) != 1)
        return fail_state(24, "live-copy-unknown-after-collect", source, copy);

    /* Normal reclamation stays exactly-once: release the one owning root and
     * drive collections to the final dispatch.  Object-index membership is
     * not ownership and must never be used as a license to decref again. */
    pcc_gc_scheduler_root_unregister_handle(root_handle);
    pcc_gc_store_root(&root_slot, 0);
    root_slot = 0;
    for (int i = 0; i < 16 && del_on_copy == 0; i++) {
        (void)pcc_gc_collect(0);
    }
    if (backend == PCC_GC_KIND_COLORED_RELOCATING) {
        if (del_on_copy != 1)
            return fail_state(25, "exactly-once-violated", source, copy);
    } else if (pcc_gc_object_is_known_no_lock(copy) != 0) {
        return fail_state(26, "dead-copy-still-known", source, copy);
    }

    printf(
        "ok backend=%d del_other=%ld\n",
        backend,
        (long)del_other
    );
    return 0;
}
"""


def _runtime_archive(kind: str) -> Path:
    if kind == "c":
        return cached_threaded_c_runtime() / "libpy_runtime.a"
    return cached_threaded_pcc_python_runtime() / "libpy_runtime_pcc_py.a"


def _compile_probe(tmp_path: Path, kind: str, backend: int) -> Path:
    runtime_dir = _runtime_archive(kind).parent
    stem = f"sweep_verdict_gc{backend}_{kind}"
    source = tmp_path / f"{stem}.c"
    executable = tmp_path / stem
    source.write_text(
        PROBE_SOURCE.replace("@BACKEND@", str(backend)), encoding="utf-8"
    )
    command = [
        os.environ.get("CC", "cc"),
        "-std=c11",
        f"-I{runtime_dir / 'include'}",
        f"-I{runtime_dir / 'src'}",
        str(source),
        str(_runtime_archive(kind)),
        "-lm",
        "-o",
        str(executable),
    ]
    build = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stdout + build.stderr
    return executable


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_relocation_copy_clears_stale_sweep_verdict_backend4(
    tmp_path: Path, kind: str
) -> None:
    """Backend 4 colored evacuation must publish a verdict-free copy."""
    executable = _compile_probe(tmp_path, kind, backend=4)
    run_env = {**os.environ}
    run_env.pop("PCC_REFCOUNT_STRATEGY", None)
    run = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        timeout=120,
        env=run_env,
    )
    assert run.returncode == 0, (
        f"{kind} gc4 sweep-verdict relocation probe returned "
        f"{run.returncode}: " + run.stdout + run.stderr
    )


def test_source_sweep_verdict_clears_only_after_forwarding_commit() -> None:
    """Rollback must retain the source's pre-existing sweep verdict."""
    repo = Path(__file__).absolute().parents[2]
    c_source = (repo / "pcc/py_runtime/src/py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    c_body = c_source.split(
        "static PyObject *pcc_gc_relocate_copy_preallocated_unlocked(", 1
    )[1].split("static PyObject *pcc_gc_relocate_copy_unlocked(", 1)[0]
    c_commit = c_body.index("pcc_gc_install_forwarding_preallocated_unlocked(")
    c_clear = c_body.index(
        "py_header_flags_and(from_h, ~PY_FLAG_GC_SWEEP_CANDIDATE)"
    )
    assert c_commit < c_clear

    py_source = (
        repo / "pcc/py_runtime/py/freestanding_gc_relocation_copy.py"
    ).read_text(encoding="utf-8")
    py_body = py_source.split(
        "def pcc_gc_backend4_relocate_copy_preallocated_unlocked(", 1
    )[1].split("@c_abi_export", 1)[0]
    py_commit = py_body.index(
        "pcc_gc_install_forwarding_preallocated_unlocked("
    )
    py_clear = py_body.index(
        "store_i32(from_obj, 12, load_i32(from_obj, 12) & ~1024)"
    )
    assert py_commit < py_clear
