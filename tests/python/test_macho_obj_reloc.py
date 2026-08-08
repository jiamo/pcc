"""LINK-P1-MACHO-OBJ-RELOC first slice: BRANCH26, PAGE21, PAGEOFF12.

Each type is differentially verified the same way: pcc emits an object with
machine code whose fixup fields are zero-filled, as(1) assembles the
equivalent asm, the relocation tables must match entry by entry (symbol
resolved by name, since indices legitimately differ), and both objects must
link against the same main and produce the same runtime behavior.

The three types here are the extern-target instruction fixups — the ones a
compiled call (`bl extern`) and a global address (`adrp/add extern@PAGE...`)
need. The remaining paired/data/TLV shapes live in
``test_macho_obj_reloc_remaining.py``; every shape outside those explicit
slices still fails closed in the writer.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.macho_obj import MachOEmitError, Relocation, TextSymbol

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# The pinned shape: a function calling an extern, and a function loading an
# extern int. Fixup fields zero-filled — the relocations carry the target.
#   _call_ext: stp x29,x30,[sp,#-16]! ; bl _ext_fn ; ldp x29,x30,[sp],#16 ; ret
#   _load_ext: adrp x0,_ext_data@PAGE ; add x0,x0,@PAGEOFF ; ldr w0,[x0] ; ret
TEXT = struct.pack(
    "<8I",
    0xA9BF7BFD, 0x94000000, 0xA8C17BFD, 0xD65F03C0,
    0x90000000, 0x91000000, 0xB9400000, 0xD65F03C0,
)
SYMBOLS = [TextSymbol("_call_ext", 0), TextSymbol("_load_ext", 16)]
UNDEFINED = ["_ext_fn", "_ext_data"]
RELOCATIONS = [
    Relocation(4, "_ext_fn", spec.ARM64_RELOC_BRANCH26, pcrel=True),
    Relocation(16, "_ext_data", spec.ARM64_RELOC_PAGE21, pcrel=True),
    Relocation(20, "_ext_data", spec.ARM64_RELOC_PAGEOFF12, pcrel=False),
]

ASM = (
    "\t.section\t__TEXT,__text,regular,pure_instructions\n"
    "\t.globl\t_call_ext\n\t.p2align\t2\n"
    "_call_ext:\n"
    "\tstp\tx29, x30, [sp, #-16]!\n"
    "\tbl\t_ext_fn\n"
    "\tldp\tx29, x30, [sp], #16\n"
    "\tret\n"
    "\t.globl\t_load_ext\n\t.p2align\t2\n"
    "_load_ext:\n"
    "\tadrp\tx0, _ext_data@PAGE\n"
    "\tadd\tx0, x0, _ext_data@PAGEOFF\n"
    "\tldr\tw0, [x0]\n"
    "\tret\n"
    "\t.subsections_via_symbols\n"
)

MAIN_C = (
    "extern int call_ext(void);\n"
    "extern int load_ext(void);\n"
    "int ext_data = 1234;\n"
    "int ext_fn(void) { return 42; }\n"
    "int main(void) {\n"
    "    if (call_ext() != 42) return 1;\n"
    "    if (load_ext() != 1234) return 2;\n"
    "    return 0;\n"
    "}\n"
)


def _emit(tmp_path: Path) -> Path:
    obj = tmp_path / "reloc_pcc.o"
    obj.write_bytes(
        macho_obj.emit_text_object(
            TEXT, SYMBOLS, undefined=UNDEFINED, relocations=RELOCATIONS
        )
    )
    return obj


def _as_object(tmp_path: Path) -> Path:
    asm = tmp_path / "reloc.s"
    asm.write_text(ASM, encoding="utf-8")
    obj = tmp_path / "reloc_as.o"
    build = _run(["xcrun", "as", "-o", str(obj), str(asm)])
    assert build.returncode == 0, build.stderr
    return obj


def _named_relocs(path: Path):
    """(r_address, symbol-name, type, pcrel, length, extern) per entry."""
    obj = spec.parse_object(path.read_bytes())
    names = [s["name"] for s in obj.symbols()]
    out = []
    for r in obj.relocations(obj.sections()[0]):
        out.append((
            r["r_address"], names[r["r_symbolnum"]], r["r_type"],
            r["r_pcrel"], r["r_length"], r["r_extern"],
        ))
    return out


def _link_and_run(tmp_path: Path, obj: Path, tag: str) -> int:
    main_c = tmp_path / f"main_{tag}.c"
    main_c.write_text(MAIN_C, encoding="utf-8")
    binary = tmp_path / f"prog_{tag}"
    link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
    assert link.returncode == 0, link.stderr
    return _run([str(binary)]).returncode


def test_relocation_tables_match_as_entry_by_entry(tmp_path):
    ours = _named_relocs(_emit(tmp_path))
    theirs = _named_relocs(_as_object(tmp_path))
    assert ours == theirs, f"\n  pcc: {ours}\n  as:  {theirs}"


def test_both_objects_link_and_behave_identically(tmp_path):
    rc_pcc = _link_and_run(tmp_path, _emit(tmp_path), "pcc")
    rc_as = _link_and_run(tmp_path, _as_object(tmp_path), "as")
    assert rc_pcc == rc_as == 0, (rc_pcc, rc_as)


def test_undefined_symbols_appear_in_the_undef_partition(tmp_path):
    obj = spec.parse_object(_emit(tmp_path).read_bytes())
    undefined = {
        s["name"] for s in obj.symbols()
        if (s["n_type"] & spec.N_TYPE) == spec.N_UNDF
    }
    assert undefined == set(UNDEFINED), undefined
    dysym = obj.command(spec.LC_DYSYMTAB).body
    assert dysym["nextdefsym"] == len(SYMBOLS)
    assert dysym["nundefsym"] == len(UNDEFINED)


def test_emitted_object_still_roundtrips(tmp_path):
    data = _emit(tmp_path).read_bytes()
    obj = spec.parse_object(data)
    covered = spec.MACH_HEADER_64.size + obj.header["sizeofcmds"]
    assert obj.pack() == data[:covered]


# --- second slice: GOT_LOAD pair and ADDEND ---------------------------------

# _got_load:    adrp x0,_ext_data@GOTPAGE ; ldr x0,[x0,@GOTPAGEOFF]
#               ldr w0,[x0] ; ret
# _addend_load: adrp x0,(_ext_arr+8)@PAGE ; add x0,x0,(_ext_arr+8)@PAGEOFF
#               ldr w0,[x0] ; ret
TEXT2 = struct.pack(
    "<8I",
    0x90000000, 0xF9400000, 0xB9400000, 0xD65F03C0,
    0x90000000, 0x91000000, 0xB9400000, 0xD65F03C0,
)
SYMBOLS2 = [TextSymbol("_got_load", 0), TextSymbol("_addend_load", 16)]
UNDEFINED2 = ["_ext_data", "_ext_arr"]
RELOCATIONS2 = [
    Relocation(0, "_ext_data", spec.ARM64_RELOC_GOT_LOAD_PAGE21, pcrel=True),
    Relocation(4, "_ext_data", spec.ARM64_RELOC_GOT_LOAD_PAGEOFF12, pcrel=False),
    Relocation(16, "_ext_arr", spec.ARM64_RELOC_PAGE21, pcrel=True, addend=8),
    Relocation(20, "_ext_arr", spec.ARM64_RELOC_PAGEOFF12, pcrel=False, addend=8),
]

ASM2 = (
    "\t.section\t__TEXT,__text,regular,pure_instructions\n"
    "\t.globl\t_got_load\n\t.p2align\t2\n"
    "_got_load:\n"
    "\tadrp\tx0, _ext_data@GOTPAGE\n"
    "\tldr\tx0, [x0, _ext_data@GOTPAGEOFF]\n"
    "\tldr\tw0, [x0]\n"
    "\tret\n"
    "\t.globl\t_addend_load\n\t.p2align\t2\n"
    "_addend_load:\n"
    "\tadrp\tx0, (_ext_arr+8)@PAGE\n"
    "\tadd\tx0, x0, (_ext_arr+8)@PAGEOFF\n"
    "\tldr\tw0, [x0]\n"
    "\tret\n"
    "\t.subsections_via_symbols\n"
)

MAIN2_C = (
    "extern int got_load(void);\n"
    "extern int addend_load(void);\n"
    "int ext_data = 777;\n"
    "int ext_arr[4] = {10, 11, 12, 13};\n"
    "int main(void) {\n"
    "    if (got_load() != 777) return 1;\n"
    "    if (addend_load() != 12) return 2;\n"  # +8 bytes = ext_arr[2]
    "    return 0;\n"
    "}\n"
)


def _named_relocs_of(path: Path):
    """Like _named_relocs, but ADDEND entries keep their value, not a name."""
    obj = spec.parse_object(path.read_bytes())
    names = [s["name"] for s in obj.symbols()]
    out = []
    for r in obj.relocations(obj.sections()[0]):
        target = (
            r["r_symbolnum"]
            if r["r_type"] == spec.ARM64_RELOC_ADDEND
            else names[r["r_symbolnum"]]
        )
        out.append((
            r["r_address"], target, r["r_type"],
            r["r_pcrel"], r["r_length"], r["r_extern"],
        ))
    return out


def test_got_load_and_addend_tables_match_as(tmp_path):
    ours = tmp_path / "reloc2_pcc.o"
    ours.write_bytes(macho_obj.emit_text_object(
        TEXT2, SYMBOLS2, undefined=UNDEFINED2, relocations=RELOCATIONS2
    ))
    asm = tmp_path / "reloc2.s"
    asm.write_text(ASM2, encoding="utf-8")
    theirs = tmp_path / "reloc2_as.o"
    build = _run(["xcrun", "as", "-o", str(theirs), str(asm)])
    assert build.returncode == 0, build.stderr

    assert _named_relocs_of(ours) == _named_relocs_of(theirs), (
        f"\n  pcc: {_named_relocs_of(ours)}\n  as:  {_named_relocs_of(theirs)}"
    )


def test_got_load_and_addend_link_and_run(tmp_path):
    for tag, obj_bytes in (
        ("pcc", macho_obj.emit_text_object(
            TEXT2, SYMBOLS2, undefined=UNDEFINED2, relocations=RELOCATIONS2
        )),
    ):
        obj = tmp_path / f"reloc2_{tag}.o"
        obj.write_bytes(obj_bytes)
        main_c = tmp_path / f"main2_{tag}.c"
        main_c.write_text(MAIN2_C, encoding="utf-8")
        binary = tmp_path / f"prog2_{tag}"
        link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
        assert link.returncode == 0, link.stderr
        rc = _run([str(binary)]).returncode
        assert rc == 0, (
            f"{tag}: GOT load or addend addressing produced wrong data (rc={rc})"
        )


def test_addend_fails_closed_outside_the_proven_shape():
    with pytest.raises(macho_obj.MachOEmitError):
        # addend on BRANCH26 is not differentially proven
        macho_obj.emit_text_object(
            TEXT2, SYMBOLS2, undefined=UNDEFINED2,
            relocations=[Relocation(
                4, "_ext_data", spec.ARM64_RELOC_BRANCH26, pcrel=True, addend=4
            )],
        )
    with pytest.raises(macho_obj.MachOEmitError):
        # 24-bit field: out-of-range addend must not be silently truncated
        macho_obj.emit_text_object(
            TEXT2, SYMBOLS2, undefined=UNDEFINED2,
            relocations=[Relocation(
                16, "_ext_arr", spec.ARM64_RELOC_PAGE21, pcrel=True,
                addend=0x800000,
            )],
        )


def test_unproven_relocation_shapes_fail_closed():
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(
            TEXT, SYMBOLS, undefined=UNDEFINED,
            relocations=[Relocation(4, "_ext_fn", spec.ARM64_RELOC_ADDEND, pcrel=False)],
        )
    with pytest.raises(MachOEmitError):
        # BRANCH26 with the wrong pcrel is a corrupt fixup, not a variant.
        macho_obj.emit_text_object(
            TEXT, SYMBOLS, undefined=UNDEFINED,
            relocations=[Relocation(4, "_ext_fn", spec.ARM64_RELOC_BRANCH26, pcrel=False)],
        )
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(
            TEXT, SYMBOLS, undefined=UNDEFINED,
            relocations=[Relocation(4, "_nope", spec.ARM64_RELOC_BRANCH26, pcrel=True)],
        )
    with pytest.raises(MachOEmitError):
        macho_obj.emit_text_object(
            TEXT, SYMBOLS, undefined=["_call_ext"],  # defined AND undefined
        )
