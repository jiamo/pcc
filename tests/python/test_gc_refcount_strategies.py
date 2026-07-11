from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cache_runtime_build


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
PY_OBJ_C = RUNTIME_DIR / "src" / "py_obj.c"


def _cc() -> str:
    return os.environ.get("CC", "cc")


# pcc_threads.c is not standalone-linkable: it is part of the runtime (the file
# header notes it and py_gc_backend.c "are always compiled into every archive
# variant"). Its virtual-thread implementation references GC functions
# (pcc_gc_alloc, pcc_gc_load_ptr, pcc_gc_free_object_memory,
# pcc_gc_note_relocation_read) defined in py_obj.c / py_gc_backend.c. Linking
# only pcc_threads.c (as these refcount-strategy smokes do) therefore fails with
# `Undefined symbols`. Provide the GC symbols by also linking the prebuilt C
# runtime archive; the strategy-compiled pcc_threads.o still defines the
# refcount symbols, so the archive's pcc_threads.o is not pulled (no duplicate),
# and the smoke never exercises the GC, so the archive's build config is
# irrelevant to the assertions.
def _compile_threads_strategy(
    tmp_path: Path,
    strategy: int,
    c_runtime_archive: Path,
    with_threads: int = 0,
) -> Path:
    src = tmp_path / f"strategy_{strategy}.c"
    exe = tmp_path / f"strategy_{strategy}.out"
    src.write_text(textwrap.dedent(f"""
        #include "py_internal.h"
        #include <stdint.h>
        #include <stdio.h>

        int main(void) {{
            int64_t slot = 1;
            if (pcc_refcount_strategy() != {strategy}) return 10;
            if (pcc_threads_enabled() != {with_threads}) return 11;
            if (pcc_refcount_incref(&slot) != 2) return 12;
            if (pcc_refcount_decref(&slot) != 1) return 13;
            if (pcc_refcount_decref(&slot) != 0) return 14;
            printf("ok\\n");
            return 0;
        }}
        """).lstrip(), encoding="utf-8")
    cmd = [
        _cc(),
        f"-DPCC_WITH_THREADS={with_threads}",
        f"-DPCC_REFCOUNT_STRATEGY={strategy}",
        "-std=c11",
        "-pthread",
        f"-I{c_runtime_archive.parent / 'include'}",
        f"-I{c_runtime_archive.parent / 'src'}",
        str(src),
        str(c_runtime_archive.parent / "src" / "pcc_threads.c"),
        str(c_runtime_archive),
        "-o",
        str(exe),
    ]
    build = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    return exe


@cache_runtime_build
def _build_refcount_runtime(tmp_path: Path, strategy: int) -> Path:
    work_runtime = tmp_path / f"py_runtime_{strategy}"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    result = subprocess.run(
        [
            "make",
            "-B",
            "-C",
            str(work_runtime),
            "PCC_WITH_THREADS=1",
            f"PCC_REFCOUNT_KIND={strategy}",
            "libpy_runtime.a",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work_runtime


def test_nonatomic_refcount_strategy_smoke(tmp_path, c_runtime_archive):
    exe = _compile_threads_strategy(
        tmp_path, strategy=0, with_threads=0, c_runtime_archive=c_runtime_archive
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_atomic_refcount_strategy_smoke(tmp_path, c_runtime_archive):
    exe = _compile_threads_strategy(
        tmp_path, strategy=1, with_threads=1, c_runtime_archive=c_runtime_archive
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_biased_and_deferred_strategy_smoke(tmp_path, c_runtime_archive):
    for strategy in (2, 3):
        exe = _compile_threads_strategy(
            tmp_path,
            strategy=strategy,
            with_threads=1,
            c_runtime_archive=c_runtime_archive,
        )
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"


def test_py_obj_refcount_dispatch_has_no_raw_mutation():
    src = PY_OBJ_C.read_text(encoding="utf-8")
    assert "pcc_refcount_incref(&h->refcount)" in src
    assert "pcc_refcount_decref(&h->refcount)" in src
    assert "h->refcount++" not in src
    assert "--h->refcount" not in src


def test_atomic_refcount_runtime_archive_builds_and_links(tmp_path):
    """Build the full C runtime archive with PCC_REFCOUNT_KIND=1.

    The source tree is copied to a temp directory so this test does not race
    with normal no-libpython compile tests or leave the repository archive in
    an atomic/threaded configuration.
    """
    work_runtime = _build_refcount_runtime(tmp_path, 1)

    smoke = tmp_path / "archive_smoke.c"
    exe = tmp_path / "archive_smoke.out"
    smoke.write_text(textwrap.dedent("""
        #include "py_runtime.h"
        #include <stdint.h>

        int main(void) {
            if (pcc_threads_enabled() != 1) return 1;
            if (pcc_refcount_strategy() != PCC_REFCOUNT_STRATEGY_ATOMIC) return 2;
            if (pcc_stop_the_world() != 0) return 3;
            if (pcc_resume_world() != 0) return 4;
            return 0;
        }
        """).lstrip(), encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            str(smoke),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr


def test_all_refcount_strategy_runtime_archives_build(tmp_path):
    for strategy in (0, 1, 2, 3):
        assert (_build_refcount_runtime(tmp_path, strategy) / "libpy_runtime.a").is_file()
