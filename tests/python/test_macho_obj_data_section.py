"""Multi-section objects and UNSIGNED data relocations, differentially verified.

The pinned shape is real compiler output in miniature: a function returning
the address of a data-section table (`adrp/add` against a *defined* symbol),
and a `__DATA,__data` table containing two pointers into an external symbol
(one with an inline addend) plus one plain integer.

UNSIGNED differs from every instruction fixup already proven: length is 3
(8-byte pointer), and the addend lives IN the pointer bytes — `.quad _ext+16`
stores 16 in the data — not in an ADDEND companion entry. The differential
diff and the runtime check both pin that behavior.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.macho_obj import (
    DATA_SECTION_FLAGS,
    MachOEmitError,
    Relocation,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
)

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# _get_table: adrp x0,_table@PAGE ; add x0,x0,_table@PAGEOFF ; ret
TEXT = struct.pack("<3I", 0x90000000, 0x91000000, 0xD65F03C0)
# .quad _ext_target ; .quad _ext_target+16 ; .quad 42
DATA = struct.pack("<3q", 0, 16, 42)

SECTIONS = [
    Section(
        sectname="__text", segname="__TEXT", data=TEXT, align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_get_table", 0),),
        relocations=(
            Relocation(0, "_table", spec.ARM64_RELOC_PAGE21, pcrel=True),
            Relocation(4, "_table", spec.ARM64_RELOC_PAGEOFF12, pcrel=False),
        ),
    ),
    Section(
        sectname="__data", segname="__DATA", data=DATA, align_log2=3,
        flags=DATA_SECTION_FLAGS,
        symbols=(TextSymbol("_table", 0),),
        relocations=(
            Relocation(0, "_ext_target", spec.ARM64_RELOC_UNSIGNED,
                       pcrel=False, length=3),
            Relocation(8, "_ext_target", spec.ARM64_RELOC_UNSIGNED,
                       pcrel=False, length=3),
        ),
    ),
]

ASM = (
    "\t.section\t__TEXT,__text,regular,pure_instructions\n"
    "\t.globl\t_get_table\n\t.p2align\t2\n"
    "_get_table:\n"
    "\tadrp\tx0, _table@PAGE\n"
    "\tadd\tx0, x0, _table@PAGEOFF\n"
    "\tret\n"
    "\t.section\t__DATA,__data\n"
    "\t.globl\t_table\n\t.p2align\t3\n"
    "_table:\n"
    "\t.quad\t_ext_target\n"
    "\t.quad\t_ext_target+16\n"
    "\t.quad\t42\n"
    "\t.subsections_via_symbols\n"
)

MAIN_C = (
    "#include <stdint.h>\n"
    "extern int64_t *get_table(void);\n"
    "int64_t ext_target[4] = {100, 101, 102, 103};\n"
    "int main(void) {\n"
    "    int64_t *t = get_table();\n"
    "    if (*(int64_t **)&t[0] != ext_target) return 1;\n"
    "    if (*(int64_t **)&t[1] != &ext_target[2]) return 2;\n"  # +16 bytes
    "    if (t[2] != 42) return 3;\n"
    "    return 0;\n"
    "}\n"
)


def _emit(tmp_path: Path) -> Path:
    obj = tmp_path / "data_pcc.o"
    obj.write_bytes(
        macho_obj.emit_object(SECTIONS, undefined=["_ext_target"])
    )
    return obj


def _as_object(tmp_path: Path) -> Path:
    asm = tmp_path / "data.s"
    asm.write_text(ASM, encoding="utf-8")
    obj = tmp_path / "data_as.o"
    build = _run(["xcrun", "as", "-o", str(obj), str(asm)])
    assert build.returncode == 0, build.stderr
    return obj


def _named_relocs_per_section(path: Path):
    obj = spec.parse_object(path.read_bytes())
    names = [s["name"] for s in obj.symbols()]
    out = {}
    for sec in obj.sections():
        entries = []
        for r in obj.relocations(sec):
            target = (
                r["r_symbolnum"]
                if r["r_type"] == spec.ARM64_RELOC_ADDEND
                else names[r["r_symbolnum"]]
            )
            entries.append((
                r["r_address"], target, r["r_type"],
                r["r_pcrel"], r["r_length"], r["r_extern"],
            ))
        out[sec["sectname_str"]] = entries
    return out


def test_relocation_tables_match_as_per_section(tmp_path):
    ours = _named_relocs_per_section(_emit(tmp_path))
    theirs = _named_relocs_per_section(_as_object(tmp_path))
    assert ours == theirs, f"\n  pcc: {ours}\n  as:  {theirs}"


def test_section_layout_matches_as(tmp_path):
    ours = spec.parse_object(_emit(tmp_path).read_bytes())
    theirs = spec.parse_object(_as_object(tmp_path).read_bytes())
    for our_sec, their_sec in zip(ours.sections(), theirs.sections()):
        for name in ("sectname_str", "segname_str", "addr", "size", "align",
                     "flags", "nreloc"):
            assert our_sec[name] == their_sec[name], (
                f"section.{name}: pcc {our_sec[name]}, as {their_sec[name]}"
            )
    # Defined symbol addresses (vm addrs) must agree, ltmp labels aside.
    def real_symbols(obj):
        return {
            s["name"]: (s["n_type"], s["n_sect"], s["n_value"])
            for s in obj.symbols() if not s["name"].startswith("ltmp")
        }
    assert real_symbols(ours) == real_symbols(theirs)


def test_both_objects_link_and_behave_identically(tmp_path):
    for tag, obj in (("pcc", _emit(tmp_path)), ("as", _as_object(tmp_path))):
        main_c = tmp_path / f"main_{tag}.c"
        main_c.write_text(MAIN_C, encoding="utf-8")
        binary = tmp_path / f"prog_{tag}"
        link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
        assert link.returncode == 0, f"{tag}: {link.stderr}"
        rc = _run([str(binary)]).returncode
        assert rc == 0, f"{tag}: table contents wrong at runtime (rc={rc})"


def test_unsigned_addend_lives_in_the_data_not_a_companion(tmp_path):
    """The +16 must be the pointer bytes; an ADDEND entry would be wrong."""
    obj = spec.parse_object(_emit(tmp_path).read_bytes())
    data_sec = next(s for s in obj.sections() if s["sectname_str"] == "__data")
    payload = obj.data[data_sec["offset"]:data_sec["offset"] + data_sec["size"]]
    assert struct.unpack("<3q", payload) == (0, 16, 42)
    types = {r["r_type"] for r in obj.relocations(data_sec)}
    assert types == {spec.ARM64_RELOC_UNSIGNED}, types


def test_unsigned_fails_closed_outside_the_proven_shape():
    with pytest.raises(MachOEmitError):
        # explicit addend on UNSIGNED: it belongs in the data bytes
        macho_obj.emit_object([
            SECTIONS[0],
            Section(
                sectname="__data", segname="__DATA", data=DATA,
                symbols=(TextSymbol("_table", 0),),
                relocations=(Relocation(
                    0, "_ext_target", spec.ARM64_RELOC_UNSIGNED,
                    pcrel=False, length=3, addend=16,
                ),),
            ),
        ], undefined=["_ext_target"])
    with pytest.raises(MachOEmitError):
        # 4-byte UNSIGNED is not proven
        macho_obj.emit_object([
            SECTIONS[0],
            Section(
                sectname="__data", segname="__DATA", data=DATA,
                symbols=(TextSymbol("_table", 0),),
                relocations=(Relocation(
                    0, "_ext_target", spec.ARM64_RELOC_UNSIGNED,
                    pcrel=False, length=2,
                ),),
            ),
        ], undefined=["_ext_target"])
    with pytest.raises(MachOEmitError):
        macho_obj.emit_object([], undefined=[])
    with pytest.raises(MachOEmitError):
        macho_obj.emit_object([SECTIONS[0], SECTIONS[0]])  # duplicate names
