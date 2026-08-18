"""Phase 1 exit gate: every pcc/py_runtime/src/*.c must compile through pcc
--emit-obj.

This is the first step of the equivalence chain that ends at Phase 4. If pcc
cannot emit an object from its own C runtime source, it cannot serve as the
middle oracle for pcc-Python runtime validation.

Upgrade path (task #166): once a differential oracle harness exists (Phase 0),
this test should also assert that the pcc-emitted object's behavior is
byte-equivalent to the cc-emitted object.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_SRC = REPO_ROOT / "pcc" / "py_runtime" / "src"
RUNTIME_INC = REPO_ROOT / "pcc" / "py_runtime" / "include"

# Platform-syscall C-kernel helpers that intentionally stay in C: they read
# ABI-sensitive OS structs through SDK headers (macOS <mach/mach.h>,
# <malloc/malloc.h>) that use modern syntax pcc's C frontend does not parse
# (C23 `enum : uint64_t`, bool bitfields). The pcc-emitted runtime archive
# CC-builds them (py_runtime/Makefile PCC_CC_ONLY_SRCS); a fake-libc shadow
# would have to duplicate the OS struct layout exactly, which is fragile. They
# have no pcc-Python port and no runtime caller, so they are not part of the
# "pcc compiles its own runtime" oracle gate.
# LIBC-P2-SDK-STRUCT-HELPERS closed the last two per-source cc
# dependencies: mach/malloc/rusage declarations now live in the fake
# libc headers with SDK-locked layouts
# (tests/python/test_sdk_struct_helpers_pcc.py).
_CC_ONLY_KERNEL_SOURCES: set[str] = set()

# Large addressable struct assignments now lower to bounded aggregate memory
# copies, so py_re_engine.c no longer needs a special ~300s exemption. Keep one
# strict timeout for every runtime source; a renewed IR-shape/codegen blow-up
# must fail like any other performance regression.
_DEFAULT_EMIT_TIMEOUT = 120

# This repository-scale self-compilation gate emits every runtime C source.
# Keep it out of the default unit suite; the integration run owns its cost.
# Within that gate, serialize compiler subprocesses to bound peak memory.
_PCC_BIN = shutil.which("pcc")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.xdist_group(name="pcc_runtime_emit"),
    pytest.mark.pcc_gate(
        unavailable=None if _PCC_BIN else "pcc CLI (cli_core) not on PATH"
    ),
]


def _runtime_sources() -> list[Path]:
    # CC-only C-kernel platform helpers (SDK-header / ABI structs; see
    # Makefile PCC_CC_ONLY_SRCS) are statically inapplicable, not skips.
    return sorted(
        p for p in RUNTIME_SRC.glob("*.c")
        if p.name not in _CC_ONLY_KERNEL_SOURCES
    )


def _emit_runtime_object(
    tmp_path: Path,
    src: Path,
    *,
    cpp_args: tuple[str, ...] = (),
) -> Path:
    pcc_bin = _PCC_BIN
    assert pcc_bin is not None, "pcc CLI is required by the module gate"
    obj_path = tmp_path / (src.stem + ".o")
    cmd = [
        pcc_bin,
        f"--cpp-arg=-I{RUNTIME_INC}",
        f"--cpp-arg=-I{RUNTIME_SRC}",
        *(f"--cpp-arg={arg}" for arg in cpp_args),
        "--emit-obj",
        str(obj_path),
        str(src),
    ]
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_DEFAULT_EMIT_TIMEOUT,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert result.returncode == 0, (
        f"pcc --emit-obj {src.name} failed:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert obj_path.is_file(), f"missing object file {obj_path}"
    assert obj_path.stat().st_size > 0, f"empty object file {obj_path}"
    return obj_path


@pytest.mark.parametrize(
    "src",
    _runtime_sources(),
    ids=lambda p: p.name,
)
def test_pcc_emits_object_for_runtime_source(tmp_path, src):
    _emit_runtime_object(tmp_path, src)


def test_pcc_c_thread_transition_tu_links_host_libc_abort(
    tmp_path: Path,
    c_runtime_archive: Path,
):
    """Prove only the host pcc-C transition TU and host-libc abort edge."""
    threads_obj = _emit_runtime_object(
        tmp_path,
        RUNTIME_SRC / "pcc_threads.c",
        cpp_args=("-DPCC_WITH_THREADS=0",),
    )

    nm_bin = shutil.which("nm")
    assert nm_bin is not None, "nm is required for the transition-TU boundary gate"
    undefined_result = subprocess.run(
        [nm_bin, "-u", str(threads_obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert undefined_result.returncode == 0, (
        undefined_result.stdout + undefined_result.stderr
    )
    undefined = {
        name[1:] if name.startswith("_") else name
        for line in undefined_result.stdout.splitlines()
        if line.strip()
        for name in [line.split()[-1]]
    }
    assert "abort" in undefined
    assert "pcc_platform_abort" not in undefined

    probe_src = tmp_path / "pcc_c_thread_transition_probe.c"
    probe_exe = tmp_path / "pcc_c_thread_transition_probe.out"
    probe_src.write_text(
        """#include "py_runtime.h"

int main(void) {
    if (pcc_threads_enabled() != 0) return 1;
    if (pcc_thread_no_park_depth() != 0) return 2;
    pcc_thread_no_park_enter();
    if (pcc_thread_no_park_depth() != 1) return 3;
    pcc_thread_no_park_exit();
    if (pcc_thread_no_park_depth() != 0) return 4;
    return 0;
}
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    link_result = subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            f"-I{RUNTIME_INC}",
            str(probe_src),
            # Keep the pcc-emitted owner before the host-C dependency archive.
            str(threads_obj),
            str(c_runtime_archive),
            "-lm",
            "-o",
            str(probe_exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert link_result.returncode == 0, link_result.stdout + link_result.stderr
    run_result = subprocess.run(
        [str(probe_exe)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr


def test_runtime_source_inventory_has_expected_files():
    """Smoke check that we haven't accidentally broken the glob."""
    names = {p.name for p in _runtime_sources()}
    # Required anchors for the 4 formerly-blocked files and the allocator.
    required = {
        "py_class.c",
        "py_int.c",
        "py_print_fmt.c",
        "py_str.c",
        "py_obj.c",
        "py_tuple.c",
        "py_obj_ops_compare.c",
        "py_obj_ops_dispatch.c",
    }
    missing = required - names
    assert not missing, f"runtime source inventory missing: {missing}"
