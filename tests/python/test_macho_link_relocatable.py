"""Static-link core, differentially verified against `ld -r`.

Two pcc-emitted objects that reference each other across the boundary (a
call, a data address, a GOT load, and a `.quad sym+offset` table) are merged
by `pcc.backend.macho_link` and by `ld -r`, and the results must agree on
section layout, symbols, and relocations. Then both merged objects go through
the final link and must run identically.

Cross-object resolution is the point: `_helper` is undefined in object A and
defined in object B, so after the merge it must no longer be undefined, and
A's call must target the merged definition.

The focused contract cases below also keep Mach-O's symbol-table-index
provenance explicit: a local and an external may share a spelling without
resolving to each other, while a genuinely unresolved external survives for
the final link.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_link import LinkError, link_relocatable

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# Object A: calls _helper (defined in B), addresses its own _table, and holds
# a pointer table naming B's _bmarker.
UNIT_A = """\
.section __DATA,__data
.p2align 3
.globl _table
_table:
  .quad 10
  .quad 20
.section __DATA,__data
.p2align 3
.globl _ptrs
_ptrs:
  .quad _bmarker
  .quad _table+8
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _use
_use:
  paciasp
  stp x29, x30, [sp, #-16]!
  mov x29, sp
  movz w0, #5
  bl _helper
  adrp x9, _table@PAGE
  add x9, x9, _table@PAGEOFF
  ldur x10, [x9, #8]
  add w0, w0, w10
  ldp x29, x30, [sp], #16
  autiasp
  ret
.subsections_via_symbols
"""

# Object B: defines _helper and _bmarker, reads A's _table through the GOT.
UNIT_B = """\
.section __DATA,__data
.p2align 3
.globl _bmarker
_bmarker:
  .quad 7
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _helper
_helper:
  adrp x9, _table@GOTPAGE
  ldr x9, [x9, _table@GOTPAGEOFF]
  ldur x10, [x9]
  add w0, w0, w10
  ret
.subsections_via_symbols
"""


def _emit(unit: str, path: Path) -> Path:
    sections, undefined = assemble_file(unit)
    path.write_bytes(macho_obj.emit_object(sections, undefined=undefined))
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _emit(UNIT_A, tmp_path / "a.o"),
        _emit(UNIT_B, tmp_path / "b.o"),
    )


def _direct_text_object(symbols, relocations=(), undefined=(), size=8):
    return macho_obj.emit_object(
        [macho_obj.Section(
            sectname="__text",
            segname="__TEXT",
            data=b"\0" * size,
            align_log2=2,
            flags=macho_obj.TEXT_SECTION_FLAGS,
            symbols=tuple(symbols),
            relocations=tuple(relocations),
        )],
        undefined=list(undefined),
    )


def _shape(data: bytes):
    obj = spec.parse_object(data)
    names = [s["name"] for s in obj.symbols()]
    payloads, relocs = {}, {}
    for sec in obj.sections():
        key = (sec["segname_str"], sec["sectname_str"])
        payloads[key] = obj.data[sec["offset"]:sec["offset"] + sec["size"]]
        relocs[key] = sorted(
            (r["r_address"],
             r["r_symbolnum"] if r["r_type"] == spec.ARM64_RELOC_ADDEND
             else names[r["r_symbolnum"]],
             r["r_type"], r["r_pcrel"], r["r_length"], r["r_extern"])
            for r in obj.relocations(sec)
        )
    symbols = {
        s["name"]: (s["n_type"] & spec.N_TYPE, s["n_sect"], s["n_value"])
        for s in obj.symbols() if not s["name"].startswith("ltmp")
    }
    return payloads, relocs, symbols


def _ld_r(tmp_path: Path, a: Path, b: Path) -> bytes:
    out = tmp_path / "ld_r.o"
    run = _run(["xcrun", "ld", "-r", "-o", str(out), str(a), str(b)])
    assert run.returncode == 0, run.stderr
    return out.read_bytes()


def test_merged_object_matches_ld_r(tmp_path):
    a, b = _inputs(tmp_path)
    ours = link_relocatable([a.read_bytes(), b.read_bytes()])
    theirs = _ld_r(tmp_path, a, b)

    p_ours, r_ours, s_ours = _shape(ours)
    p_theirs, r_theirs, s_theirs = _shape(theirs)

    assert set(p_ours) == set(p_theirs), (set(p_ours), set(p_theirs))
    for key in p_theirs:
        assert p_ours[key] == p_theirs[key], (
            f"{key}: payload differs\n  pcc: {p_ours[key].hex()}\n"
            f"  ld:  {p_theirs[key].hex()}"
        )
    assert r_ours == r_theirs, f"\n  pcc: {r_ours}\n  ld:  {r_theirs}"
    assert s_ours == s_theirs, f"\n  pcc: {s_ours}\n  ld:  {s_theirs}"


def test_cross_object_symbol_is_resolved(tmp_path):
    a, b = _inputs(tmp_path)
    merged = link_relocatable([a.read_bytes(), b.read_bytes()])
    obj = spec.parse_object(merged)

    undefined = {
        s["name"] for s in obj.symbols()
        if (s["n_type"] & spec.N_TYPE) == spec.N_UNDF
    }
    assert undefined == set(), (
        "_helper/_table/_bmarker are each defined by one of the inputs; "
        f"still undefined after the merge: {sorted(undefined)}"
    )
    defined = {s["name"] for s in obj.symbols()}
    for name in ("_use", "_helper", "_table", "_ptrs", "_bmarker"):
        assert name in defined, sorted(defined)


def test_local_and_external_same_spelling_keep_distinct_relocation_targets():
    local = _direct_text_object(
        (
            macho_obj.TextSymbol("_local_entry", 0, external=True),
            macho_obj.TextSymbol("_shared", 4, external=False),
        ),
        (macho_obj.Relocation(
            0, "_shared", spec.ARM64_RELOC_BRANCH26, True,
        ),),
    )
    consumer = _direct_text_object(
        (macho_obj.TextSymbol("_consumer", 0, external=True),),
        (macho_obj.Relocation(
            0, "_shared", spec.ARM64_RELOC_BRANCH26, True,
        ),),
        undefined=("_shared",),
        size=4,
    )
    external = _direct_text_object(
        (macho_obj.TextSymbol("_shared", 0, external=True),),
        size=4,
    )

    # Reserve external spellings before processing any one input: local-first
    # and external-first orders must both preserve the same two identities.
    for objects in (
        (local, consumer, external),
        (external, local, consumer),
    ):
        obj = spec.parse_object(link_relocatable(list(objects)))
        symbols = obj.symbols()
        local_names = [
            symbol["name"] for symbol in symbols
            if symbol["name"].startswith("_shared$link")
            and not (symbol["n_type"] & spec.N_EXT)
        ]
        assert len(local_names) == 1, [s["name"] for s in symbols]
        external_shared = [
            symbol for symbol in symbols
            if symbol["name"] == "_shared"
            and (symbol["n_type"] & spec.N_EXT)
            and (symbol["n_type"] & spec.N_TYPE) == spec.N_SECT
        ]
        assert len(external_shared) == 1, symbols
        names = [symbol["name"] for symbol in symbols]
        targets = {
            names[reloc["r_symbolnum"]]
            for section in obj.sections()
            for reloc in obj.relocations(section)
            if reloc["r_extern"]
        }
        assert targets == {"_shared", local_names[0]}, targets
        assert not any(
            (symbol["n_type"] & spec.N_TYPE) == spec.N_UNDF
            for symbol in symbols
        )


def test_unresolved_external_and_its_relocation_survive_for_final_link():
    source = _direct_text_object(
        (macho_obj.TextSymbol("_caller", 0, external=True),),
        (macho_obj.Relocation(
            0, "_missing", spec.ARM64_RELOC_BRANCH26, True,
        ),),
        undefined=("_missing",),
        size=4,
    )
    obj = spec.parse_object(link_relocatable([source]))
    symbols = obj.symbols()
    undefined = [
        symbol["name"] for symbol in symbols
        if (symbol["n_type"] & spec.N_TYPE) == spec.N_UNDF
    ]
    assert undefined == ["_missing"]
    names = [symbol["name"] for symbol in symbols]
    text = next(
        section for section in obj.sections()
        if section["sectname_str"] == "__text"
    )
    relocations = obj.relocations(text)
    assert len(relocations) == 1
    assert names[relocations[0]["r_symbolnum"]] == "_missing"


def test_section_alignment_rebases_symbols_and_relocations():
    first = macho_obj.emit_object([macho_obj.Section(
        sectname="__data",
        segname="__DATA",
        data=b"A",
        align_log2=0,
        flags=macho_obj.DATA_SECTION_FLAGS,
        symbols=(macho_obj.TextSymbol("_first", 0),),
    )])
    second = macho_obj.emit_object(
        [macho_obj.Section(
            sectname="__data",
            segname="__DATA",
            data=b"B" * 8,
            align_log2=3,
            flags=macho_obj.DATA_SECTION_FLAGS,
            symbols=(macho_obj.TextSymbol("_second", 0),),
            relocations=(macho_obj.Relocation(
                0, "_missing", spec.ARM64_RELOC_UNSIGNED, False, length=3,
            ),),
        )],
        undefined=["_missing"],
    )

    obj = spec.parse_object(link_relocatable([first, second]))
    data = next(
        section for section in obj.sections()
        if section["sectname_str"] == "__data"
    )
    payload = obj.data[data["offset"]:data["offset"] + data["size"]]
    assert payload == b"A" + b"\0" * 7 + b"B" * 8
    second_symbol = next(
        symbol for symbol in obj.symbols() if symbol["name"] == "_second"
    )
    assert second_symbol["n_value"] - data["addr"] == 8
    relocations = obj.relocations(data)
    assert len(relocations) == 1
    assert relocations[0]["r_address"] == 8


def test_merged_objects_link_and_run_identically(tmp_path):
    a, b = _inputs(tmp_path)
    main_c = tmp_path / "main.c"
    # _helper(5) = 5 + table[0](10) = 15; _use adds table[1](20) -> 35
    main_c.write_text(
        "extern int use(void);\n"
        "int main(void) { return use() == 35 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    for tag, merged in (
        ("pcc", link_relocatable([a.read_bytes(), b.read_bytes()])),
        ("ld", _ld_r(tmp_path, a, b)),
    ):
        obj = tmp_path / f"merged_{tag}.o"
        obj.write_bytes(merged)
        binary = tmp_path / f"prog_{tag}"
        link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
        assert link.returncode == 0, f"{tag}: {link.stderr}"
        rc = _run([str(binary)]).returncode
        assert rc == 0, f"{tag}: wrong result after merge (rc={rc})"


def test_addend_survives_the_merge(tmp_path):
    """`.quad _table+8` must still point at the second element."""
    a, b = _inputs(tmp_path)
    merged = link_relocatable([a.read_bytes(), b.read_bytes()])
    obj = spec.parse_object(merged)
    data_sec = next(
        s for s in obj.sections() if s["sectname_str"] == "__data"
    )
    ptrs = next(s for s in obj.symbols() if s["name"] == "_ptrs")
    off = data_sec["offset"] + (ptrs["n_value"] - data_sec["addr"])
    payload = obj.data[off:off + 16]
    assert struct.unpack("<2q", payload) == (0, 8), payload.hex()


def test_merge_at_real_emitter_scale(tmp_path):
    """The small units above are readable; this one is the real thing.

    A whole self-backend compilation unit (thousands of instructions, three
    sections, hundreds of relocations) merged with a unit that calls into it
    must still equal `ld -r` exactly.
    """
    big_asm = (tmp_path / "big.s")
    # Build a large text section: many functions, each calling the next.
    parts = [".section __TEXT,__text,regular,pure_instructions\n"]
    for i in range(64):
        parts.append(f".p2align 2\n.globl _f{i}\n_f{i}:\n")
        parts.append("  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n")
        parts.append(f"  movz w0, #{i}\n")
        if i:
            parts.append(f"  bl _f{i - 1}\n")
        parts.append("  bl _runtime_hook\n")
        parts.append("  adrp x9, _blob@PAGE\n  add x9, x9, _blob@PAGEOFF\n")
        parts.append("  ldur x10, [x9]\n  add w0, w0, w10\n")
        parts.append("  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n")
    parts.append(".section __DATA,__data\n.p2align 3\n.globl _blob\n_blob:\n")
    parts.extend("  .quad %d\n" % v for v in range(32))
    parts.append(".subsections_via_symbols\n")
    big_asm.write_text("".join(parts), encoding="utf-8")

    big = _emit(big_asm.read_text(encoding="utf-8"), tmp_path / "big.o")
    side = _emit(
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".p2align 2\n.globl _side\n_side:\n"
        "  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n"
        "  bl _f63\n"
        "  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n"
        ".subsections_via_symbols\n",
        tmp_path / "side.o",
    )

    ours = link_relocatable([big.read_bytes(), side.read_bytes()])
    theirs = _ld_r(tmp_path, big, side)
    p_ours, r_ours, s_ours = _shape(ours)
    p_theirs, r_theirs, s_theirs = _shape(theirs)
    assert set(p_ours) == set(p_theirs)
    for key in p_theirs:
        assert p_ours[key] == p_theirs[key], f"{key}: payload differs"
    assert r_ours == r_theirs
    assert s_ours == s_theirs
    # Non-trivial by construction, so a passing test means something.
    text_relocs = r_theirs[("__TEXT", "__text")]
    assert len(text_relocs) > 150, len(text_relocs)
    assert len(s_theirs) > 60, len(s_theirs)


def test_fails_closed_on_bad_link_jobs(tmp_path):
    a, _b = _inputs(tmp_path)
    with pytest.raises(LinkError):
        link_relocatable([])
    with pytest.raises(LinkError, match="duplicate definition"):
        # the same object twice = duplicate definitions
        link_relocatable([a.read_bytes(), a.read_bytes()])


def test_fails_closed_on_wrong_architecture_and_malformed_addend_pair():
    source = _direct_text_object(
        (macho_obj.TextSymbol("_entry", 0, external=True),),
        (macho_obj.Relocation(
            0,
            "_target",
            spec.ARM64_RELOC_PAGE21,
            True,
            addend=4,
        ),),
        undefined=("_target",),
    )

    wrong_arch = bytearray(source)
    header = spec.MACH_HEADER_64.unpack(wrong_arch)
    header["cputype"] = spec.CPU_TYPE_X86_64
    wrong_arch[:spec.MACH_HEADER_64.size] = spec.MACH_HEADER_64.pack(header)
    with pytest.raises(LinkError, match="not an arm64-all object"):
        link_relocatable([bytes(wrong_arch)])

    malformed = bytearray(source)
    parsed = spec.parse_object(source)
    text = next(
        section for section in parsed.sections()
        if section["sectname_str"] == "__text"
    )
    raw_relocations = parsed.relocations(text)
    assert [entry["r_type"] for entry in raw_relocations] == [
        spec.ARM64_RELOC_ADDEND,
        spec.ARM64_RELOC_PAGE21,
    ]
    companion_offset = text["reloff"] + spec.RELOCATION_INFO.size
    companion = spec.RELOCATION_INFO.unpack(malformed, companion_offset)
    companion["r_address"] = 4
    malformed[
        companion_offset:companion_offset + spec.RELOCATION_INFO.size
    ] = spec.RELOCATION_INFO.pack(companion)
    with pytest.raises(LinkError, match="ADDEND must be followed"):
        link_relocatable([bytes(malformed)])

    section_target = macho_obj.emit_object([macho_obj.Section(
        sectname="__data",
        segname="__DATA",
        data=b"\0" * 8,
        align_log2=3,
        flags=macho_obj.DATA_SECTION_FLAGS,
        symbols=(macho_obj.TextSymbol("_slot", 0),),
        relocations=(macho_obj.Relocation(
            0,
            "",
            spec.ARM64_RELOC_UNSIGNED,
            False,
            length=3,
            section=("__DATA", "__data"),
            target_offset=0,
        ),),
    )])
    malformed_section_target = bytearray(section_target)
    parsed = spec.parse_object(section_target)
    data = parsed.sections()[0]
    raw = spec.RELOCATION_INFO.unpack(
        malformed_section_target, data["reloff"],
    )
    decoded = spec.unpack_relocation(raw["r_info"])
    raw["r_info"] = spec.pack_relocation(
        r_symbolnum=decoded["r_symbolnum"],
        r_pcrel=decoded["r_pcrel"],
        r_length=2,
        r_extern=decoded["r_extern"],
        r_type=decoded["r_type"],
    )
    malformed_section_target[
        data["reloff"]:data["reloff"] + spec.RELOCATION_INFO.size
    ] = spec.RELOCATION_INFO.pack(raw)
    with pytest.raises(LinkError, match="eight-byte section target"):
        link_relocatable([bytes(malformed_section_target)])
