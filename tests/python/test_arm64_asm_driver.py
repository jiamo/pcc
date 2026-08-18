"""Full-file differential: a real self-backend .s through pcc's own path.

`assemble_file` + `emit_object` versus `as(1)` on the same input. The input
is genuine emitter output (compiled from a small program by the self backend
and pinned here), covering the whole measured dialect: interleaved
re-declared sections, `.p2align` padding between data items, `.globl`,
`.quad/.long/.byte/.space` items, text labels, assembler-local branch
labels, same-file calls, and adrp/@PAGEOFF against same-file data symbols.

Equality bars:

- `__text` payload bytes identical to as(1)'s
- every data section's payload bytes identical
- relocation tables identical entry by entry (symbols by name)
- real symbols identical (name, type, section, address)
- both objects link into the same runnable program
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.arm64_asm_driver import assemble_file, assemble_lines
from pcc.backend.arm64_encode import EncodeError

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def test_incremental_module_builder_matches_complete_driver():
    from pcc.backend.arm64_asm_driver import AArch64ModuleBuilder

    expected = assemble_file(REAL_SHAPE)
    builder = AArch64ModuleBuilder()
    for line in REAL_SHAPE.splitlines():
        builder.append_chunk(line)
    assert builder.finish() == expected
    assert builder.closed


def test_incremental_module_builder_uses_final_text_layout_and_closes_on_error():
    from pcc.backend.arm64_asm_driver import AArch64ModuleBuilder

    builder = AArch64ModuleBuilder(["L_done"])
    builder.append_chunk(".section __TEXT,__text,regular,pure_instructions\n_probe:")
    builder.append_encoded(0x14000000, -26, 0)
    builder.append_encoded(0xD503201F, 0, -1)
    builder.append_chunk("L_done:")
    builder.append_encoded(0xD65F03C0, 0, -1)
    assert builder.text_label_offsets() == {"_probe": 0, "L_done": 8}
    assert builder.text_size() == 12
    assert builder.finish() == assemble_file(
        ".section __TEXT,__text,regular,pure_instructions\n_probe:\n"
        " b L_done\n nop\nL_done:\n ret\n"
    )
    failed = AArch64ModuleBuilder()
    failed.append_chunk(".section __TEXT,__text,regular,pure_instructions\n_probe:")
    failed.append_encoded(0xD65F03C0, 0, -1)
    with pytest.raises(EncodeError, match="directive '.bad' not proven"):
        failed.append_chunk(".bad")
    assert failed.closed
    for buffer in failed.buffers.values():
        assert buffer.text_builder is None or buffer.text_builder.closed


def test_incremental_module_builder_closes_after_encoded_allocation_failure(monkeypatch):
    from pcc.backend.arm64_asm_driver import AArch64ModuleBuilder
    from pcc.backend.arm64_encode import PackedAArch64TextBuilder

    builder = AArch64ModuleBuilder()
    builder.append_chunk(".section __TEXT,__text,regular,pure_instructions")
    builder.append_encoded(0xD503201F, 0, -1)

    def fail_allocation(*args):
        raise MemoryError("injected encoded allocation failure")

    monkeypatch.setattr(PackedAArch64TextBuilder, "append_encoded", fail_allocation)
    with pytest.raises(MemoryError, match="injected encoded allocation failure"):
        builder.append_encoded(0xD65F03C0, 0, -1)
    assert builder.closed
    assert builder.current.text_builder.closed


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


# Genuine self-backend dialect, miniaturized: the same directive set,
# section interleaving, and reference shapes the emitter produces (measured
# from a real --backend self compile), small enough to read.
REAL_SHAPE = """\
.section __DATA,__const
.p2align 2
_tb_func_1:
  .long 1
.section __DATA,__const
.p2align 0
_tb_name_1:
  .byte 102, 105, 98, 0
.section __DATA,__data
.p2align 3
_pystr_obj_1:
  .quad 1
  .long 4
  .long 1
  .quad 5
  .quad -1
  .quad -1
  .byte 104, 101, 108, 108, 111, 0
  .space 6
.section __DATA,__data
.p2align 3
_table_1:
  .quad _tb_func_1
  .quad _pystr_obj_1+8
  .quad 42
.section __TEXT,__text,regular,pure_instructions
.p2align 2
.globl _leaf_fn
_leaf_fn:
  movz w0, #7
  ret
.p2align 2
.globl _recur_fn
_recur_fn:
  paciasp
  stp x29, x30, [sp, #-16]!
  mov x29, sp
  cmp w0, #1
  b.le L_recur_base
  sub w0, w0, #1
  bl _recur_fn
  add w0, w0, #1
L_recur_base:
  ldp x29, x30, [sp], #16
  autiasp
  ret
.p2align 2
.globl _entry_fn
_entry_fn:
  paciasp
  stp x29, x30, [sp, #-16]!
  mov x29, sp
  bl _leaf_fn
  cbz w0, L_entry_zero
  adrp x9, _pystr_obj_1@PAGE
  add x9, x9, _pystr_obj_1@PAGEOFF
  ldur x10, [x9, #16]
  add w0, w0, w10
  b L_entry_done
L_entry_zero:
  movz w0, #0
L_entry_done:
  bl _external_hook
  ldp x29, x30, [sp], #16
  autiasp
  ret
.subsections_via_symbols
"""


def _pcc_object(tmp_path: Path) -> Path:
    sections, undefined = assemble_file(REAL_SHAPE)
    obj = tmp_path / "shape_pcc.o"
    obj.write_bytes(macho_obj.emit_object(sections, undefined=undefined))
    return obj


def test_line_input_api_matches_full_string_projection() -> None:
    # Emitter entries are chunks, not guaranteed physical lines: globals and
    # constant tables deliberately publish several directives in one string.
    line_sections, line_undefined = assemble_lines([REAL_SHAPE])
    text_sections, text_undefined = assemble_file(REAL_SHAPE)
    assert line_sections == text_sections
    assert line_undefined == text_undefined


def test_line_input_merges_structured_sections_in_macho_order() -> None:
    structured = macho_obj.Section(
        sectname="__pcc_stackmaps",
        segname="__DATA",
        data=b"stack-map",
        align_log2=3,
        flags=macho_obj.PCC_STACKMAP_SECTION_FLAGS,
        relocations=(
            macho_obj.Relocation(
                0,
                "_entry_fn",
                spec.ARM64_RELOC_UNSIGNED,
                pcrel=False,
                length=3,
            ),
            macho_obj.Relocation(
                0,
                "_external_stackmap_target",
                spec.ARM64_RELOC_UNSIGNED,
                pcrel=False,
                length=3,
            ),
        ),
    )
    trailing = macho_obj.Section(
        sectname="__compact_unwind",
        segname="__LD",
        data=b"",
        flags=macho_obj.COMPACT_UNWIND_SECTION_FLAGS,
    )
    sections, undefined = assemble_lines(
        REAL_SHAPE.splitlines(),
        (trailing, structured),
    )

    keys = [(section.segname, section.sectname) for section in sections]
    assert keys.index(("__DATA", "__pcc_stackmaps")) < keys.index(
        ("__LD", "__compact_unwind")
    )
    assert sections[keys.index(("__DATA", "__pcc_stackmaps"))] is structured
    assert "_entry_fn" not in undefined
    assert "_external_stackmap_target" in undefined


def test_line_input_rejects_duplicate_or_untyped_structured_sections() -> None:
    duplicate = macho_obj.Section(
        sectname="__data",
        segname="__DATA",
        data=b"",
    )
    with pytest.raises(EncodeError, match="duplicates section"):
        assemble_lines(REAL_SHAPE.splitlines(), (duplicate,))
    with pytest.raises(EncodeError, match="is not a Section"):
        assemble_lines(REAL_SHAPE.splitlines(), (object(),))


def _as_object(tmp_path: Path) -> Path:
    src = tmp_path / "shape.s"
    src.write_text(REAL_SHAPE, encoding="utf-8")
    obj = tmp_path / "shape_as.o"
    build = _run(["xcrun", "as", "-o", str(obj), str(src)])
    assert build.returncode == 0, build.stderr
    return obj


def _payloads(path: Path):
    obj = spec.parse_object(path.read_bytes())
    out = {}
    for sec in obj.sections():
        key = (sec["segname_str"], sec["sectname_str"])
        out[key] = obj.data[sec["offset"]:sec["offset"] + sec["size"]]
    return out


def _named_relocs(path: Path):
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


def test_every_section_payload_matches_as(tmp_path):
    ours = _payloads(_pcc_object(tmp_path))
    theirs = _payloads(_as_object(tmp_path))
    assert set(ours) == set(theirs), (set(ours), set(theirs))
    for key in theirs:
        assert ours[key] == theirs[key], (
            f"{key}: payload differs\n  pcc: {ours[key].hex()}\n"
            f"  as:  {theirs[key].hex()}"
        )


def test_relocation_tables_match_as(tmp_path):
    ours = _named_relocs(_pcc_object(tmp_path))
    theirs = _named_relocs(_as_object(tmp_path))
    assert ours == theirs, f"\n  pcc: {ours}\n  as:  {theirs}"


def test_real_symbols_match_as(tmp_path):
    def real_symbols(path):
        obj = spec.parse_object(path.read_bytes())
        return {
            s["name"]: (s["n_type"], s["n_sect"], s["n_value"])
            for s in obj.symbols() if not s["name"].startswith("ltmp")
        }

    assert real_symbols(_pcc_object(tmp_path)) == real_symbols(
        _as_object(tmp_path)
    )


def test_both_objects_link_and_behave_identically(tmp_path):
    main_c = tmp_path / "main.c"
    main_c.write_text(
        "extern int entry_fn(void);\n"
        "void external_hook(void) {}\n"
        "extern int recur_fn(int);\n"
        # 7 from leaf_fn + the third pystr quad (5) = 12; recur_fn(3)
        # exercises the same-atom recursive bl, which must be resolved
        # inline (no relocation) exactly as as(1) does.
        "int main(void) {\n"
        "    if (entry_fn() != 12) return 1;\n"
        "    if (recur_fn(3) != 3) return 2;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    for tag, obj in (
        ("pcc", _pcc_object(tmp_path)), ("as", _as_object(tmp_path)),
    ):
        binary = tmp_path / f"prog_{tag}"
        link = _run([_CC, str(main_c), str(obj), "-o", str(binary)])
        assert link.returncode == 0, f"{tag}: {link.stderr}"
        rc = _run([str(binary)]).returncode
        assert rc == 0, f"{tag}: wrong runtime behavior (rc={rc})"


def test_driver_fails_closed_on_unproven_dialect():
    with pytest.raises(EncodeError):
        assemble_file(".section __TEXT,__weird\n_x:\n  ret\n")
    with pytest.raises(EncodeError):
        assemble_file(
            ".section __DATA,__data\n_x:\n  .asciz \"s\"\n"
        )
    with pytest.raises(EncodeError):
        # symbol-valued .long is not pointer-width
        assemble_file(
            ".section __DATA,__data\n_x:\n  .long _y\n"
        )
    with pytest.raises(EncodeError):
        assemble_file("  ret\n")  # instruction before any .section
