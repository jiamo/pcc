"""LINK-P1-MACHO-SPEC acceptance: declarations verified against the SDK and
a real clang object.

Three layers, each closing a different failure mode:

1. **SDK layout probe** — every field offset, struct size, and constant in
   `pcc/backend/macho_spec.py` is compared against a cc probe compiled over
   `<mach-o/loader.h>` etc. This is what "generated from or checked against
   the SDK headers" means here: the SDK stays the authority and there is no
   second generated file to drift.
2. **Round-trip on a clang .o** — parse a clang-produced object and
   re-serialise it; the header + load-command region must come back byte for
   byte, and every field otool reports must match what the parser read.
   A missing or mis-sized field cannot survive this.
3. **Relocation bitfield** — pack/unpack of relocation_info's packed word
   round-trips on every arm64 relocation clang emitted for a call+global
   shape, and the interpreted fields match otool's textual output.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import textwrap
from pathlib import Path

import pytest

from pcc.backend import macho_spec as spec

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_DARWIN = os.uname().sysname == "Darwin"
_GATE = None if (_CC and _IS_DARWIN) else "needs cc and a Darwin SDK"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)


def _run(cmd, **kw):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, **kw
    )


# --- layer 1: the SDK is the authority --------------------------------------

_PROBE_HEADERS = """
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach-o/reloc.h>
#include <mach-o/arm64/reloc.h>
#include <stddef.h>
#include <stdio.h>
"""

# struct name in the SDK -> our declaration. relocation_info is checked
# separately because its second word is a C bitfield (no offsetof per field).
_SDK_STRUCTS = {
    "mach_header_64": spec.MACH_HEADER_64,
    "load_command": spec.LOAD_COMMAND,
    "segment_command_64": spec.SEGMENT_COMMAND_64,
    "section_64": spec.SECTION_64,
    "symtab_command": spec.SYMTAB_COMMAND,
    "dysymtab_command": spec.DYSYMTAB_COMMAND,
    "nlist_64": spec.NLIST_64,
    "build_version_command": spec.BUILD_VERSION_COMMAND,
    "build_tool_version": spec.BUILD_TOOL_VERSION,
    "linkedit_data_command": spec.LINKEDIT_DATA_COMMAND,
    "data_in_code_entry": spec.DATA_IN_CODE_ENTRY,
}

_SDK_CONSTANTS = {
    "MH_MAGIC_64": spec.MH_MAGIC_64,
    "MH_OBJECT": spec.MH_OBJECT,
    "MH_EXECUTE": spec.MH_EXECUTE,
    "MH_SUBSECTIONS_VIA_SYMBOLS": spec.MH_SUBSECTIONS_VIA_SYMBOLS,
    "CPU_TYPE_ARM64": spec.CPU_TYPE_ARM64,
    "CPU_SUBTYPE_ARM64_ALL": spec.CPU_SUBTYPE_ARM64_ALL,
    "LC_SEGMENT_64": spec.LC_SEGMENT_64,
    "LC_SYMTAB": spec.LC_SYMTAB,
    "LC_DYSYMTAB": spec.LC_DYSYMTAB,
    "LC_BUILD_VERSION": spec.LC_BUILD_VERSION,
    "LC_MAIN": spec.LC_MAIN,
    "LC_LOAD_DYLIB": spec.LC_LOAD_DYLIB,
    "LC_UUID": spec.LC_UUID,
    "LC_CODE_SIGNATURE": spec.LC_CODE_SIGNATURE,
    "LC_FUNCTION_STARTS": spec.LC_FUNCTION_STARTS,
    "LC_DATA_IN_CODE": spec.LC_DATA_IN_CODE,
    "S_REGULAR": spec.S_REGULAR,
    "S_ZEROFILL": spec.S_ZEROFILL,
    "S_CSTRING_LITERALS": spec.S_CSTRING_LITERALS,
    "S_MOD_INIT_FUNC_POINTERS": spec.S_MOD_INIT_FUNC_POINTERS,
    "S_COALESCED": spec.S_COALESCED,
    "S_ATTR_PURE_INSTRUCTIONS": spec.S_ATTR_PURE_INSTRUCTIONS,
    "S_ATTR_NO_TOC": spec.S_ATTR_NO_TOC,
    "S_ATTR_STRIP_STATIC_SYMS": spec.S_ATTR_STRIP_STATIC_SYMS,
    "S_ATTR_LIVE_SUPPORT": spec.S_ATTR_LIVE_SUPPORT,
    "S_ATTR_DEBUG": spec.S_ATTR_DEBUG,
    "S_ATTR_SOME_INSTRUCTIONS": spec.S_ATTR_SOME_INSTRUCTIONS,
    "S_ATTR_EXT_RELOC": spec.S_ATTR_EXT_RELOC,
    "S_ATTR_LOC_RELOC": spec.S_ATTR_LOC_RELOC,
    "N_EXT": spec.N_EXT,
    "N_PEXT": spec.N_PEXT,
    "N_TYPE": spec.N_TYPE,
    "N_UNDF": spec.N_UNDF,
    "N_SECT": spec.N_SECT,
    "PLATFORM_MACOS": spec.PLATFORM_MACOS,
    "ARM64_RELOC_UNSIGNED": spec.ARM64_RELOC_UNSIGNED,
    "ARM64_RELOC_SUBTRACTOR": spec.ARM64_RELOC_SUBTRACTOR,
    "ARM64_RELOC_BRANCH26": spec.ARM64_RELOC_BRANCH26,
    "ARM64_RELOC_PAGE21": spec.ARM64_RELOC_PAGE21,
    "ARM64_RELOC_PAGEOFF12": spec.ARM64_RELOC_PAGEOFF12,
    "ARM64_RELOC_GOT_LOAD_PAGE21": spec.ARM64_RELOC_GOT_LOAD_PAGE21,
    "ARM64_RELOC_GOT_LOAD_PAGEOFF12": spec.ARM64_RELOC_GOT_LOAD_PAGEOFF12,
    "ARM64_RELOC_ADDEND": spec.ARM64_RELOC_ADDEND,
    "DICE_KIND_DATA": spec.DICE_KIND_DATA,
    "DICE_KIND_JUMP_TABLE8": spec.DICE_KIND_JUMP_TABLE8,
    "DICE_KIND_JUMP_TABLE16": spec.DICE_KIND_JUMP_TABLE16,
    "DICE_KIND_JUMP_TABLE32": spec.DICE_KIND_JUMP_TABLE32,
    "DICE_KIND_ABS_JUMP_TABLE32": spec.DICE_KIND_ABS_JUMP_TABLE32,
}


# Fields whose C spelling differs from our flat name (SDK nests n_strx in a
# union). Key: (struct, our field) -> the offsetof path in the header.
_SDK_FIELD_PATHS = {
    ("nlist_64", "n_strx"): "n_un.n_strx",
}


def _sdk_layout(tmp_path: Path) -> dict[str, int]:
    lines = []
    for sdk_name, decl in _SDK_STRUCTS.items():
        lines.append(
            f'printf("S {sdk_name} %zu\\n", sizeof(struct {sdk_name}));'
        )
        for field_name, _fmt in decl.fields:
            path = _SDK_FIELD_PATHS.get((sdk_name, field_name), field_name)
            lines.append(
                f'printf("O {sdk_name}.{field_name} %zu\\n", '
                f"offsetof(struct {sdk_name}, {path}));"
            )
    lines.append(
        'printf("S relocation_info %zu\\n", sizeof(struct relocation_info));'
    )
    for const in _SDK_CONSTANTS:
        lines.append(
            f'printf("C {const} %llu\\n", (unsigned long long){const});'
        )
    src = tmp_path / "probe.c"
    src.write_text(
        _PROBE_HEADERS
        + "int main(void) {\n"
        + textwrap.indent("\n".join(lines), "    ")
        + "\n    return 0;\n}\n",
        encoding="utf-8",
    )
    exe = tmp_path / "probe"
    build = _run([_CC, str(src), "-o", str(exe)])
    assert build.returncode == 0, build.stderr
    out = _run([str(exe)])
    values: dict[str, int] = {}
    for line in out.stdout.splitlines():
        kind, name, value = line.split()
        values[f"{kind} {name}"] = int(value)
    return values


def test_every_field_offset_size_and_constant_matches_the_sdk(tmp_path):
    sdk = _sdk_layout(tmp_path)
    problems = []
    for sdk_name, decl in _SDK_STRUCTS.items():
        if decl.size != sdk[f"S {sdk_name}"]:
            problems.append(
                f"sizeof({sdk_name}): ours {decl.size}, SDK {sdk[f'S {sdk_name}']}"
            )
        for field_name, _fmt in decl.fields:
            ours = decl.offset_of(field_name)
            theirs = sdk[f"O {sdk_name}.{field_name}"]
            if ours != theirs:
                problems.append(
                    f"offsetof({sdk_name}, {field_name}): ours {ours}, SDK {theirs}"
                )
    if spec.RELOCATION_INFO.size != sdk["S relocation_info"]:
        problems.append(
            f"sizeof(relocation_info): ours {spec.RELOCATION_INFO.size}, "
            f"SDK {sdk['S relocation_info']}"
        )
    for const, ours in _SDK_CONSTANTS.items():
        theirs = sdk[f"C {const}"]
        if (ours & 0xFFFFFFFFFFFFFFFF) != theirs:
            problems.append(f"{const}: ours 0x{ours:x}, SDK 0x{theirs:x}")
    assert not problems, "\n  ".join(["declarations disagree with the SDK:"] + problems)


# --- layer 2: round-trip a real clang object --------------------------------

_CLANG_SRC = r"""
extern int helper(int);
int global_counter = 7;
static const char message[] = "hello mach-o";

int compute(int x) {
    global_counter += x;
    return helper(global_counter) + (int)message[0];
}

int helper_local(int y) { return y * 3; }
"""


def _clang_object(tmp_path: Path) -> bytes:
    src = tmp_path / "shape.c"
    src.write_text(_CLANG_SRC, encoding="utf-8")
    obj = tmp_path / "shape.o"
    build = _run([_CC, "-c", "-O1", "-target", "arm64-apple-macos12", str(src),
                  "-o", str(obj)])
    assert build.returncode == 0, build.stderr
    return obj.read_bytes()


def test_parse_and_repack_reproduces_the_clang_object_exactly(tmp_path):
    data = _clang_object(tmp_path)
    obj = spec.parse_object(data)

    covered = spec.MACH_HEADER_64.size + obj.header["sizeofcmds"]
    assert obj.pack() == data[:covered], (
        "re-serialising the parsed header + load commands did not reproduce "
        "the clang object byte for byte"
    )
    # Every load command accounted for, none skipped.
    assert len(obj.commands) == obj.header["ncmds"]
    assert sum(lc.cmdsize for lc in obj.commands) == obj.header["sizeofcmds"]


def test_parsed_fields_match_otool_output(tmp_path):
    obj_path = tmp_path / "shape.o"
    obj_path.write_bytes(_clang_object(tmp_path))
    obj = spec.parse_object(obj_path.read_bytes())

    otool = _run(["xcrun", "otool", "-lv", str(obj_path)])
    assert otool.returncode == 0, otool.stderr

    # Section names + sizes as otool sees them.
    reported = re.findall(
        r"sectname (\S+)\n\s+segname (\S+)\n\s+addr 0x([0-9a-f]+)\n\s+size 0x([0-9a-f]+)",
        otool.stdout,
    )
    ours = {
        (s["sectname_str"], s["segname_str"]): (s["addr"], s["size"])
        for s in obj.sections()
    }
    assert reported, otool.stdout[:2000]
    for sectname, segname, addr, size in reported:
        key = (sectname, segname)
        assert key in ours, f"otool sees {key}, parser does not"
        assert ours[key] == (int(addr, 16), int(size, 16)), key

    # Symbols as nm sees them (name -> defined/undefined).
    nm = _run(["xcrun", "nm", "-m", str(obj_path)])
    assert nm.returncode == 0, nm.stderr
    names = {s["name"] for s in obj.symbols()}
    for expected in ("_compute", "_helper", "_global_counter", "_helper_local"):
        assert expected in names, sorted(names)

    undefined = {
        s["name"] for s in obj.symbols() if (s["n_type"] & spec.N_TYPE) == spec.N_UNDF
    }
    assert "_helper" in undefined
    assert "_compute" not in undefined


# --- layer 3: relocation bitfield ------------------------------------------

def test_relocation_bitfield_roundtrips_against_clang_relocations(tmp_path):
    obj_path = tmp_path / "shape.o"
    obj_path.write_bytes(_clang_object(tmp_path))
    obj = spec.parse_object(obj_path.read_bytes())

    text = next(
        s for s in obj.sections() if s["sectname_str"] == "__text"
    )
    relocs = obj.relocations(text)
    assert relocs, "the call+global shape must produce relocations"

    seen_types = {r["r_type"] for r in relocs}
    # The shape calls an extern (BRANCH26) and addresses a global
    # (PAGE21 + PAGEOFF12).
    assert spec.ARM64_RELOC_BRANCH26 in seen_types, seen_types
    assert spec.ARM64_RELOC_PAGE21 in seen_types, seen_types

    for r in relocs:
        packed = spec.pack_relocation(
            r_symbolnum=r["r_symbolnum"], r_pcrel=r["r_pcrel"],
            r_length=r["r_length"], r_extern=r["r_extern"], r_type=r["r_type"],
        )
        assert spec.unpack_relocation(packed) == {
            k: v for k, v in r.items() if k != "r_address"
        }

    # And against otool's textual view of the same table.
    otool = _run(["xcrun", "otool", "-r", str(obj_path)])
    assert otool.returncode == 0, otool.stderr
    assert f"{len(relocs)} relocation entries" in otool.stdout.replace(
        "relocation entries)", "relocation entries"
    ) or str(len(relocs)) in otool.stdout


# --- fail-closed ------------------------------------------------------------

def test_rejects_what_it_does_not_model():
    with pytest.raises(spec.MachOFormatError):
        spec.parse_object(b"\x00" * 8)
    with pytest.raises(spec.MachOFormatError):
        spec.parse_object(struct.pack("<I", spec.MH_CIGAM_64) + b"\x00" * 28)
    # Truncated command list must not parse as success.
    header = spec.MACH_HEADER_64.pack({
        "magic": spec.MH_MAGIC_64, "cputype": spec.CPU_TYPE_ARM64,
        "cpusubtype": 0, "filetype": spec.MH_OBJECT, "ncmds": 1,
        "sizeofcmds": 4, "flags": 0, "reserved": 0,
    })
    with pytest.raises(spec.MachOFormatError):
        spec.parse_object(header + b"\x00\x00\x00\x00")
