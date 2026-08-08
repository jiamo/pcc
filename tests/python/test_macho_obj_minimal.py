"""LINK-P1-MACHO-OBJ-MINIMAL acceptance: pcc-emitted .o, no as(1) involved.

The row requires two proofs, both here:

1. **The system linker consumes pcc's .o and the binary runs** — a leaf
   function emitted by `pcc.backend.macho_obj` is linked by ld against a
   cc-built main, executed, and its return value checked.
2. **Field-level equivalence with as(1)** — the same machine code assembled
   by as(1) is parsed with `macho_spec` and compared field by field
   (byte-identity is explicitly not the bar: as adds an ltmp local label and
   its own build-version numbers; every divergence the diff allows is named).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.macho_obj import MachOEmitError, TextSymbol

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# mov w0, #42 ; ret  — a leaf: no calls, no globals, no relocations.
LEAF_CODE = struct.pack("<II", 0x52800540, 0xD65F03C0)
LEAF_ASM = (
    "\t.section\t__TEXT,__text,regular,pure_instructions\n"
    "\t.globl\t_leaf42\n"
    "\t.p2align\t2\n"
    "_leaf42:\n"
    "\tmov\tw0, #42\n"
    "\tret\n"
    "\t.subsections_via_symbols\n"
)


def _emit(tmp_path: Path) -> Path:
    obj = tmp_path / "leaf_pcc.o"
    obj.write_bytes(
        macho_obj.emit_text_object(LEAF_CODE, [TextSymbol("_leaf42", 0)])
    )
    return obj


# --- proof 1: ld links it and the binary runs -------------------------------


def test_system_linker_consumes_the_object_and_the_binary_runs(tmp_path):
    obj = _emit(tmp_path)
    main_c = tmp_path / "main.c"
    main_c.write_text(
        "extern int leaf42(void);\n"
        "int main(void) { return leaf42() == 42 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    binary = tmp_path / "prog"
    link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
    assert link.returncode == 0, link.stderr
    run = _run([str(binary)])
    assert run.returncode == 0, f"leaf42() did not return 42 (rc={run.returncode})"


def test_nm_and_otool_agree_on_the_emitted_object(tmp_path):
    obj = _emit(tmp_path)
    nm = _run(["xcrun", "nm", str(obj)])
    assert nm.returncode == 0, nm.stderr
    assert "T _leaf42" in nm.stdout, nm.stdout
    otool = _run(["xcrun", "otool", "-lv", str(obj)])
    assert otool.returncode == 0, otool.stderr
    assert "sectname __text" in otool.stdout
    assert "PURE_INSTRUCTIONS" in otool.stdout


# --- proof 2: field-level diff against as(1) --------------------------------

# Divergences from as(1) output this diff deliberately allows. Anything not
# listed here must match exactly.
#   - as emits an `ltmp0` local label; pcc emits only the real symbol, so
#     symbol counts/indices and dysymtab local-vs-extdef partitions differ.
#   - as stamps the running OS's minos and no sdk; pcc pins minos itself.
#   - file offsets follow from the symbol-count difference.
_ALLOWED_SECTION_DIVERGENCES = {"offset"}
_ALLOWED_BUILD_VERSION_DIVERGENCES = {"minos", "sdk"}


def _as_object(tmp_path: Path) -> Path:
    asm = tmp_path / "leaf.s"
    asm.write_text(LEAF_ASM, encoding="utf-8")
    obj = tmp_path / "leaf_as.o"
    build = _run(["xcrun", "as", "-o", str(obj), str(asm)])
    assert build.returncode == 0, build.stderr
    return obj


def test_field_level_equivalence_with_as(tmp_path):
    ours = spec.parse_object(_emit(tmp_path).read_bytes())
    theirs = spec.parse_object(_as_object(tmp_path).read_bytes())

    problems: list[str] = []

    for name in ("magic", "cputype", "cpusubtype", "filetype", "flags"):
        if ours.header[name] != theirs.header[name]:
            problems.append(
                f"header.{name}: pcc {ours.header[name]:#x}, as {theirs.header[name]:#x}"
            )
    if [lc.cmd for lc in ours.commands] != [lc.cmd for lc in theirs.commands]:
        problems.append(
            f"load-command kinds: pcc {[hex(lc.cmd) for lc in ours.commands]}, "
            f"as {[hex(lc.cmd) for lc in theirs.commands]}"
        )

    our_sec = ours.sections()[0]
    their_sec = theirs.sections()[0]
    for name, _fmt in spec.SECTION_64.fields:
        if name in ("sectname", "segname"):
            continue
        if name in _ALLOWED_SECTION_DIVERGENCES:
            continue
        if our_sec[name] != their_sec[name]:
            problems.append(
                f"section.{name}: pcc {our_sec[name]}, as {their_sec[name]}"
            )
    if (our_sec["sectname_str"], our_sec["segname_str"]) != (
        their_sec["sectname_str"], their_sec["segname_str"],
    ):
        problems.append("section names differ")

    bv_ours = ours.command(spec.LC_BUILD_VERSION).body
    bv_theirs = theirs.command(spec.LC_BUILD_VERSION).body
    for name in ("platform", "ntools"):
        if bv_ours[name] != bv_theirs[name]:
            problems.append(
                f"build_version.{name}: pcc {bv_ours[name]}, as {bv_theirs[name]}"
            )

    # The real symbol must agree exactly; as's extra ltmp0 is the allowed delta.
    def real_symbols(obj):
        return {
            s["name"]: (s["n_type"], s["n_sect"], s["n_value"])
            for s in obj.symbols()
            if not s["name"].startswith("ltmp")
        }

    if real_symbols(ours) != real_symbols(theirs):
        problems.append(
            f"symbols: pcc {real_symbols(ours)}, as {real_symbols(theirs)}"
        )

    # Same machine code bytes in both payloads.
    def text_payload(obj, sec):
        return obj.data[sec["offset"]:sec["offset"] + sec["size"]]

    if text_payload(ours, our_sec) != text_payload(theirs, their_sec):
        problems.append("__text payload bytes differ")

    assert not problems, "\n  ".join(
        ["pcc-emitted object diverges from as(1) beyond the allowed set:"]
        + problems
    )


def test_emitted_object_roundtrips_through_the_spec_parser(tmp_path):
    data = _emit(tmp_path).read_bytes()
    obj = spec.parse_object(data)
    covered = spec.MACH_HEADER_64.size + obj.header["sizeofcmds"]
    assert obj.pack() == data[:covered]
    relocs = obj.relocations(obj.sections()[0])
    assert relocs == [], "the minimal object must have no relocations"


# --- fail-closed ------------------------------------------------------------


def test_rejects_shapes_outside_the_proven_subset():
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(b"", [TextSymbol("_x", 0)])
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(b"\x00" * 6, [TextSymbol("_x", 0)])  # not /4
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(LEAF_CODE, [])
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(LEAF_CODE, [TextSymbol("_x", 999)])
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(
            LEAF_CODE, [TextSymbol("_x", 0), TextSymbol("_x", 4)]
        )
