"""Ad-hoc signature writer, verified against ld's own output and codesign(1).

Three oracles, strongest first:

1. **Byte identity with ld**: re-signing an untouched ld-signed binary with
   the same identifier must reproduce ld's signature bytes exactly — every
   header field and every page hash.
2. **codesign(1)**: after pcc *changes the binary* (a cstring edit, which
   invalidates a __TEXT page hash) and re-signs it, `codesign --verify`
   accepts the result and the kernel runs it with the new behavior. The
   negative control proves the test can fail: the edited binary *without*
   re-signing is rejected by codesign.
3. Fail-closed on binaries outside the verified shape.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.macho_codesign import (
    CodesignError,
    build_signature,
    parse_signature,
    resign,
)

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)

SOURCE = r"""
#include <stdio.h>
static const char message[] = "AAAA";
int main(void) { printf("%s\n", message); return 0; }
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def _build(tmp_path: Path) -> Path:
    src = tmp_path / "prog.c"
    src.write_text(SOURCE, encoding="utf-8")
    binary = tmp_path / "prog"
    build = _run([_CC, str(src), "-o", str(binary)])
    assert build.returncode == 0, build.stderr
    return binary


def _linkedit_command(data: bytes):
    obj = spec.parse_object(data)
    return next(
        lc for lc in obj.commands
        if lc.cmd == spec.LC_SEGMENT_64
        and lc.body["segname_str"] == "__LINKEDIT"
    )


def test_resigning_an_untouched_binary_reproduces_lds_bytes(tmp_path):
    binary = _build(tmp_path)
    data = binary.read_bytes()
    params = parse_signature(data)
    resigned = resign(data)
    assert resigned == data, (
        "re-signing an unchanged ld-signed binary must be a byte-level "
        f"fixed point (identifier {params.identifier!r}); pcc's signature "
        "diverges from ld's"
    )


def test_pcc_resigns_a_modified_binary_and_codesign_accepts_it(tmp_path):
    binary = _build(tmp_path)
    data = binary.read_bytes()

    # Change program behavior: edit the cstring, which lives in a hashed
    # __TEXT page. The old signature is now invalid.
    assert data.count(b"AAAA\0") == 1
    edited = data.replace(b"AAAA\0", b"BBBB\0")

    tampered = tmp_path / "tampered"
    tampered.write_bytes(edited)
    tampered.chmod(0o755)
    reject = _run(["codesign", "--verify", str(tampered)])
    assert reject.returncode != 0, (
        "negative control failed: codesign accepted a modified, un-resigned "
        "binary — this test cannot prove anything"
    )

    fixed = tmp_path / "fixed"
    fixed.write_bytes(resign(edited))
    fixed.chmod(0o755)
    verify = _run(["codesign", "--verify", "--strict", str(fixed)])
    assert verify.returncode == 0, verify.stderr

    run = _run([str(fixed)])
    assert run.returncode == 0
    assert run.stdout.strip() == "BBBB", run.stdout


def test_resign_with_a_new_identifier_still_verifies(tmp_path):
    binary = _build(tmp_path)
    resigned = resign(binary.read_bytes(), identifier=b"pcc-owned-sig")
    out = tmp_path / "renamed"
    out.write_bytes(resigned)
    out.chmod(0o755)
    verify = _run(["codesign", "--verify", "--strict", str(out)])
    assert verify.returncode == 0, verify.stderr
    assert parse_signature(resigned).identifier == b"pcc-owned-sig"
    assert _run([str(out)]).returncode == 0


def test_fails_closed_outside_the_verified_shape(tmp_path):
    # An object file has no LC_CODE_SIGNATURE.
    src = tmp_path / "o.c"
    src.write_text("int f(void){return 1;}\n", encoding="utf-8")
    obj = tmp_path / "o.o"
    assert _run([_CC, "-c", str(src), "-o", str(obj)]).returncode == 0
    with pytest.raises(CodesignError):
        parse_signature(obj.read_bytes())

    # A developer-ID-style signature (special slots) is outside the shape;
    # simulate by corrupting nSpecialSlots in a real signature.
    binary = _build(tmp_path)
    data = bytearray(binary.read_bytes())
    params = parse_signature(bytes(data))
    # superblob at dataoff; CD at +20; nSpecialSlots at CD+24 (big-endian)
    struct.pack_into(">I", data, params.dataoff + 20 + 24, 7)
    with pytest.raises(CodesignError, match="special slots"):
        parse_signature(bytes(data))


def test_fails_closed_on_inconsistent_superblob_and_codedirectory_ranges(
    tmp_path,
):
    original = _build(tmp_path).read_bytes()
    params = parse_signature(original)
    cd = params.dataoff + 20
    hash_off = struct.unpack_from(">I", original, cd + 16)[0]

    # Each case corrupts one independently trusted length/count field. None
    # may escape as struct.error/ValueError or make the parser read a sibling
    # region as part of the signature.
    mutations = (
        (params.dataoff + 4, params.datasize + 1, "bad superblob length"),
        (params.dataoff + 8, 2, "only one CodeDirectory"),
        (cd + 4, params.datasize, "does not fill the superblob"),
        (cd + 16, hash_off + 1, "hash table length"),
        (cd + 28, 0, "code slots"),
        (cd + 32, params.dataoff - 1, "codeLimit"),
    )
    for offset, value, message in mutations:
        malformed = bytearray(original)
        struct.pack_into(">I", malformed, offset, value)
        with pytest.raises(CodesignError, match=message):
            parse_signature(bytes(malformed))

    command = spec.parse_object(original).command(spec.LC_CODE_SIGNATURE)
    assert command is not None
    truncated = bytearray(original[:params.dataoff + 8])
    struct.pack_into("<I", truncated, command.offset + 12, 8)
    with pytest.raises(CodesignError, match="truncated embedded-signature"):
        parse_signature(bytes(truncated))


def test_fails_closed_when_signature_range_is_not_the_file_tail(tmp_path):
    data = _build(tmp_path).read_bytes()
    with pytest.raises(CodesignError, match="end of the file"):
        parse_signature(data + b"trailing")

    malformed = bytearray(data)
    obj = spec.parse_object(data)
    command = obj.command(spec.LC_CODE_SIGNATURE)
    assert command is not None
    _dataoff, datasize = struct.unpack_from("<II", command.raw, 8)
    struct.pack_into("<I", malformed, command.offset + 12, datasize + 8)
    with pytest.raises(CodesignError, match="outside the file"):
        parse_signature(bytes(malformed))


def test_resign_requires_one_covering_linkedit_segment(tmp_path):
    original = _build(tmp_path).read_bytes()
    linkedit = _linkedit_command(original)

    missing = bytearray(original)
    missing[linkedit.offset + 8:linkedit.offset + 24] = b"__NOT_LINKEDIT\0\0"
    with pytest.raises(CodesignError, match="0 __LINKEDIT segments"):
        resign(bytes(missing))

    short = bytearray(original)
    struct.pack_into(
        "<Q",
        short,
        linkedit.offset + spec.SEGMENT_COMMAND_64.offset_of("filesize"),
        linkedit.body["filesize"] - 8,
    )
    with pytest.raises(CodesignError, match="does not end at the end"):
        resign(bytes(short))


def test_resign_rejects_identifier_and_linkedit_overflow(tmp_path):
    original = _build(tmp_path).read_bytes()
    params = parse_signature(original)
    for identifier in (b"", b"has\0nul"):
        with pytest.raises(CodesignError, match="identifier"):
            resign(original, identifier=identifier)

    # The signature is mapped by __LINKEDIT. Growing it beyond that segment's
    # declared VM extent must fail instead of writing a structurally
    # inconsistent executable.
    linkedit = _linkedit_command(original)
    tight = bytearray(original)
    struct.pack_into(
        "<Q",
        tight,
        linkedit.offset + spec.SEGMENT_COMMAND_64.offset_of("vmsize"),
        linkedit.body["filesize"],
    )
    with pytest.raises(CodesignError, match="does not fit in __LINKEDIT"):
        resign(
            bytes(tight),
            identifier=params.identifier + b"x" * 64,
        )


def test_build_signature_rejects_unverified_version_and_flags():
    kwargs = dict(
        identifier=b"pcc-test",
        exec_seg_base=0,
        exec_seg_limit=0,
        exec_seg_flags=0,
    )
    with pytest.raises(CodesignError, match="version"):
        build_signature(b"mach-o", version=0x20500, **kwargs)
    with pytest.raises(CodesignError, match="flags"):
        build_signature(b"mach-o", flags=0, **kwargs)
    with pytest.raises(CodesignError, match="unsigned 64-bit"):
        build_signature(
            b"mach-o", **dict(kwargs, exec_seg_limit=-1)
        )
    with pytest.raises(CodesignError, match="16-byte aligned"):
        build_signature(b"unaligned", **kwargs)
