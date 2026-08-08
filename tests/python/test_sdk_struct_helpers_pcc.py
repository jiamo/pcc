"""pcc compiles the SDK-struct kernel helpers with SDK-exact layouts.

py_os_rss.c / py_os_heap.c were the last two runtime sources pcc could not
compile: they read macOS SDK structs (mach_task_basic_info,
malloc_statistics_t, struct rusage) that the fake libc headers did not
declare. The fake declarations added for them are locked here field-by-field
against the host SDK via a cc-compiled oracle, and the mach/malloc queries
are executed for real from a pcc-compiled binary (LIBC-P2-SDK-STRUCT-HELPERS).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROBE = r"""
#include <mach/mach.h>
#include <malloc/malloc.h>
#include <sys/resource.h>
#include <stdio.h>
#include <stddef.h>

int main(void) {
    printf("mtbi_size=%lu\n", (unsigned long)sizeof(struct mach_task_basic_info));
    printf("mtbi_resident_off=%lu\n",
           (unsigned long)offsetof(struct mach_task_basic_info, resident_size));
    printf("mtbi_count=%lu\n", (unsigned long)MACH_TASK_BASIC_INFO_COUNT);
    printf("mtbi_flavor=%d\n", (int)MACH_TASK_BASIC_INFO);
    printf("kern_success=%d\n", (int)KERN_SUCCESS);
    printf("mstat_size=%lu\n", (unsigned long)sizeof(malloc_statistics_t));
    printf("mstat_in_use_off=%lu\n",
           (unsigned long)offsetof(malloc_statistics_t, size_in_use));
    printf("mstat_max_off=%lu\n",
           (unsigned long)offsetof(malloc_statistics_t, max_size_in_use));
    printf("mstat_alloc_off=%lu\n",
           (unsigned long)offsetof(malloc_statistics_t, size_allocated));
    printf("rusage_size=%lu\n", (unsigned long)sizeof(struct rusage));
    printf("maxrss_off=%lu\n", (unsigned long)offsetof(struct rusage, ru_maxrss));
    printf("rusage_self=%d\n", (int)RUSAGE_SELF);

    struct mach_task_basic_info info;
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    kern_return_t kr = task_info(
        mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &count);
    printf("rss_query_ok=%d\n", kr == KERN_SUCCESS ? 1 : 0);
    printf("rss_over_1mb=%d\n",
           kr == KERN_SUCCESS && info.resident_size > 1024 * 1024 ? 1 : 0);

    malloc_statistics_t stats;
    malloc_zone_statistics((malloc_zone_t *)0, &stats);
    printf("heap_in_use_positive=%d\n", stats.size_in_use > 0 ? 1 : 0);

    struct rusage ru;
    int rc = getrusage(RUSAGE_SELF, &ru);
    printf("peak_rss_over_1mb=%d\n",
           rc == 0 && ru.ru_maxrss > 1024 * 1024 ? 1 : 0);
    return 0;
}
"""


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


def _run(binary: Path) -> dict[str, str]:
    out = subprocess.run(
        [str(binary)], text=True, capture_output=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    values: dict[str, str] = {}
    for line in out.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return values


def test_sdk_struct_helpers_match_cc_oracle_and_query_for_real(tmp_path):
    src = tmp_path / "probe.c"
    src.write_text(PROBE, encoding="utf-8")
    env = os.environ.copy()
    env.pop("LC_ALL", None)

    cc_bin = tmp_path / "probe_cc"
    cc = subprocess.run(
        ["cc", "-o", str(cc_bin), str(src)],
        text=True, capture_output=True, timeout=120, env=env,
    )
    assert cc.returncode == 0, cc.stderr

    from pcc.api import build

    artifact = build(str(src), kind="exe", out_dir=str(tmp_path))
    pcc_bin = Path(str(artifact.output_path))
    assert pcc_bin.is_file()

    oracle = _run(cc_bin)
    ours = _run(pcc_bin)
    assert ours == oracle
    assert ours["rss_query_ok"] == "1"
    assert ours["rss_over_1mb"] == "1"
    assert ours["heap_in_use_positive"] == "1"
    assert ours["peak_rss_over_1mb"] == "1"
