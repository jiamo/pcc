from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def _write_case(path: Path) -> None:
    path.write_text(
        textwrap.dedent("""
            import pcc.virtual_thread as vt

            def worker(left: int, right: int) -> int:
                temp: int = left + right
                return temp + 1

            def main() -> None:
                thread = vt.spawn(worker, 20, 21)
                print(vt.run(2, 8))
                print(vt.state(thread))
                print(vt.result(thread))

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )


def _write_from_import_case(path: Path) -> None:
    path.write_text(
        textwrap.dedent("""
            from pcc.virtual_thread import result, run, spawn, state

            def worker(value: int) -> int:
                return value + 1

            def main() -> None:
                thread = spawn(worker, 41)
                print(run(2, 8))
                print(state(thread))
                print(result(thread))

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )


def _write_generator_case(path: Path) -> None:
    path.write_text(
        textwrap.dedent("""
            import threading
            import pcc.virtual_thread as vt

            lock = threading.Lock()

            def first():
                base = 10
                lock.acquire()
                vt.yield_now()
                base = base + 1
                lock.release()
                return base

            def second():
                value = 20
                lock.acquire()
                value = value + 2
                lock.release()
                return value
                vt.yield_now()

            def main() -> None:
                a = vt.spawn(first)
                b = vt.spawn(second)
                print(vt.run(2, 10))
                print(vt.state(a))
                print(vt.result(a))
                print(vt.state(b))
                print(vt.result(b))

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )


def _write_blocking_primitives_case(path: Path) -> None:
    path.write_text(
        textwrap.dedent("""
            import threading
            import pcc.virtual_thread as vt

            event = threading.Event()
            cond = threading.Condition()
            sem = threading.Semaphore(0)

            def event_waiter():
                event.wait()
                return 7
                vt.yield_now()

            def event_setter():
                vt.yield_now()
                event.set()
                return 8

            def cond_waiter():
                cond.acquire()
                cond.wait()
                cond.release()
                return 9
                vt.yield_now()

            def cond_notifier():
                vt.yield_now()
                cond.acquire()
                cond.notify()
                cond.release()
                return 10

            def sem_waiter():
                sem.acquire()
                return 11
                vt.yield_now()

            def sem_releaser():
                vt.yield_now()
                sem.release()
                return 12

            def main() -> None:
                a = vt.spawn(event_waiter)
                b = vt.spawn(event_setter)
                c = vt.spawn(cond_waiter)
                d = vt.spawn(cond_notifier)
                e = vt.spawn(sem_waiter)
                f = vt.spawn(sem_releaser)
                print(vt.run(3, 30))
                print(vt.result(a))
                print(vt.result(b))
                print(vt.result(c))
                print(vt.result(d))
                print(vt.result(e))
                print(vt.result(f))

            if __name__ == "__main__":
                main()
            """).lstrip(),
        encoding="utf-8",
    )


def test_virtual_thread_spawn_lowers_to_typed_resume_ir(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_frontend.py"
    ll = tmp_path / "vthread_frontend.ll"
    _write_case(src)

    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )

    ir_text = ll.read_text(encoding="utf-8")
    assert "py_continuation_new_typed" in ir_text
    assert "__vthread_resume_2" in ir_text
    assert "py_continuation_get_slot" in ir_text
    assert "py_virtual_thread_complete" in ir_text


def test_virtual_thread_generator_spawn_lowers_to_state_machine_resume_ir(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_generator.py"
    ll = tmp_path / "vthread_generator.ll"
    _write_generator_case(src)

    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
    )

    ir_text = ll.read_text(encoding="utf-8")
    assert "py_virtual_thread_resume_generator" in ir_text
    assert "py_threading_lock_acquire_vthread" in ir_text
    assert "__gen_resume" in ir_text


def test_virtual_thread_spawn_runs_direct_user_function(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_frontend.py"
    exe = tmp_path / "vthread_frontend.out"
    _write_case(src)

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines() == ["1", "4", "42"]


def test_virtual_thread_import_from_aliases_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_from_import.py"
    exe = tmp_path / "vthread_from_import.out"
    _write_from_import_case(src)

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines() == ["1", "4", "42"]


def test_virtual_thread_generator_spawn_preserves_frame_and_parks_lock(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_generator.py"
    exe = tmp_path / "vthread_generator.out"
    _write_generator_case(src)

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines() == ["4", "4", "11", "4", "22"]


def test_virtual_thread_generator_parks_threading_wait_primitives(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "vthread_blocking.py"
    exe = tmp_path / "vthread_blocking.out"
    _write_blocking_primitives_case(src)

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert int(lines[0]) >= 9
    assert lines[1:] == ["7", "8", "9", "10", "11", "12"]
