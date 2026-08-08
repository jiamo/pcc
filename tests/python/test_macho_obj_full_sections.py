"""cstring / const / zerofill sections and local symbols, differentially verified.

The pinned shape is the rest of what a real compiled C file produces: a
function addressing a local (non-exported) cstring label, a `__TEXT,__const`
table, and a zerofill `__DATA,__bss` variable. Three semantics get proven
here beyond the earlier suites:

- a **local symbol** (`l_.str`) in the locals partition that is still the
  target of PAGE21/PAGEOFF12 relocations — r_extern=1 means "symbolnum is a
  symtab index", not "the symbol is exported";
- a **zerofill** section with file offset 0 and no payload bytes, occupying
  vm space only;
- vmaddr accumulation across four sections with mixed alignments (2, 0, 3, 3).
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
    CSTRING_SECTION_FLAGS,
    DATA_SECTION_FLAGS,
    MachOEmitError,
    Relocation,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
    ZEROFILL_SECTION_FLAGS,
)

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# _get_msg: adrp x0,l_.str@PAGE ; add x0,x0,l_.str@PAGEOFF ; ret
TEXT = struct.pack("<3I", 0x90000000, 0x91000000, 0xD65F03C0)
CSTRING = b"hello\0"
CONST = struct.pack("<2q", 7, 9)

SECTIONS = [
    Section(
        sectname="__text", segname="__TEXT", data=TEXT, align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_get_msg", 0),),
        relocations=(
            Relocation(0, "l_.str", spec.ARM64_RELOC_PAGE21, pcrel=True),
            Relocation(4, "l_.str", spec.ARM64_RELOC_PAGEOFF12, pcrel=False),
        ),
    ),
    Section(
        sectname="__cstring", segname="__TEXT", data=CSTRING, align_log2=0,
        flags=CSTRING_SECTION_FLAGS,
        symbols=(TextSymbol("l_.str", 0, external=False),),
    ),
    Section(
        sectname="__const", segname="__TEXT", data=CONST, align_log2=3,
        flags=DATA_SECTION_FLAGS,
        symbols=(TextSymbol("_ktable", 0),),
    ),
    Section(
        sectname="__bss", segname="__DATA", align_log2=3,
        flags=ZEROFILL_SECTION_FLAGS,
        symbols=(TextSymbol("_counter", 0),),
        zerofill_size=8,
    ),
]

ASM = (
    "\t.section\t__TEXT,__text,regular,pure_instructions\n"
    "\t.globl\t_get_msg\n\t.p2align\t2\n"
    "_get_msg:\n"
    "\tadrp\tx0, l_.str@PAGE\n"
    "\tadd\tx0, x0, l_.str@PAGEOFF\n"
    "\tret\n"
    "\t.section\t__TEXT,__cstring,cstring_literals\n"
    "l_.str:\n"
    '\t.asciz\t"hello"\n'
    "\t.section\t__TEXT,__const\n"
    "\t.globl\t_ktable\n\t.p2align\t3\n"
    "_ktable:\n"
    "\t.quad\t7\n"
    "\t.quad\t9\n"
    "\t.globl\t_counter\n"
    ".zerofill __DATA,__bss,_counter,8,3\n"
    "\t.subsections_via_symbols\n"
)

MAIN_C = (
    "#include <string.h>\n"
    "#include <stdint.h>\n"
    "extern const char *get_msg(void);\n"
    "extern const int64_t ktable[2];\n"
    "extern int64_t counter;\n"
    "int main(void) {\n"
    '    if (strcmp(get_msg(), "hello") != 0) return 1;\n'
    "    if (ktable[0] != 7 || ktable[1] != 9) return 2;\n"
    "    if (counter != 0) return 3;\n"
    "    counter = 5;\n"
    "    return counter == 5 ? 0 : 4;\n"
    "}\n"
)


def _emit(tmp_path: Path) -> Path:
    obj = tmp_path / "full_pcc.o"
    obj.write_bytes(macho_obj.emit_object(SECTIONS))
    return obj


def _as_object(tmp_path: Path) -> Path:
    asm = tmp_path / "full.s"
    asm.write_text(ASM, encoding="utf-8")
    obj = tmp_path / "full_as.o"
    build = _run(["xcrun", "as", "-o", str(obj), str(asm)])
    assert build.returncode == 0, build.stderr
    return obj


def test_section_layout_matches_as(tmp_path):
    ours = spec.parse_object(_emit(tmp_path).read_bytes())
    theirs = spec.parse_object(_as_object(tmp_path).read_bytes())
    assert len(ours.sections()) == len(theirs.sections()) == 4
    for our_sec, their_sec in zip(ours.sections(), theirs.sections()):
        for name in ("sectname_str", "segname_str", "addr", "size", "align",
                     "flags", "nreloc"):
            assert our_sec[name] == their_sec[name], (
                f"{our_sec['sectname_str']}.{name}: "
                f"pcc {our_sec[name]}, as {their_sec[name]}"
            )
    bss = next(s for s in ours.sections() if s["sectname_str"] == "__bss")
    assert bss["offset"] == 0, "zerofill must have no file payload"


def test_symbols_match_as_with_the_local_in_the_locals_partition(tmp_path):
    ours = spec.parse_object(_emit(tmp_path).read_bytes())
    theirs = spec.parse_object(_as_object(tmp_path).read_bytes())

    def real_symbols(obj):
        return {
            s["name"]: (s["n_type"], s["n_sect"], s["n_value"])
            for s in obj.symbols() if not s["name"].startswith("ltmp")
        }

    assert real_symbols(ours) == real_symbols(theirs)
    # l_.str is N_SECT without N_EXT, and dysymtab counts it as a local.
    lstr = next(s for s in ours.symbols() if s["name"] == "l_.str")
    assert lstr["n_type"] == spec.N_SECT
    dysym = ours.command(spec.LC_DYSYMTAB).body
    assert dysym["nlocalsym"] == 1
    assert dysym["nextdefsym"] == 3


def test_relocations_against_the_local_symbol_match_as(tmp_path):
    def named(path):
        obj = spec.parse_object(path.read_bytes())
        names = [s["name"] for s in obj.symbols()]
        return [
            (r["r_address"], names[r["r_symbolnum"]], r["r_type"],
             r["r_pcrel"], r["r_length"], r["r_extern"])
            for r in obj.relocations(obj.sections()[0])
        ]

    assert named(_emit(tmp_path)) == named(_as_object(tmp_path))


def test_both_objects_link_and_behave_identically(tmp_path):
    for tag, obj in (("pcc", _emit(tmp_path)), ("as", _as_object(tmp_path))):
        main_c = tmp_path / f"main_{tag}.c"
        main_c.write_text(MAIN_C, encoding="utf-8")
        binary = tmp_path / f"prog_{tag}"
        link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
        assert link.returncode == 0, f"{tag}: {link.stderr}"
        rc = _run([str(binary)]).returncode
        assert rc == 0, f"{tag}: cstring/const/bss behavior wrong (rc={rc})"


def test_zerofill_fails_closed_outside_the_proven_shape():
    with pytest.raises(MachOEmitError):
        # payload on a zerofill section
        macho_obj.emit_object([
            SECTIONS[0],
            Section(sectname="__bss", segname="__DATA", data=b"\0" * 8,
                    flags=ZEROFILL_SECTION_FLAGS,
                    symbols=(TextSymbol("_c", 0),), zerofill_size=8),
        ])
    with pytest.raises(MachOEmitError):
        # content section after a zerofill section: ld would reject it
        macho_obj.emit_object([
            SECTIONS[0], SECTIONS[3], SECTIONS[2],
        ])
    with pytest.raises(MachOEmitError):
        # zerofill_size on a regular section
        macho_obj.emit_object([
            Section(sectname="__data", segname="__DATA", data=b"\1" * 8,
                    symbols=(TextSymbol("_d", 0),), zerofill_size=8),
        ])
