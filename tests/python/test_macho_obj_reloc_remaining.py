"""Remaining finite arm64 Mach-O object relocation slices.

Every accepted shape is pinned against as(1) entry by entry and exercised by
the system linker.  The malformed-shape cases are equally important: the
writer must reject an incomplete pair or ambiguous section target before it
publishes a plausible-looking object.
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
)


_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def _relocation_rows(path: Path):
    """Resolve extern entries by symbol and local entries by section name."""
    obj = spec.parse_object(path.read_bytes())
    symbols = [symbol["name"] for symbol in obj.symbols()]
    sections = obj.sections()
    rows = {}
    for section in sections:
        entries = []
        for reloc in obj.relocations(section):
            if reloc["r_type"] == spec.ARM64_RELOC_ADDEND:
                target = reloc["r_symbolnum"]
            elif reloc["r_extern"]:
                target = symbols[reloc["r_symbolnum"]]
            else:
                target_section = sections[reloc["r_symbolnum"] - 1]
                target = (
                    target_section["segname_str"],
                    target_section["sectname_str"],
                )
            entries.append((
                reloc["r_address"],
                target,
                reloc["r_type"],
                reloc["r_pcrel"],
                reloc["r_length"],
                reloc["r_extern"],
            ))
        # as(1) may synthesize an empty __TEXT,__text section even when the
        # source contains only data.  The relocation oracle compares the
        # finite relocation tables, not unrelated zero-entry sections.
        if entries:
            rows[(section["segname_str"], section["sectname_str"])] = entries
    return rows


def _section_payload(path: Path, segname: str, sectname: str) -> bytes:
    obj = spec.parse_object(path.read_bytes())
    section = next(
        section for section in obj.sections()
        if section["segname_str"] == segname
        and section["sectname_str"] == sectname
    )
    start = section["offset"]
    return obj.data[start:start + section["size"]]


def _differential_objects(
    tmp_path: Path,
    tag: str,
    ours_bytes: bytes,
    assembly: str,
) -> tuple[Path, Path]:
    ours = tmp_path / f"{tag}_pcc.o"
    ours.write_bytes(ours_bytes)
    source = tmp_path / f"{tag}.s"
    source.write_text(assembly, encoding="utf-8")
    theirs = tmp_path / f"{tag}_as.o"
    assembled = _run(["xcrun", "as", "-o", str(theirs), str(source)])
    assert assembled.returncode == 0, assembled.stderr
    assert _relocation_rows(ours) == _relocation_rows(theirs)
    return ours, theirs


def _link_and_run(tmp_path: Path, tag: str, obj: Path, main_source: str) -> int:
    main = tmp_path / f"{tag}_main.c"
    main.write_text(main_source, encoding="utf-8")
    output = tmp_path / f"{tag}.out"
    linked = _run([_CC, str(main), str(obj), "-o", str(output)])
    assert linked.returncode == 0, linked.stderr
    return _run([str(output)]).returncode


def test_subtractor_pairs_match_as_and_compute_both_widths(tmp_path: Path) -> None:
    from pcc.backend.macho_link import link_relocatable

    data = struct.pack("<qi", 7, 3)
    sections = [Section(
        sectname="__data",
        segname="__DATA",
        data=data,
        align_log2=3,
        flags=DATA_SECTION_FLAGS,
        symbols=(
            TextSymbol("_difference64", 0),
            TextSymbol("_difference32", 8),
        ),
        relocations=(
            Relocation(
                0,
                "_subtractor_base",
                spec.ARM64_RELOC_SUBTRACTOR,
                pcrel=False,
                length=3,
                minuend="_subtractor_target",
            ),
            Relocation(
                8,
                "_subtractor_base",
                spec.ARM64_RELOC_SUBTRACTOR,
                pcrel=False,
                length=2,
                minuend="_subtractor_target",
            ),
        ),
    )]
    assembly = (
        "\t.section\t__DATA,__data\n"
        "\t.globl\t_difference64\n\t.p2align\t3\n"
        "_difference64:\n"
        "\t.quad\t_subtractor_target - _subtractor_base + 7\n"
        "\t.globl\t_difference32\n"
        "_difference32:\n"
        "\t.long\t_subtractor_target - _subtractor_base + 3\n"
        "\t.subsections_via_symbols\n"
    )
    main_source = (
        "#include <stdint.h>\n"
        "extern int64_t difference64;\n"
        "extern int32_t difference32;\n"
        "char subtractor_base = 1;\n"
        "char subtractor_gap[31];\n"
        "char subtractor_target = 2;\n"
        "int main(void) {\n"
        "  intptr_t d = (intptr_t)(uintptr_t)&subtractor_target\n"
        "             - (intptr_t)(uintptr_t)&subtractor_base;\n"
        "  if (difference64 != d + 7) return 1;\n"
        "  if (difference32 != (int32_t)(d + 3)) return 2;\n"
        "  return 0;\n"
        "}\n"
    )
    objects = _differential_objects(
        tmp_path,
        "subtractor",
        macho_obj.emit_object(
            sections,
            undefined=["_subtractor_base", "_subtractor_target"],
        ),
        assembly,
    )
    merged = tmp_path / "subtractor_pcc_ld_r.o"
    merged.write_bytes(link_relocatable([objects[0].read_bytes()]))
    assert _relocation_rows(merged) == _relocation_rows(objects[0])
    for owner, obj in (
        ("pcc", objects[0]),
        ("as", objects[1]),
        ("pcc_ld_r", merged),
    ):
        assert _link_and_run(tmp_path, f"subtractor_{owner}", obj, main_source) == 0


def test_pointer_to_got_forms_match_as_and_obey_ld64_boundaries(tmp_path: Path) -> None:
    from pcc.backend.macho_link import link_relocatable

    data = struct.pack("<iIQ", 0, 0, 0)
    sections = [Section(
        sectname="__data",
        segname="__DATA",
        data=data,
        align_log2=3,
        flags=DATA_SECTION_FLAGS,
        symbols=(
            TextSymbol("_got_delta", 0),
            TextSymbol("_got_absolute", 8),
        ),
        relocations=(
            Relocation(
                0,
                "_ext_value",
                spec.ARM64_RELOC_POINTER_TO_GOT,
                pcrel=True,
                length=2,
            ),
            Relocation(
                8,
                "_ext_value",
                spec.ARM64_RELOC_POINTER_TO_GOT,
                pcrel=False,
                length=3,
            ),
        ),
    )]
    assembly = (
        "\t.section\t__DATA,__data\n"
        "\t.globl\t_got_delta\n\t.p2align\t3\n"
        "_got_delta:\n"
        "\t.long\t_ext_value@GOT - .\n"
        "\t.space\t4\n"
        "\t.globl\t_got_absolute\n"
        "_got_absolute:\n"
        "\t.quad\t_ext_value@GOT\n"
        "\t.subsections_via_symbols\n"
    )
    main_source = (
        "#include <stdint.h>\n"
        "extern int32_t got_delta;\n"
        "extern int *got_absolute;\n"
        "int ext_value = 91;\n"
        "int main(void) {\n"
        "  int **slot = (int **)((char *)&got_delta + got_delta);\n"
        "  if (*slot != &ext_value) return 1;\n"
        "  if (got_absolute != &ext_value) return 2;\n"
        "  return 0;\n"
        "}\n"
    )
    objects = _differential_objects(
        tmp_path,
        "pointer_to_got",
        macho_obj.emit_object(sections, undefined=["_ext_value"]),
        assembly,
    )

    # The absolute eight-byte PTRTGOT form is a real as(1) object shape and
    # survives relocatable links, but Darwin's final linker rejects it.  Pin
    # that boundary for both emitters instead of claiming an executable that
    # the platform oracle itself cannot produce.
    for owner, obj in (("pcc", objects[0]), ("as", objects[1])):
        merged = tmp_path / f"pointer_to_got_{owner}_ld_r.o"
        if owner == "pcc":
            merged.write_bytes(link_relocatable([obj.read_bytes()]))
        else:
            linked = _run(["xcrun", "ld", "-r", str(obj), "-o", str(merged)])
            assert linked.returncode == 0, linked.stderr
        merged_rows = _relocation_rows(merged)
        input_rows = _relocation_rows(obj)
        assert merged_rows.keys() == input_rows.keys()
        for section_key in input_rows:
            # These two PTRTGOT entries are independent.  A relocatable link
            # may canonicalise their physical table order; unlike a
            # SUBTRACTOR/UNSIGNED pair, only their fields and addresses carry
            # semantics.
            assert sorted(merged_rows[section_key], key=lambda row: row[0]) == sorted(
                input_rows[section_key], key=lambda row: row[0]
            )

        main = tmp_path / f"pointer_to_got_{owner}_main.c"
        main.write_text(main_source, encoding="utf-8")
        output = tmp_path / f"pointer_to_got_{owner}.out"
        linked = _run([_CC, str(main), str(obj), "-o", str(output)])
        assert linked.returncode != 0
        assert "relocation in '_got_absolute' is not supported" in linked.stderr

    # The four-byte PC-relative form is supported by final ld64.  Exercise it
    # independently so the unsupported absolute form cannot mask its runtime
    # behavior.
    pcrel_data = struct.pack("<i", 0)
    pcrel_sections = [Section(
        sectname="__data",
        segname="__DATA",
        data=pcrel_data,
        align_log2=2,
        flags=DATA_SECTION_FLAGS,
        symbols=(TextSymbol("_got_delta", 0),),
        relocations=(Relocation(
            0,
            "_ext_value",
            spec.ARM64_RELOC_POINTER_TO_GOT,
            pcrel=True,
            length=2,
        ),),
    )]
    pcrel_assembly = (
        "\t.section\t__DATA,__data\n"
        "\t.globl\t_got_delta\n\t.p2align\t2\n"
        "_got_delta:\n"
        "\t.long\t_ext_value@GOT - .\n"
        "\t.subsections_via_symbols\n"
    )
    pcrel_main = (
        "#include <stdint.h>\n"
        "extern int32_t got_delta;\n"
        "int ext_value = 91;\n"
        "int main(void) {\n"
        "  int **slot = (int **)((char *)&got_delta + got_delta);\n"
        "  return *slot == &ext_value ? 0 : 1;\n"
        "}\n"
    )
    pcrel_objects = _differential_objects(
        tmp_path,
        "pointer_to_got_pcrel",
        macho_obj.emit_object(pcrel_sections, undefined=["_ext_value"]),
        pcrel_assembly,
    )
    for owner, obj in (("pcc", pcrel_objects[0]), ("as", pcrel_objects[1])):
        assert _link_and_run(
            tmp_path, f"pointer_to_got_pcrel_{owner}", obj, pcrel_main
        ) == 0


def test_tlvp_pair_matches_as_and_reads_thread_local_storage(tmp_path: Path) -> None:
    # Save LR around the TLV resolver call, then load the returned address.
    text = struct.pack(
        "<8I",
        0xA9BF7BFD,
        0x90000000,
        0xF9400000,
        0xF9400008,
        0xD63F0100,
        0xB9400000,
        0xA8C17BFD,
        0xD65F03C0,
    )
    sections = [Section(
        sectname="__text",
        segname="__TEXT",
        data=text,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_load_tls_value", 0),),
        relocations=(
            Relocation(
                4,
                "_tls_value",
                spec.ARM64_RELOC_TLVP_LOAD_PAGE21,
                pcrel=True,
            ),
            Relocation(
                8,
                "_tls_value",
                spec.ARM64_RELOC_TLVP_LOAD_PAGEOFF12,
                pcrel=False,
            ),
        ),
    )]
    assembly = (
        "\t.section\t__TEXT,__text,regular,pure_instructions\n"
        "\t.globl\t_load_tls_value\n\t.p2align\t2\n"
        "_load_tls_value:\n"
        "\tstp\tx29, x30, [sp, #-16]!\n"
        "\tadrp\tx0, _tls_value@TLVPPAGE\n"
        "\tldr\tx0, [x0, _tls_value@TLVPPAGEOFF]\n"
        "\tldr\tx8, [x0]\n"
        "\tblr\tx8\n"
        "\tldr\tw0, [x0]\n"
        "\tldp\tx29, x30, [sp], #16\n"
        "\tret\n"
        "\t.subsections_via_symbols\n"
    )
    main_source = (
        "extern int load_tls_value(void);\n"
        "_Thread_local int tls_value = 55;\n"
        "int main(void) {\n"
        "  if (load_tls_value() != 55) return 1;\n"
        "  tls_value = 73;\n"
        "  if (load_tls_value() != 73) return 2;\n"
        "  return 0;\n"
        "}\n"
    )
    objects = _differential_objects(
        tmp_path,
        "tlvp",
        macho_obj.emit_object(sections, undefined=["_tls_value"]),
        assembly,
    )
    for owner, obj in (("pcc", objects[0]), ("as", objects[1])):
        assert _link_and_run(tmp_path, f"tlvp_{owner}", obj, main_source) == 0


def test_local_section_target_matches_as_payload_and_behavior(tmp_path: Path) -> None:
    from pcc.backend.macho_link import link_relocatable

    sections = [
        # as(1) canonicalises segment order (__TEXT before __DATA) even though
        # the source below first switches to __DATA.  Give the direct writer
        # that same final order so the embedded section-relative target value
        # is compared under an identical address layout.
        Section(
            sectname="__const",
            segname="__TEXT",
            data=struct.pack("<2q", 11, 123),
            align_log2=3,
            flags=DATA_SECTION_FLAGS,
        ),
        Section(
            sectname="__data",
            segname="__DATA",
            data=b"\0" * 8,
            align_log2=3,
            flags=DATA_SECTION_FLAGS,
            symbols=(TextSymbol("_local_ptr", 0),),
            relocations=(Relocation(
                0,
                "",
                spec.ARM64_RELOC_UNSIGNED,
                pcrel=False,
                length=3,
                section=("__TEXT", "__const"),
                target_offset=8,
            ),),
        ),
    ]
    assembly = (
        "\t.section\t__DATA,__data\n"
        "\t.globl\t_local_ptr\n\t.p2align\t3\n"
        "_local_ptr:\n"
        "\t.quad\tLlocal_target\n"
        "\t.section\t__TEXT,__const\n\t.p2align\t3\n"
        "\t.quad\t11\n"
        "Llocal_target:\n"
        "\t.quad\t123\n"
        "\t.subsections_via_symbols\n"
    )
    main_source = (
        "extern const long *local_ptr;\n"
        "int main(void) { return *local_ptr == 123 ? 0 : 1; }\n"
    )
    ours = tmp_path / "local_section_pcc.o"
    ours.write_bytes(macho_obj.emit_object(sections))
    source = tmp_path / "local_section.s"
    source.write_text(assembly, encoding="utf-8")
    theirs = tmp_path / "local_section_as.o"
    assembled = _run(["xcrun", "as", "-o", str(theirs), str(source)])
    assert assembled.returncode == 0, assembled.stderr
    objects = (ours, theirs)

    # pcc deliberately owns the local r_extern=0 section-target form.  as(1)
    # expresses the same target through an extern relocation to its local
    # temporary symbol, so a raw relocation-row equality assertion would
    # reject two semantically equivalent objects.
    assert _relocation_rows(ours) == {
        ("__DATA", "__data"): [
            (0, ("__TEXT", "__const"), spec.ARM64_RELOC_UNSIGNED, 0, 3, 0),
        ],
    }
    as_obj = spec.parse_object(theirs.read_bytes())
    as_sections = as_obj.sections()
    as_data = next(
        section for section in as_sections
        if section["segname_str"] == "__DATA"
        and section["sectname_str"] == "__data"
    )
    as_reloc = as_obj.relocations(as_data)
    assert len(as_reloc) == 1
    assert as_reloc[0]["r_extern"] == 1
    as_symbol = as_obj.symbols()[as_reloc[0]["r_symbolnum"]]
    as_target_section = as_sections[as_symbol["n_sect"] - 1]
    assert (
        as_target_section["segname_str"],
        as_target_section["sectname_str"],
    ) == ("__TEXT", "__const")
    stored_addend = struct.unpack(
        "<Q", _section_payload(theirs, "__DATA", "__data")
    )[0]
    assert (
        as_symbol["n_value"] - as_target_section["addr"] + stored_addend
    ) == 8
    assert _section_payload(objects[0], "__DATA", "__data") == (
        _section_payload(objects[1], "__DATA", "__data")
    )
    merged = tmp_path / "local_section_pcc_ld_r.o"
    merged.write_bytes(link_relocatable([objects[0].read_bytes()]))
    for owner, obj in (
        ("pcc", objects[0]),
        ("as", objects[1]),
        ("pcc_ld_r", merged),
    ):
        assert _link_and_run(
            tmp_path, f"local_section_{owner}", obj, main_source
        ) == 0


def test_remaining_relocation_shapes_fail_closed() -> None:
    base = Section(
        sectname="__data",
        segname="__DATA",
        data=b"\0" * 24,
        symbols=(TextSymbol("_owner", 0),),
    )

    def emit(relocation: Relocation, *, extra: tuple[Section, ...] = ()) -> bytes:
        source = Section(
            sectname=base.sectname,
            segname=base.segname,
            data=base.data,
            symbols=base.symbols,
            relocations=(relocation,),
        )
        return macho_obj.emit_object(
            [source, *extra],
            undefined=["_base", "_target"],
        )

    with pytest.raises(MachOEmitError, match="requires a following UNSIGNED"):
        emit(Relocation(
            0, "_base", spec.ARM64_RELOC_SUBTRACTOR,
            pcrel=False, length=3,
        ))
    with pytest.raises(MachOEmitError, match="unknown minuend"):
        emit(Relocation(
            0, "_base", spec.ARM64_RELOC_SUBTRACTOR,
            pcrel=False, length=3, minuend="_missing",
        ))
    with pytest.raises(MachOEmitError, match="only valid on"):
        emit(Relocation(
            0, "_target", spec.ARM64_RELOC_UNSIGNED,
            pcrel=False, length=3, minuend="_base",
        ))
    with pytest.raises(MachOEmitError, match="one of"):
        emit(Relocation(
            0, "_target", spec.ARM64_RELOC_POINTER_TO_GOT,
            pcrel=False, length=2,
        ))
    with pytest.raises(MachOEmitError, match="addend not proven"):
        emit(Relocation(
            0, "_target", spec.ARM64_RELOC_TLVP_LOAD_PAGE21,
            pcrel=True, addend=4,
        ))
    with pytest.raises(MachOEmitError, match="multiple relocation requests"):
        macho_obj.emit_object([
            Section(
                sectname="__data",
                segname="__DATA",
                data=b"\0" * 8,
                symbols=(TextSymbol("_duplicates", 0),),
                relocations=(
                    Relocation(
                        0, "_target", spec.ARM64_RELOC_UNSIGNED,
                        pcrel=False, length=3,
                    ),
                    Relocation(
                        0, "_base", spec.ARM64_RELOC_UNSIGNED,
                        pcrel=False, length=3,
                    ),
                ),
            ),
        ], undefined=["_base", "_target"])
    with pytest.raises(MachOEmitError, match="leave symbol empty"):
        emit(
            Relocation(
                0, "_target", spec.ARM64_RELOC_UNSIGNED,
                pcrel=False, length=3,
                section=("__TEXT", "__const"),
            ),
            extra=(Section(
                sectname="__const", segname="__TEXT", data=b"x" * 8,
            ),),
        )
    with pytest.raises(MachOEmitError, match="outside __TEXT,__const"):
        emit(
            Relocation(
                0, "", spec.ARM64_RELOC_UNSIGNED,
                pcrel=False, length=3,
                section=("__TEXT", "__const"), target_offset=8,
            ),
            extra=(Section(
                sectname="__const", segname="__TEXT", data=b"x" * 8,
            ),),
        )
    with pytest.raises(MachOEmitError, match="must be zero-filled"):
        nonzero = Section(
            sectname="__bad",
            segname="__DATA",
            data=b"\1" + b"\0" * 7,
            symbols=(TextSymbol("_bad", 0),),
            relocations=(Relocation(
                0, "", spec.ARM64_RELOC_UNSIGNED,
                pcrel=False, length=3,
                section=("__TEXT", "__const"), target_offset=0,
            ),),
        )
        macho_obj.emit_object([
            nonzero,
            Section(sectname="__const", segname="__TEXT", data=b"x" * 8),
        ])
    with pytest.raises(MachOEmitError, match="linker-special section"):
        emit(
            Relocation(
                0, "", spec.ARM64_RELOC_UNSIGNED,
                pcrel=False, length=3,
                section=("__TEXT", "__cstring"), target_offset=0,
            ),
            extra=(Section(
                sectname="__cstring",
                segname="__TEXT",
                data=b"x\0",
                align_log2=0,
                flags=CSTRING_SECTION_FLAGS,
            ),),
        )
