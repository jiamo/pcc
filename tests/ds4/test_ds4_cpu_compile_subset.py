from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DS4_ROOT = Path("~/pcc_refs/antirez-ds4-depth1").expanduser()
MANIFEST_PATH = Path(__file__).with_name("ds4_cpu_compile_subset.json")
PINNED_COMMIT = "80ebbc396aee40eedc1d829222f3362d10fa4c6c"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ds4_root() -> Path:
    root = Path(os.environ.get("PCC_DS4_ROOT", str(DEFAULT_DS4_ROOT))).expanduser()
    assert root.is_dir(), (
        f"pinned ds4 reference is required for this compile gate: {root}; "
        "absence is not compile evidence"
    )
    head = (root / ".git/HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        head = (root / ".git" / head.removeprefix("ref: ")).read_text(
            encoding="utf-8"
        ).strip()
    assert head == PINNED_COMMIT
    return root


@pytest.fixture(scope="module")
def pcc_bin() -> Path:
    candidate = Path(sys.executable).parent / "pcc"
    assert candidate.is_file(), f"pcc CLI is required: {candidate}"
    return candidate


@pytest.fixture(scope="module")
def cc_bin() -> str:
    cc = shutil.which("cc")
    assert cc is not None, "native cc is required as the ds4 CPU oracle"
    return cc


def _run(command: list[str], *, timeout: float = 90.0) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"command failed rc={result.returncode}: {command!r}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit(manifest: dict, unit_id: str) -> dict:
    return next(unit for unit in manifest["units"] if unit["id"] == unit_id)


def _pcc_object(
    pcc_bin: Path,
    source: Path,
    output: Path,
    *,
    cpp_args: tuple[str, ...] = (),
) -> None:
    _run(
        [
            str(pcc_bin),
            "--emit-obj",
            str(output),
            *(f"--cpp-arg={arg}" for arg in cpp_args),
            str(source),
        ]
    )
    assert output.is_file() and output.stat().st_size > 0


def test_subset_manifest_is_pinned_and_claim_bounded(manifest: dict, ds4_root: Path):
    assert manifest["pinned_commit"] == PINNED_COMMIT
    assert manifest["mode"] == "host-pcc-c-frontend"
    assert manifest["full_ds4_support_claimed"] is False
    assert {unit["category"] for unit in manifest["units"]} == {
        "c-posix",
        "gguf-quant-numeric",
        "kv-posix",
    }
    assert manifest["excluded"]
    for unit in manifest["units"]:
        assert _sha256(ds4_root / unit["source"]) == unit["source_sha256"]
        if "header" in unit:
            assert _sha256(ds4_root / unit["header"]) == unit["header_sha256"]


def test_generic_mmap_dirent_clock_fake_libc_surfaces_match_native(
    tmp_path: Path, pcc_bin: Path, cc_bin: str
):
    source = tmp_path / "posix_surface.c"
    source.write_text(
        """
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
int main(int argc, char **argv) {
    if (argc != 2) return 10;
    unsigned char *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return 11;
    p[0] = 37; p[4095] = 91;
    if (p[0] != 37 || p[4095] != 91 || munmap(p, 4096) != 0) return 12;
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 13;
    DIR *dir = opendir(argv[1]);
    if (!dir) return 14;
    int found = 0; struct dirent *entry;
    while ((entry = readdir(dir)) != NULL)
        if (strcmp(entry->d_name, "AGENTS.md") == 0) found = 1;
    if (closedir(dir) != 0 || !found) return 15;
    printf("anonymous=4096 agents=%d monotonic=%d\\n", found, ts.tv_sec >= 0);
    return 0;
}
""".lstrip(),
        encoding="utf-8",
    )
    pcc_obj = tmp_path / "posix.pcc.o"
    _pcc_object(pcc_bin, source, pcc_obj)
    pcc_exe = tmp_path / "posix.pcc"
    native_exe = tmp_path / "posix.native"
    _run([cc_bin, str(pcc_obj), "-o", str(pcc_exe)])
    _run([cc_bin, str(source), "-o", str(native_exe)])
    pcc_run = _run([str(pcc_exe), str(REPO_ROOT)], timeout=10)
    native_run = _run([str(native_exe), str(REPO_ROOT)], timeout=10)
    assert pcc_run.stdout == native_run.stdout == (
        "anonymous=4096 agents=1 monotonic=1\n"
    )


def test_ssd_posix_unit_matches_native_behavior(
    tmp_path: Path,
    manifest: dict,
    ds4_root: Path,
    pcc_bin: Path,
    cc_bin: str,
):
    unit = _unit(manifest, "ssd-posix")
    harness = tmp_path / "ssd_probe.c"
    harness.write_text(
        """
#include "ds4_ssd.h"
#include <inttypes.h>
#include <stdio.h>
int main(void) {
    uint64_t bytes = 0; uint32_t experts = 0;
    ds4_ssd_cache_plan plan; ds4_ssd_memory_lock lock;
    if (!ds4_parse_gib_arg("16GB", &bytes)) return 10;
    if (bytes != 16ull * 1024ull * 1024ull * 1024ull) return 11;
    if (ds4_parse_gib_arg("0", &bytes)) return 12;
    if (!ds4_parse_streaming_cache_experts_arg("37", &experts, &bytes)) return 13;
    if (experts != 37 || bytes != 0) return 14;
    if (!ds4_ssd_auto_cache_plan(1000, 200, 100, 4, &plan)) return 15;
    if (plan.model_target_bytes != 800 || plan.cache_bytes != 600 ||
        plan.cache_experts != 4 || plan.effective_cache_bytes != 400) return 16;
    if (!ds4_ssd_memory_lock_acquire(&lock, 0)) return 17;
    if (lock.ptr != NULL || lock.bytes != 0) return 18;
    ds4_ssd_memory_lock_release(&lock);
    printf("gib=%" PRIu64 " experts=%u plan=%" PRIu64 "/%" PRIu64 "/%u/%" PRIu64 "\\n",
           16ull * 1024ull * 1024ull * 1024ull, experts,
           plan.model_target_bytes, plan.cache_bytes, plan.cache_experts,
           plan.effective_cache_bytes);
    return 0;
}
""".lstrip(),
        encoding="utf-8",
    )
    source = ds4_root / unit["source"]
    pcc_obj = tmp_path / "ssd.pcc.o"
    _pcc_object(pcc_bin, source, pcc_obj, cpp_args=(f"-I{ds4_root}",))
    pcc_exe = tmp_path / "ssd.pcc"
    native_exe = tmp_path / "ssd.native"
    _run([cc_bin, f"-I{ds4_root}", str(harness), str(pcc_obj), "-o", str(pcc_exe)])
    _run([cc_bin, f"-I{ds4_root}", str(harness), str(source), "-o", str(native_exe)])
    pcc_run = _run([str(pcc_exe)], timeout=10)
    native_run = _run([str(native_exe)], timeout=10)
    assert pcc_run.stdout == native_run.stdout == unit["oracle_stdout"]


def test_q4k_quant_numeric_unit_matches_native_behavior(
    tmp_path: Path,
    manifest: dict,
    ds4_root: Path,
    pcc_bin: Path,
    cc_bin: str,
):
    unit = _unit(manifest, "q4k-quant")
    source = ds4_root / unit["source"]
    macros = ("-DDS4_NO_GPU", "-DDS4_Q4K_DOT_TEST_MAIN", f"-I{ds4_root}")
    pcc_obj = tmp_path / "q4k.pcc.o"
    _pcc_object(pcc_bin, source, pcc_obj, cpp_args=macros)
    pcc_exe = tmp_path / "q4k.pcc"
    native_exe = tmp_path / "q4k.native"
    _run([cc_bin, str(pcc_obj), "-lm", "-pthread", "-o", str(pcc_exe)])
    _run([cc_bin, *macros, str(source), "-lm", "-pthread", "-o", str(native_exe)])
    pcc_run = _run([str(pcc_exe)], timeout=20)
    native_run = _run([str(native_exe)], timeout=20)
    assert pcc_run.stdout == native_run.stdout
    assert unit["oracle_summary"] in pcc_run.stdout


def test_kvstore_complete_translation_unit_compiles_but_runtime_stays_unclaimed(
    tmp_path: Path,
    manifest: dict,
    ds4_root: Path,
    pcc_bin: Path,
    cc_bin: str,
):
    unit = _unit(manifest, "kvstore-tu")
    source = ds4_root / unit["source"]
    pcc_obj = tmp_path / "kvstore.pcc.o"
    native_obj = tmp_path / "kvstore.native.o"
    _pcc_object(pcc_bin, source, pcc_obj, cpp_args=(f"-I{ds4_root}",))
    _run([cc_bin, f"-I{ds4_root}", "-c", str(source), "-o", str(native_obj)])
    assert native_obj.stat().st_size > 0
    assert unit["classification"] == "PCC_OBJECT_COMPILE_SUPPORTED_RUNTIME_UNPROVEN"
    assert "engine/session/token payload" in unit["runtime_boundary"]
