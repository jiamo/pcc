"""Remaining LINK-P1-MACHO-OBJ-FULL section and load-command shapes.

This suite keeps the generic object contract and the real pcc producer wired
together.  The fixture is LLVM IR, not a hand-built Section list: it exercises
``llvm.global_ctors`` lowering, post-peephole compact-unwind sizing, the full
asm driver, and finally the Mach-O writer.  A second fixture pins inline data
and LC_DATA_IN_CODE independently so neither proof can pass vacuously.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.arm64_encode import EncodeError
from pcc.backend.macho_link import link_relocatable
from pcc.backend.self_backend import emit_aarch64_darwin_asm
from pcc.backend.macho_obj import (
    COMPACT_UNWIND_SECTION_FLAGS,
    DATA_SECTION_FLAGS,
    MOD_INIT_SECTION_FLAGS,
    DataInCodeRegion,
    MachOEmitError,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
)


_IS_ARM64_DARWIN = (
    os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
)
_AS = shutil.which("as")
_CC = shutil.which(os.environ.get("CC", "cc"))
_DIFF_GATE = pytest.mark.pcc_gate(
    unavailable=None if (_AS and _IS_ARM64_DARWIN)
    else "needs as(1) on Darwin arm64"
)
_RUN_GATE = pytest.mark.pcc_gate(
    unavailable=None if (_AS and _CC and _IS_ARM64_DARWIN)
    else "needs cc and as(1) on Darwin arm64"
)


REAL_PCC_IR = r'''
target triple = "arm64-apple-macosx12.0.0"

@llvm.global_ctors = appending global [2 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 65535, ptr @late_module_init, ptr null }, { i32, ptr, ptr } { i32 101, ptr @pcc_module_init, ptr null }]

define internal void @late_module_init() {
entry:
  ret void
}

define internal void @pcc_module_init() {
entry:
  ret void
}

define i32 @main() {
entry:
  ret i32 0
}
'''


BEHAVIOR_PCC_IR = r'''
target triple = "arm64-apple-macosx12.0.0"

@ctor_flag = internal global i32 0
@llvm.global_ctors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 101, ptr @set_ctor_flag, ptr null }]

define internal void @set_ctor_flag() {
entry:
  store i32 77, ptr @ctor_flag
  ret void
}

define i32 @read_ctor_flag() {
entry:
  %value = load i32, ptr @ctor_flag
  ret i32 %value
}
'''


INLINE_DATA_ASM = r'''
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _entry
_entry:
  b Lafter_table
.data_region jt32
  .long 287454020, 1432778632
.end_data_region
Lafter_table:
  movz w0, #0
  ret
.subsections_via_symbols
'''


def _object_from_asm(asm_text: str) -> bytes:
    sections, undefined = assemble_file(asm_text)
    return macho_obj.emit_object(sections, undefined=undefined)


def _section(obj: spec.MachOObject, segname: str, sectname: str):
    return next(
        section for section in obj.sections()
        if section["segname_str"] == segname
        and section["sectname_str"] == sectname
    )


def _named_relocations(obj: spec.MachOObject, section):
    names = [symbol["name"] for symbol in obj.symbols()]
    return [
        (
            entry["r_address"],
            names[entry["r_symbolnum"]] if entry["r_extern"]
            else ("section", entry["r_symbolnum"]),
            entry["r_type"], entry["r_pcrel"], entry["r_length"],
            entry["r_extern"],
        )
        for entry in obj.relocations(section)
    ]


def test_real_pcc_ir_emits_mod_init_and_compact_unwind_sections():
    asm_text = emit_aarch64_darwin_asm(REAL_PCC_IR, optimize=False)
    assert ".section __DATA,__mod_init_func,mod_init_funcs" in asm_text
    assert ".section __LD,__compact_unwind,regular,debug" in asm_text
    assert "_llvm.global_ctors:" not in asm_text

    obj = spec.parse_object(_object_from_asm(asm_text))
    text = _section(obj, "__TEXT", "__text")
    mod_init = _section(obj, "__DATA", "__mod_init_func")
    unwind = _section(obj, "__LD", "__compact_unwind")

    assert mod_init["flags"] == MOD_INIT_SECTION_FLAGS
    assert mod_init["align"] == 3
    assert mod_init["size"] == 16
    mod_relocs = _named_relocations(obj, mod_init)
    assert len(mod_relocs) == 2
    mod_reloc_by_offset = {entry[0]: entry for entry in mod_relocs}
    assert set(mod_reloc_by_offset) == {0, 8}
    assert all(
        entry[2:] == (spec.ARM64_RELOC_UNSIGNED, 0, 3, 1)
        for entry in mod_relocs
    )
    # The IR deliberately lists 65535 first: lowering must honor ctor
    # priority, then retain source order only as the equal-priority tie break.
    assert "pcc_module_init" in mod_reloc_by_offset[0][1]
    assert "late_module_init" in mod_reloc_by_offset[8][1]

    assert unwind["flags"] == COMPACT_UNWIND_SECTION_FLAGS
    assert unwind["align"] == 3
    assert unwind["size"] == 3 * 32
    text_index = next(
        index for index, section in enumerate(obj.sections(), start=1)
        if section["segname_str"] == "__TEXT"
        and section["sectname_str"] == "__text"
    )
    unwind_relocs = _named_relocations(obj, unwind)
    assert [entry[0] for entry in unwind_relocs] == [64, 32, 0]
    assert all(
        entry[1] == ("section", text_index)
        and entry[2:] == (spec.ARM64_RELOC_UNSIGNED, 0, 3, 0)
        for entry in unwind_relocs
    )

    payload = obj.data[unwind["offset"]:unwind["offset"] + unwind["size"]]
    for row in range(3):
        base = row * 32
        function_length = int.from_bytes(payload[base + 8:base + 12], "little")
        encoding = int.from_bytes(payload[base + 12:base + 16], "little")
        assert function_length > 0 and function_length % 4 == 0
        assert encoding == 0x04000000
        assert payload[base + 16:base + 32] == b"\0" * 16

    names = {symbol["name"] for symbol in obj.symbols()}
    assert not any("llvm.global_ctors" in name for name in names)
    assert text["nreloc"] == 0


def test_inline_data_region_emits_exact_lc_data_in_code_table():
    obj = spec.parse_object(_object_from_asm(INLINE_DATA_ASM))
    text = _section(obj, "__TEXT", "__text")
    command = obj.command(spec.LC_DATA_IN_CODE)
    assert command is not None
    assert command.cmdsize == spec.LINKEDIT_DATA_COMMAND.size
    assert command.body["datasize"] == spec.DATA_IN_CODE_ENTRY.size
    assert command.body["dataoff"] < obj.command(spec.LC_SYMTAB).body["symoff"]
    assert obj.data_in_code() == [{
        "offset": text["addr"] + 4,
        "length": 8,
        "kind": spec.DICE_KIND_JUMP_TABLE32,
    }]
    payload = obj.data[text["offset"]:text["offset"] + text["size"]]
    assert payload[4:12] == bytes.fromhex("4433221188776655")


def test_data_in_code_odd_tail_and_relocatable_merge_preserve_the_range():
    source = r'''
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _entry
_entry:
  ret
.data_region jt8
  .byte 1
_byte_entry:
  .byte 2, 3
.end_data_region
.subsections_via_symbols
'''
    original = spec.parse_object(_object_from_asm(source))
    prefix = _object_from_asm(r'''
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _prefix
_prefix:
  ret
.subsections_via_symbols
''')
    suffix = _object_from_asm(r'''
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _suffix
_suffix:
  ret
.subsections_via_symbols
''')
    merged = spec.parse_object(
        link_relocatable([prefix, original.data, suffix])
    )
    original_expected = [{
        "offset": 4,
        "length": 3,
        "kind": spec.DICE_KIND_JUMP_TABLE8,
    }]
    merged_expected = [
        {
            "offset": 8,
            "length": 3,
            "kind": spec.DICE_KIND_JUMP_TABLE8,
        },
        {
            "offset": 11,
            "length": 1,
            "kind": spec.DICE_KIND_DATA,
        },
    ]
    assert original.data_in_code() == original_expected
    assert merged.data_in_code() == merged_expected
    merged_byte_symbol = next(
        item for item in merged.symbols() if item["name"] == "_byte_entry"
    )
    assert merged_byte_symbol["n_value"] == 9


def test_private_extern_stays_in_extdef_partition_with_n_pext():
    sections, undefined = assemble_file(r'''
.section __DATA,__data
.p2align 3
_hidden_entry:
  .quad 0
.private_extern _hidden_entry
.subsections_via_symbols
''')
    object_bytes = macho_obj.emit_object(sections, undefined=undefined)
    obj = spec.parse_object(object_bytes)
    symbol = next(
        item for item in obj.symbols() if item["name"] == "_hidden_entry"
    )
    assert symbol["n_type"] == spec.N_SECT | spec.N_EXT | spec.N_PEXT
    dysymtab = obj.command(spec.LC_DYSYMTAB).body
    assert dysymtab["nlocalsym"] == 0
    assert dysymtab["nextdefsym"] == 1
    merged = spec.parse_object(link_relocatable([object_bytes]))
    merged_symbol = next(
        item for item in merged.symbols() if item["name"] == "_hidden_entry"
    )
    assert merged_symbol["n_type"] == (
        spec.N_SECT | spec.N_EXT | spec.N_PEXT
    )


def test_special_sections_and_data_regions_fail_closed():
    text = Section(
        sectname="__text", segname="__TEXT", data=b"\0" * 8,
        align_log2=2, flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_f", 0),),
    )
    with pytest.raises(MachOEmitError, match="mod_init"):
        macho_obj.emit_object([
            text,
            Section(
                sectname="__mod_init_func", segname="__DATA", data=b"\0" * 8,
                align_log2=3, flags=MOD_INIT_SECTION_FLAGS,
            ),
        ])
    with pytest.raises(MachOEmitError, match="function-address"):
        macho_obj.emit_object([
            text,
            Section(
                sectname="__compact_unwind", segname="__LD",
                data=b"\0" * 32, align_log2=3,
                flags=COMPACT_UNWIND_SECTION_FLAGS,
            ),
        ])
    with pytest.raises(MachOEmitError, match="ordered and non-overlapping"):
        macho_obj.emit_object([
            Section(
                sectname="__text", segname="__TEXT", data=b"\0" * 16,
                align_log2=2, flags=TEXT_SECTION_FLAGS,
                symbols=(TextSymbol("_f", 0),),
                data_in_code=(
                    DataInCodeRegion(4, 8), DataInCodeRegion(8, 4),
                ),
            ),
        ])
    with pytest.raises(MachOEmitError, match="instruction sections"):
        macho_obj.emit_object([
            text,
            Section(
                sectname="__data", segname="__DATA", data=b"\0" * 8,
                flags=DATA_SECTION_FLAGS,
                data_in_code=(DataInCodeRegion(0, 4),),
            ),
        ])
    with pytest.raises(EncodeError, match="unterminated"):
        assemble_file(r'''
.section __TEXT,__text,regular,pure_instructions
.globl _f
_f:
.data_region
  .long 1
''')
    with pytest.raises(EncodeError, match="bad data-region kind"):
        assemble_file(r'''
.section __TEXT,__text,regular,pure_instructions
.globl _f
_f:
.data_region data
  .long 1
.end_data_region
''')


@_RUN_GATE
def test_real_pcc_mod_init_pointer_runs_before_main(tmp_path: Path):
    asm_text = emit_aarch64_darwin_asm(BEHAVIOR_PCC_IR, optimize=False)
    pcc_object = tmp_path / "ctor_pcc.o"
    pcc_object.write_bytes(_object_from_asm(asm_text))
    asm_source = tmp_path / "ctor.s"
    asm_source.write_text(asm_text, encoding="utf-8")
    as_object = tmp_path / "ctor_as.o"
    assembled = subprocess.run(
        [_AS, "-o", str(as_object), str(asm_source)],
        capture_output=True, text=True, timeout=120,
    )
    assert assembled.returncode == 0, assembled.stderr

    main_source = tmp_path / "main.c"
    main_source.write_text(
        "extern int read_ctor_flag(void);\n"
        "int main(void) { return read_ctor_flag() == 77 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    for tag, object_path in (("pcc", pcc_object), ("as", as_object)):
        executable = tmp_path / f"ctor_{tag}"
        linked = subprocess.run(
            [_CC, str(main_source), str(object_path), "-o", str(executable)],
            capture_output=True, text=True, timeout=120,
        )
        assert linked.returncode == 0, f"{tag}: {linked.stderr}"
        ran = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=60,
        )
        assert ran.returncode == 0, f"{tag}: ctor did not run"


@_DIFF_GATE
def test_real_pcc_section_symbol_and_data_in_code_shape_matches_as(tmp_path: Path):
    asm_text = emit_aarch64_darwin_asm(REAL_PCC_IR, optimize=False)
    # Add a separately named text atom carrying inline data so the same object
    # differentially exercises all three remaining shapes.
    inline = INLINE_DATA_ASM.replace(".subsections_via_symbols\n", "")
    asm_text = asm_text.replace(
        ".subsections_via_symbols\n",
        inline + ".subsections_via_symbols\n",
    )

    ours = spec.parse_object(_object_from_asm(asm_text))
    source = tmp_path / "remaining.s"
    source.write_text(asm_text, encoding="utf-8")
    oracle_path = tmp_path / "remaining_as.o"
    run = subprocess.run(
        [_AS, "-o", str(oracle_path), str(source)],
        capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr
    oracle = spec.parse_object(oracle_path.read_bytes())

    def sections(obj):
        return {
            (section["segname_str"], section["sectname_str"]): (
                section["addr"], section["size"], section["align"],
                section["flags"],
                obj.data[section["offset"]:section["offset"] + section["size"]],
                _named_relocations(obj, section),
            )
            for section in obj.sections()
        }

    assert sections(ours) == sections(oracle)
    assert ours.data_in_code() == oracle.data_in_code()

    def real_symbols(obj):
        return {
            symbol["name"]: (
                symbol["n_type"], symbol["n_sect"], symbol["n_value"],
            )
            for symbol in obj.symbols()
            if not symbol["name"].startswith("ltmp")
        }

    assert real_symbols(ours) == real_symbols(oracle)
