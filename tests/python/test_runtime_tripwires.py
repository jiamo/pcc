from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


@pytest.fixture(scope="module")
def armed_runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    runtime = tmp_path_factory.mktemp("tripwire-runtime") / "py_runtime"
    shutil.copytree(
        RUNTIME_DIR,
        runtime,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    env = {**os.environ, "CPPFLAGS": "-DPCC_RUNTIME_TRIPWIRES"}
    build = subprocess.run(
        ["make", "-B", "-C", str(runtime), "libpy_runtime.a"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return runtime


def _compile_probe(tmp_path: Path, runtime: Path, name: str, source: str) -> Path:
    src = tmp_path / f"{name}.c"
    exe = tmp_path / name
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    build = subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            f"-I{runtime / 'include'}",
            f"-I{runtime / 'src'}",
            str(src),
            str(runtime / "libpy_runtime.a"),
            "-lm",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return exe


def test_tripwire_source_covers_named_runtime_boundaries():
    internal = (RUNTIME_DIR / "src" / "py_internal.h").read_text(encoding="utf-8")
    gc_source = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    handle_source = (RUNTIME_DIR / "src" / "py_cpy_handle.c").read_text(
        encoding="utf-8"
    )

    assert "#ifdef PCC_RUNTIME_TRIPWIRES" in internal
    assert "#define PCC_RT_TRIPWIRE(cond, msg) ((void)0)" in internal
    for message in (
        "UNKNOWN forwarding lookup returned the wrong source",
        "UNKNOWN forwarding source/target type_tag mismatch",
        "UNKNOWN zpage forwarding source lost its retained span",
        "live forwarding span was not retained as a zombie page",
        "forwarding count underflow / duplicate removal",
        "registered scheduler root has a NULL slot address",
        "scheduler root prev/next linkage mismatch",
        "continuation root map/count drift",
    ):
        assert message in gc_source
    for message in (
        "cannot own a NULL foreign reference",
        "invalid native-handle object",
        "owned foreign reference has no release hook",
    ):
        assert message in handle_source
    py_obj_source = (RUNTIME_DIR / "src" / "py_obj.c").read_text(
        encoding="utf-8"
    )
    validity = py_obj_source.split("static int py_type_tag_is_valid", 1)[1].split(
        "PyObject *py_bool_from_bit", 1
    )[0]
    assert "tag == PY_TYPE_CPY_HANDLE" in validity


def test_armed_tripwires_accept_valid_roots_zpage_forwarding_and_native_handle(
    tmp_path: Path,
    armed_runtime: Path,
):
    exe = _compile_probe(
        tmp_path,
        armed_runtime,
        "tripwire_valid_probe",
        r'''
        #include "py_runtime.h"
        #include "py_internal.h"

        static int release_hits = 0;

        static void release_foreign(void *ptr) {
            if (ptr == (void *)(uintptr_t)0x2000) release_hits++;
        }

        int main(void) {
            if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) return 2;

            py_cpy_handle_set_release_fn(release_foreign);
            PyObject *handle = py_cpy_handle_new((void *)(uintptr_t)0x2000);
            if (handle == 0) return 3;
            py_decref(handle);
            if (release_hits != 1) return 4;

            /* A zero-length list-shaped object is non-leaf, so backend 4
             * places it on a zpage and the relocation selector can choose it. */
            PyObject *root = pcc_gc_alloc(64, PY_TYPE_LIST, 0);
            if (root == 0) return 5;
            pcc_gc_scheduler_root_register(&root);
            if (pcc_gc_scheduler_root_count() != 1) return 6;

            int32_t frame_map[2] = {1, 0};
            pcc_gc_register_continuation_root(frame_map, &root);
            if (pcc_gc_continuation_root_slot_count() != 1) return 7;

            if (pcc_gc_select_relocation_set(8) <= 0) return 8;
            if (!pcc_gc_relocation_set_contains(root)) return 9;
            PyObject *old = root;
            PyObject *moved = pcc_gc_relocate_copy(old, 64);
            if (moved == 0 || moved == old) return 10;
            if (pcc_gc_note_relocation_read(old) != moved) return 11;

            pcc_gc_unregister_continuation_root(&root);
            pcc_gc_scheduler_root_unregister(&root);
            return 0;
        }
        ''',
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_armed_tripwire_fault_injection_aborts_with_runtime_log(
    tmp_path: Path,
    armed_runtime: Path,
):
    exe = _compile_probe(
        tmp_path,
        armed_runtime,
        "tripwire_fault_probe",
        r'''
        #include "py_runtime.h"
        #include <stdint.h>

        int main(void) {
            PyObject *handle = py_cpy_handle_new((void *)(uintptr_t)0x1000);
            if (handle == 0) return 2;
            /* Deliberately omit py_cpy_handle_set_release_fn(). */
            py_decref(handle);
            return 3;
        }
        ''',
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PCC_LOG": "runtime"},
    )
    assert result.returncode != 0
    assert "TRIPWIRE" in result.stderr
    assert "owned foreign reference has no release hook" in result.stderr


def test_owned_acquire_download_has_no_fixed_transfer_timeout():
    """`pcc1 -m pip install numpy` must not fail on slow-but-alive downloads.

    A hard CURLOPT_TIMEOUT once capped every transfer at 60s, which failed the
    README numpy flow exactly at the bandwidth cliff (20MB sdist at ~0.4MB/s).
    The libcurl path must abort on STALL (low-speed options), never on a fixed
    total-transfer wall clock.
    """
    source = (RUNTIME_DIR / "src" / "py_http.c").read_text(encoding="utf-8")
    assert "PCC_CURLOPT_LOW_SPEED_LIMIT" in source
    assert "PCC_CURLOPT_LOW_SPEED_TIME" in source
    assert "PCC_CURLOPT_TIMEOUT" not in source, (
        "a fixed total-transfer timeout regressed into the owned-acquire "
        "libcurl path; use low-speed (stall) abort instead"
    )
