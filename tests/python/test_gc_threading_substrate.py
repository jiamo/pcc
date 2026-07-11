from pathlib import Path
import os
import shutil
import subprocess
import textwrap

from pcc.py_frontend.codegen.runtime_abi import RUNTIME_SIGNATURES
from tests.runtime_build_cache import cache_runtime_build


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_HEADER = REPO_ROOT / "pcc" / "py_runtime" / "include" / "py_runtime.h"
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
RUNTIME_MAKEFILE = REPO_ROOT / "pcc" / "py_runtime" / "Makefile"
PY_OBJ_C = REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_obj.c"
PY_OBJ_PORT = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_obj.py"
THREADS_C = REPO_ROOT / "pcc" / "py_runtime" / "src" / "pcc_threads.c"
PY_OBJ_GC_C = REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_obj_gc.c"
PY_OBJ_GC_PORT = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_obj_gc.py"
PY_GC_BACKEND_C = REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_gc_backend.c"
PY_GC_BACKEND_PORT = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_backend.py"
CORE_HELPERS = REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "core_helpers.py"
CONTROL_FLOW_LOWERING = (
    REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "control_flow_lowering.py"
)
FOR_LOOP_LOWERING = (
    REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "for_loop_lowering.py"
)
USER_FUNCTION_LOWERING = (
    REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "user_function_lowering.py"
)


@cache_runtime_build
def _build_threaded_runtime(tmp_path: Path) -> Path:
    work_runtime = tmp_path / "py_runtime"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
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


THREADING_SURFACE = [
    "pcc_threads_enabled",
    "pcc_current_thread_id",
    "pcc_refcount_strategy",
    "pcc_thread_safepoint",
    "pcc_stop_the_world",
    "pcc_resume_world",
]


def test_threading_substrate_public_surface_is_in_header_and_runtime_abi():
    header = RUNTIME_HEADER.read_text(encoding="utf-8")
    for name in THREADING_SURFACE:
        assert name in header
        assert name in RUNTIME_SIGNATURES
    for name in [
        "PCC_REFCOUNT_STRATEGY_NONATOMIC",
        "PCC_REFCOUNT_STRATEGY_ATOMIC",
        "PCC_REFCOUNT_STRATEGY_BIASED",
        "PCC_REFCOUNT_STRATEGY_DEFERRED",
    ]:
        assert name in header


def test_threading_substrate_is_built_into_c_and_pcc_python_archives():
    makefile = RUNTIME_MAKEFILE.read_text(encoding="utf-8")
    assert "$(SRCDIR)/pcc_threads.c" in makefile
    assert "$(OBJDIR_PY)/pcc_threads.o" in makefile
    assert "PCC_WITH_THREADS" in makefile
    assert "PCC_REFCOUNT_KIND" in makefile


def test_refcount_paths_go_through_strategy_helpers():
    c_src = PY_OBJ_C.read_text(encoding="utf-8")
    assert "pcc_refcount_incref(&h->refcount)" in c_src
    assert "pcc_refcount_decref(&h->refcount)" in c_src
    assert "h->refcount++" not in c_src
    assert "--h->refcount" not in c_src

    py_src = PY_OBJ_PORT.read_text(encoding="utf-8")
    assert 'extern("pcc_refcount_incref"' in py_src
    assert 'extern("pcc_refcount_decref"' in py_src
    assert 'extern("pcc_refcount_forget"' in py_src
    assert "pcc_refcount_forget(o)" in py_src
    assert 'extern("py_dealloc_thread_thread"' in py_src
    assert "pcc_refcount_incref(o)" in py_src
    assert "pcc_refcount_decref(o)" in py_src


def test_gc_safepoint_polls_thread_gate_in_c_and_pcc_python_runtime():
    c_src = PY_OBJ_C.read_text(encoding="utf-8")
    c_body = c_src.split("void pcc_gc_safepoint(void)", 1)[1]
    c_body = c_body.split("int64_t pcc_gc_collect", 1)[0]
    assert "pcc_gc_note_safepoint()" in c_body
    assert "pcc_thread_safepoint()" in c_body

    py_src = PY_OBJ_PORT.read_text(encoding="utf-8")
    py_body = py_src.split('@c_abi_export("pcc_gc_safepoint")', 1)[1]
    py_body = py_body.split('@c_abi_export("pcc_gc_collect")', 1)[0]
    assert "pcc_gc_note_safepoint()" in py_body
    assert "pcc_thread_safepoint()" in py_body


def test_gc_alloc_polls_thread_gate_without_non_threaded_gc_step():
    c_src = PY_OBJ_C.read_text(encoding="utf-8")
    c_body = c_src.split("PyObject *pcc_gc_alloc", 1)[1]
    c_body = c_body.split("PyObject *pcc_gc_retain", 1)[0]
    assert "pcc_thread_safepoint()" in c_body
    assert c_body.index("pcc_thread_safepoint()") < c_body.index("pcc_gc_note_alloc(size)")
    assert "pcc_gc_safepoint()" not in c_body
    assert "pcc_gc_step(" not in c_body

    py_src = PY_OBJ_PORT.read_text(encoding="utf-8")
    py_body = py_src.split('@c_abi_export("pcc_gc_alloc")', 1)[1]
    py_body = py_body.split('@c_abi_export("pcc_gc_retain")', 1)[0]
    assert "pcc_thread_safepoint()" in py_body
    assert py_body.index("pcc_thread_safepoint()") < py_body.index("pcc_gc_note_alloc(size)")
    assert "pcc_gc_safepoint()" not in py_body
    assert "pcc_gc_step(" not in py_body


def test_python_codegen_emits_thread_safepoint_at_loop_backedges_and_function_entry():
    core_src = CORE_HELPERS.read_text(encoding="utf-8")
    assert "def _emit_thread_safepoint" in core_src
    assert 'self.runtime["pcc_thread_safepoint"]' in core_src

    while_src = CONTROL_FLOW_LOWERING.read_text(encoding="utf-8")
    assert 'name=self._fresh("while.latch")' in while_src
    assert (
        "self.loop_stack.append((latch_bb, end_bb, self._loop_finally_base()))"
        in while_src
    )
    assert "self._emit_thread_safepoint()" in while_src
    assert "self.builder.branch(cond_bb)" in while_src

    for_src = FOR_LOOP_LOWERING.read_text(encoding="utf-8")
    assert for_src.count("self._emit_thread_safepoint()") >= 8
    for marker in [
        '"for.cpy.latch"',
        '"for.obj.latch"',
        '"for.iter.latch"',
        '"async.for.latch"',
        '"comp.latch"',
    ]:
        assert marker in for_src

    user_src = USER_FUNCTION_LOWERING.read_text(encoding="utf-8")
    assert "def _low_builder_thread_safepoint" in user_src
    assert '"pcc_thread_safepoint"' in user_src
    assert "self._emit_thread_safepoint()" in user_src


def test_python_codegen_ir_contains_loop_and_entry_thread_safepoints(
    tmp_path,
    monkeypatch,
):
    from pcc.py_frontend.pipeline import compile_python

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    src = tmp_path / "loop_safepoints.py"
    out_ll = tmp_path / "loop_safepoints.ll"
    src.write_text(textwrap.dedent("""
        def spin(n: int) -> int:
            acc = 0
            while acc < n:
                acc = acc + 1
                if acc == 3:
                    continue
            for i in range(0, n):
                acc = acc + i
            return acc
        """).lstrip(), encoding="utf-8")

    compile_python(
        str(src),
        str(out_ll),
        ir_scaffold_mode="on",
        libpython_mode="off",
        emit_llvm_only=True,
    )
    ir_text = out_ll.read_text(encoding="utf-8")
    assert "@pcc_thread_stop_requested = external global i32" in ir_text
    assert (
        "declare external void @pcc_thread_safepoint()" in ir_text
        or "declare void @pcc_thread_safepoint()" in ir_text
    )
    assert ir_text.count("@pcc_thread_safepoint()") >= 4
    assert "while.latch" in ir_text
    assert "for.step" in ir_text


def test_biased_and_deferred_refcount_are_recognized_strategies():
    """All four refcount strategies must be wired in pcc_threads.c.

    Originally this test guarded the *unimplemented* state by asserting the
    "PyObjectHeader ABI migration" error string. v2 of the threading patch
    made BIASED / DEFERRED actually build (their semantics still degrade to
    ATOMIC pending the real ob_tid layout split, but the strategy enum is
    plumbed end-to-end). Flip the assertion to the now-correct state.
    """
    src = THREADS_C.read_text(encoding="utf-8")
    assert "PCC_REFCOUNT_KIND_BIASED" in src
    assert "PCC_REFCOUNT_KIND_DEFERRED" in src
    assert "__atomic_add_fetch" in src
    assert "__atomic_sub_fetch" in src


def test_refcount_cycle_gc_collect_wraps_stw_gate_in_c_and_pcc_python_runtime():
    c_src = PY_OBJ_GC_C.read_text(encoding="utf-8")
    c_body = c_src.split("int64_t py_gc_collect(void)", 1)[1]
    c_body = c_body.split("void py_gc_track", 1)[0]
    assert "pcc_stop_the_world()" in c_body
    assert "pcc_resume_world()" in c_body
    assert c_body.index("pcc_stop_the_world()") < c_body.index("py_gc_collecting = 1")
    assert c_body.index("pcc_resume_world()") > c_body.index("py_gc_dealloc_unreachable")

    py_src = PY_OBJ_GC_PORT.read_text(encoding="utf-8")
    py_body = py_src.split('@c_abi_export("py_gc_collect")', 1)[1]
    py_body = py_body.split('@c_abi_export("py_gc_track")', 1)[0]
    assert 'extern("pcc_stop_the_world"' in py_src
    assert 'extern("pcc_resume_world"' in py_src
    assert "pcc_stop_the_world()" in py_body
    assert "pcc_resume_world()" in py_body


def test_generational_backend_step_polls_thread_safepoint_in_c_and_pcc_python_runtime():
    c_src = PY_GC_BACKEND_C.read_text(encoding="utf-8")
    c_body = c_src.split("int64_t pcc_gc_step(int64_t budget)", 1)[1]
    c_body = c_body.split("void pcc_gc_note_alloc", 1)[0]
    c_body = c_body.split("PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR", 1)[1]
    c_body = c_body.split("PCC_GC_KIND_COLORED_RELOCATING", 1)[0]
    c_helper = c_src.split("static int64_t pcc_gc_step_generational_promotion", 2)[2]
    c_helper = c_helper.split("int64_t pcc_gc_step(int64_t budget)", 1)[0]
    assert "PCC_GC_SAFEPOINT_BATCH" in c_src
    assert "pcc_gc_step_generational_promotion(budget, 1)" in c_body
    assert "pcc_thread_safepoint()" in c_helper

    py_src = PY_GC_BACKEND_PORT.read_text(encoding="utf-8")
    py_body = py_src.split("elif backend == 3:", 1)[1]
    py_body = py_body.split("elif backend == 4:", 1)[0]
    py_helper = py_src.split("def _step_generational_promotion", 1)[1]
    py_helper = py_helper.split('@c_abi_export("pcc_gc_step")', 1)[0]
    assert 'extern("pcc_thread_safepoint"' in py_src
    assert "_step_generational_promotion(budget, 1)" in py_body
    assert "pcc_thread_safepoint()" in py_helper


def test_tracing_gc_finalizer_handles_thread_objects_and_refcount_side_table():
    c_src = PY_GC_BACKEND_C.read_text(encoding="utf-8")
    assert "PccGcThreadObject" in c_src
    fixed_owner = c_src.split("static int pcc_gc_visit_fixed_owner_slots(", 1)[1]
    fixed_owner = fixed_owner.split(
        "static int pcc_gc_visit_continuation_owner_slots(",
        1,
    )[0]
    assert "PccGcThreadObject *t = (PccGcThreadObject *)o" in fixed_owner
    assert "visit(&t->callable, ctx)" in fixed_owner
    assert "visit(&t->args, ctx)" in fixed_owner
    assert "visit(&t->result, ctx)" in fixed_owner
    assert "pcc_refcount_forget(&h->refcount)" in c_src
    for name in [
        "py_dealloc_thread_lock",
        "py_dealloc_thread_rlock",
        "py_dealloc_thread_event",
        "py_dealloc_thread_condition",
        "py_dealloc_thread_semaphore",
        "py_dealloc_thread_thread",
    ]:
        assert name in c_src

    py_src = PY_GC_BACKEND_PORT.read_text(encoding="utf-8")
    extern_import = next(
        line for line in py_src.splitlines() if line.startswith("from pcc.extern import ")
    )
    for name in ["c_abi_export", "c_int64", "c_ptr", "c_void", "extern"]:
        assert name in extern_import
    assert "pcc_refcount_forget(o)" in py_src
    assert "tag == 27" in py_src
    assert "py_dealloc_thread_thread(o)" in py_src

    thread_port = (REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_threading.py").read_text(
        encoding="utf-8"
    )
    assert '@c_abi_export("py_dealloc_thread_thread")' in thread_port
    assert "pcc_gc_store_ptr(o, ptr_add(o, 24), callable)" in thread_port
    assert "pcc_gc_store_ptr(o, ptr_add(o, 32), args)" in thread_port
    assert "py_decref_extern(pcc_gc_load_ptr(thread, ptr_add(thread, 24)))" in thread_port


def test_no_libpython_all_backends_collect_through_thread_gate(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent("""
        from pcc.extern import extern, c_int32, c_int64, c_void

        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
        pcc_gc_safepoint = extern("pcc_gc_safepoint", (), c_void)
        pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
        pcc_resume_world = extern("pcc_resume_world", (), c_int64)

        def main() -> None:
            b = 0
            while b < 5:
                print(pcc_gc_set_backend(b))
                pcc_gc_safepoint()
                print(pcc_gc_collect(0) >= 0)
                print(pcc_stop_the_world())
                print(pcc_resume_world())
                b = b + 1

        if __name__ == "__main__":
            main()
        """).lstrip(), encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    expected: list[str] = []
    for _backend in range(5):
        expected.extend(["0", "True", "0", "0"])
    assert result.stdout.strip().splitlines() == expected


def test_pthread_substrate_stop_the_world_stress(tmp_path):
    """Exercise the real pthread path without exposing Python-level threads.

    The worker repeatedly enters safepoints while the main thread performs
    many STW/resume cycles. This catches the stale-parked-thread race where
    a second STW can start before a just-resumed worker clears its TLS parked
    flag.
    """
    work_runtime = _build_threaded_runtime(tmp_path)
    cc = os.environ.get("CC", "cc")
    src = tmp_path / "thread_smoke.c"
    exe = tmp_path / "thread_smoke.out"
    src.write_text(textwrap.dedent(r"""
        #include "py_internal.h"
        #include <stdint.h>
        #include <stdio.h>

        static int64_t worker_done = 0;
        static int64_t worker_seen = 0;

        static void *worker_main(void *arg) {
            (void)arg;
            (void)pcc_current_thread_id();
            while (__atomic_load_n(&worker_done, __ATOMIC_RELAXED) == 0) {
                pcc_thread_safepoint();
                __atomic_add_fetch(&worker_seen, 1, __ATOMIC_RELAXED);
            }
            return 0;
        }

        int main(void) {
            if (pcc_threads_enabled() != 1) return 2;
            (void)pcc_current_thread_id();

            PccThreadHandle *thread = 0;
            if (pcc_thread_start(&thread, worker_main, 0) != 0) return 3;

            while (__atomic_load_n(&worker_seen, __ATOMIC_RELAXED) < 1) {
                pcc_thread_safepoint();
            }

            for (int i = 0; i < 64; i++) {
                if (pcc_stop_the_world() != 0) return 10;
                if (pcc_stop_the_world() != 0) return 11;
                pcc_thread_safepoint();
                if (pcc_resume_world() != 0) return 12;
                if (pcc_resume_world() != 0) return 13;
            }

            __atomic_store_n(&worker_done, 1, __ATOMIC_RELAXED);
            void *result = 0;
            if (pcc_thread_join(thread, &result) != 0) return 14;
            if (pcc_resume_world() != -1) return 15;

            PccMutex *mutex = pcc_mutex_new();
            PccCond *cond = pcc_cond_new();
            if (mutex == 0 || cond == 0) return 16;
            if (pcc_mutex_lock(mutex) != 0) return 17;
            if (pcc_cond_signal(cond) != 0) return 18;
            if (pcc_cond_broadcast(cond) != 0) return 19;
            if (pcc_mutex_unlock(mutex) != 0) return 20;
            pcc_cond_free(cond);
            pcc_mutex_free(mutex);

            printf("ok\n");
            return 0;
        }
        """).lstrip(), encoding="utf-8")

    cmd = [
        cc,
        "-DPCC_WITH_THREADS=1",
        "-std=c11",
        "-pthread",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / "libpy_runtime.a"),
        "-lm",
        "-o",
        str(exe),
    ]
    build = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_concurrent_stop_the_world_requesters_are_serialized(tmp_path):
    """A second STW requester parks for the owner, then gets its own turn."""
    work_runtime = _build_threaded_runtime(tmp_path)
    cc = os.environ.get("CC", "cc")
    src = tmp_path / "concurrent_stw.c"
    exe = tmp_path / "concurrent_stw.out"
    src.write_text(textwrap.dedent(r"""
        #include "py_internal.h"
        #include <stdint.h>
        #include <stdio.h>

        static int64_t worker_started = 0;
        static int64_t worker_owned_stop = 0;

        static void *worker_main(void *arg) {
            (void)arg;
            __atomic_store_n(&worker_started, 1, __ATOMIC_RELEASE);
            if (pcc_stop_the_world() != 0) return (void *)(intptr_t)2;
            __atomic_store_n(&worker_owned_stop, 1, __ATOMIC_RELEASE);
            if (pcc_resume_world() != 0) return (void *)(intptr_t)3;
            return 0;
        }

        int main(void) {
            if (pcc_threads_enabled() != 1) return 2;
            (void)pcc_current_thread_id();

            PccThreadHandle *thread = 0;
            if (pcc_thread_start(&thread, worker_main, 0) != 0) return 3;
            while (__atomic_load_n(&worker_started, __ATOMIC_ACQUIRE) == 0) {
            }
            while (__atomic_load_n(
                &pcc_thread_stop_requested, __ATOMIC_ACQUIRE
            ) == 0) {
            }
            if (pcc_resume_world() != -1) return 4;

            /* The worker owns the first stop and is waiting for this live
             * thread to park. This call must serialize behind it, not fail. */
            if (pcc_stop_the_world() != 0) return 5;
            if (__atomic_load_n(&worker_owned_stop, __ATOMIC_ACQUIRE) != 1) {
                return 6;
            }
            if (pcc_resume_world() != 0) return 7;

            void *result = 0;
            if (pcc_thread_join(thread, &result) != 0) return 8;
            if (result != 0) return 9;
            printf("serialized-stw-ok\n");
            return 0;
        }
        """).lstrip(), encoding="utf-8")

    build = subprocess.run(
        [
            cc,
            "-DPCC_WITH_THREADS=1",
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-lm",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "serialized-stw-ok"


def test_threaded_allocator_boundary_is_safepoint_for_stw(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)
    cc = os.environ.get("CC", "cc")
    src = tmp_path / "alloc_safepoint.c"
    exe = tmp_path / "alloc_safepoint.out"
    src.write_text(textwrap.dedent(r"""
        #include "py_internal.h"
        #include <stdint.h>
        #include <stdio.h>

        static int64_t worker_done = 0;
        static int64_t worker_started = 0;
        static int64_t worker_iterations = 0;

        static void *worker_main(void *arg) {
            (void)arg;
            (void)pcc_current_thread_id();
            __atomic_store_n(&worker_started, 1, __ATOMIC_RELEASE);
            while (__atomic_load_n(&worker_done, __ATOMIC_ACQUIRE) == 0) {
                PyObject *obj = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                if (obj == 0) return (void *)(intptr_t)2;
                pcc_gc_release(obj);
                __atomic_add_fetch(&worker_iterations, 1, __ATOMIC_RELAXED);
            }
            return 0;
        }

        int main(void) {
            if (pcc_threads_enabled() != 1) return 2;

            PccThreadHandle *thread = 0;
            if (pcc_thread_start(&thread, worker_main, 0) != 0) return 3;

            while (__atomic_load_n(&worker_started, __ATOMIC_ACQUIRE) == 0) {
                pcc_thread_safepoint();
            }
            while (__atomic_load_n(&worker_iterations, __ATOMIC_RELAXED) < 1) {
                pcc_thread_safepoint();
            }

            for (int i = 0; i < 8; i++) {
                if (pcc_stop_the_world() != 0) return 10;
                if (pcc_resume_world() != 0) return 11;
            }

            __atomic_store_n(&worker_done, 1, __ATOMIC_RELEASE);
            void *result = 0;
            if (pcc_thread_join(thread, &result) != 0) return 12;
            if (result != 0) return 13;

            printf("alloc-safepoint-ok\n");
            return 0;
        }
        """).lstrip(), encoding="utf-8")

    build = subprocess.run(
        [
            cc,
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-lm",
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
    assert result.stdout.strip() == "alloc-safepoint-ok"


def test_thread_safepoint_composes_with_all_gc_backends(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent("""
        from pcc.extern import extern, c_int32, c_int64, c_void

        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
        pcc_gc_safepoint = extern("pcc_gc_safepoint", (), c_void)
        pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
        pcc_resume_world = extern("pcc_resume_world", (), c_int64)

        def exercise(backend: int) -> None:
            print(pcc_gc_set_backend(backend))
            print(pcc_stop_the_world())
            pcc_gc_safepoint()
            print(pcc_resume_world())
            print(pcc_gc_backend())
            print(pcc_gc_collect(0) >= 0)

        def main() -> None:
            exercise(0)
            exercise(1)
            exercise(2)
            exercise(3)
            exercise(4)

        if __name__ == "__main__":
            main()
        """).lstrip(), encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    expected: list[str] = []
    for backend in range(5):
        expected.extend(["0", "0", "0", str(backend), "True"])
    assert result.stdout.strip().splitlines() == expected


def test_threading_substrate_runs_in_no_libpython_binary(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent("""
        from pcc.extern import extern, c_int64, c_void

        pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
        pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
        pcc_refcount_strategy = extern("pcc_refcount_strategy", (), c_int64)
        pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
        pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
        pcc_resume_world = extern("pcc_resume_world", (), c_int64)

        def main() -> None:
            print(pcc_threads_enabled())
            print(pcc_refcount_strategy())
            tid1 = pcc_current_thread_id()
            tid2 = pcc_current_thread_id()
            print(tid1 == tid2)
            pcc_thread_safepoint()
            print(pcc_stop_the_world())
            pcc_thread_safepoint()
            print(pcc_resume_world())

        if __name__ == "__main__":
            main()
        """).lstrip(), encoding="utf-8")

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "0", "True", "0", "0"]
