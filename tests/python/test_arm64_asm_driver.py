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
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.arm64_encode import EncodeError

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


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
