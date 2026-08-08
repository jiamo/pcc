"""pcc-owned _FORTIFY_SOURCE wrappers: copy through, abort on overflow.

`__memcpy_chk` was the last libSystem import in the MEM-STR set. musl has no
_FORTIFY_SOURCE layer, so pcc/py_runtime/src/py_libc_fortify.c is pcc's own
implementation (LIBC-P2-MEM-STR). The platform contract is: perform the
operation when it fits the destination's known size, abort when it does not —
a silent short copy would turn a detected overflow into corrupted data.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FORTIFY_SRC = "pcc/py_runtime/src/py_libc_fortify.c"

PROBE_OK = r"""
#include <stdio.h>
#include <string.h>
#include <stddef.h>

void *__memcpy_chk(void *, const void *, size_t, size_t);
void *__memmove_chk(void *, const void *, size_t, size_t);
void *__memset_chk(void *, int, size_t, size_t);

int main(void) {
    char dst[32];
    const char *src = "0123456789abcdef";
    memset(dst, 0, sizeof(dst));
    __memcpy_chk(dst, src, 16, sizeof(dst));
    printf("copy=%s\n", dst);
    __memmove_chk(dst + 4, dst, 8, sizeof(dst) - 4);
    printf("move=%.12s\n", dst);
    __memset_chk(dst, 'x', 5, sizeof(dst));
    printf("set=%.5s\n", dst);
    /* exact fit must be allowed, not rejected */
    char tight[8];
    __memcpy_chk(tight, src, 8, 8);
    printf("tight=%.8s\n", tight);
    return 0;
}
"""

PROBE_OVERFLOW = r"""
#include <stddef.h>
void *__memcpy_chk(void *, const void *, size_t, size_t);
int main(void) {
    char dst[8];
    const char *src = "0123456789abcdef";
    __memcpy_chk(dst, src, 16, sizeof(dst));  /* must abort */
    return 0;
}
"""

EXPECTED = [
    "copy=0123456789abcdef",
    "move=0123012345678",
    "set=xxxxx",
    "tight=01234567",
]


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


REPO = _repo_root()


def _build(tmp_path: Path, name: str, source: str) -> Path:
    src = tmp_path / f"{name}.c"
    src.write_text(source, encoding="utf-8")
    from pcc.api import build

    artifact = build(
        [str(src), str(REPO / FORTIFY_SRC)],
        kind="exe",
        out_dir=str(tmp_path / name),
    )
    return Path(str(artifact.output_path))


def test_fortify_wrappers_copy_when_the_size_fits(tmp_path):
    binary = _build(tmp_path, "ok", PROBE_OK)
    out = subprocess.run([str(binary)], text=True, capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = out.stdout.strip().splitlines()
    assert got[0] == EXPECTED[0]
    assert got[2] == EXPECTED[2]
    assert got[3] == EXPECTED[3]
    # memmove overlap result is checked against Python's own semantics
    buf = bytearray(b"0123456789abcdef" + b"\x00" * 16)
    buf[4:12] = buf[0:8]
    assert got[1] == "move=" + buf[:12].decode()


def test_fortify_wrapper_aborts_when_the_copy_would_overflow(tmp_path):
    binary = _build(tmp_path, "overflow", PROBE_OVERFLOW)
    # Bytes, not text: the abort path writes a crash report through the
    # platform's handler and the captured stderr is not guaranteed ASCII.
    out = subprocess.run([str(binary)], capture_output=True, timeout=60)
    # SIGABRT (-6) or any non-zero exit is acceptable; silently returning 0 is not.
    assert out.returncode != 0, (
        "__memcpy_chk returned normally for a 16-byte copy into an 8-byte "
        "destination — the fortify check did not fire"
    )


def test_fortify_source_is_compiled_by_pcc_into_the_runtime_archives():
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(encoding="utf-8")
    assert "py_libc_fortify.c" in makefile
    assert "FORTIFY_OBJ_PCC" in makefile
    archive = REPO / "pcc" / "py_runtime" / "libpy_runtime_pcc_py.a"
    if archive.is_file():
        out = subprocess.run(
            ["nm", str(archive)], text=True, capture_output=True, timeout=120
        )
        assert "___memcpy_chk" in out.stdout
