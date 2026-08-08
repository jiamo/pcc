"""Merging real cc-produced objects, differentially against `ld -r`.

The earlier relocatable-link suite uses pcc-emitted objects. Real compiler
output brings shapes pcc's own emitter does not produce: section-based
(non-extern) relocations, assembler-local `ltmp` temporaries, a `__LD,
__compact_unwind` section, and static functions.

Proven here: **every** section's payload and relocation table, and the whole
symbol table, match `ld -r` exactly — including `__LD,__compact_unwind`.

That last one took measuring what ld actually does rather than guessing. ld
converts a compact-unwind entry's section-target relocation into a
**symbol-target** one: the function-address field is zeroed and an extern
relocation names the function that owns the address. That survives any later
reordering, where a baked-in address would not. pcc now does the same
whenever a defined symbol sits exactly at the rebased address, and keeps the
section target otherwise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.macho_link import link_relocatable

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc on Darwin arm64"

pytestmark = pytest.mark.pcc_gate(unavailable=_GATE)

UNIT_A = """
static int a(void) { return 1; }
static int b(void) { return 2; }
int (*table[2])(void) = {a, b};
int pick(int i) { return table[i](); }
"""

UNIT_B = """
extern int pick(int);
int go(void) { return pick(0) + pick(1); }
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def _objects(tmp_path: Path) -> tuple[Path, Path]:
    out = []
    for name, source in (("a", UNIT_A), ("b", UNIT_B)):
        src = tmp_path / f"{name}.c"
        src.write_text(source, encoding="utf-8")
        obj = tmp_path / f"{name}.o"
        build = _run([_CC, "-c", "-O1", str(src), "-o", str(obj)])
        assert build.returncode == 0, build.stderr
        out.append(obj)
    return out[0], out[1]


def _shape(data: bytes):
    obj = spec.parse_object(data)
    names = [s["name"] for s in obj.symbols()]
    payloads, relocs = {}, {}
    for sec in obj.sections():
        key = (sec["segname_str"], sec["sectname_str"])
        payloads[key] = obj.data[sec["offset"]:sec["offset"] + sec["size"]]
        relocs[key] = sorted(
            (r["r_address"],
             ("section", r["r_symbolnum"]) if not r["r_extern"]
             else names[r["r_symbolnum"]],
             r["r_type"], r["r_pcrel"], r["r_length"], r["r_extern"])
            for r in obj.relocations(sec)
        )
    symbols = {
        s["name"]: (s["n_type"] & spec.N_TYPE, s["n_sect"], s["n_value"])
        for s in obj.symbols()
    }
    return payloads, relocs, symbols


def _merged_pair(tmp_path: Path):
    a, b = _objects(tmp_path)
    ours = link_relocatable([a.read_bytes(), b.read_bytes()])
    out = tmp_path / "ld_r.o"
    run = _run(["xcrun", "ld", "-r", "-o", str(out), str(a), str(b)])
    assert run.returncode == 0, run.stderr
    return ours, out.read_bytes()


def test_the_input_really_contains_the_shapes_under_test(tmp_path):
    """Otherwise this suite could pass without exercising anything new."""
    a, _b = _objects(tmp_path)
    obj = spec.parse_object(a.read_bytes())
    non_extern = [
        r for sec in obj.sections() for r in obj.relocations(sec)
        if not r["r_extern"]
    ]
    assert non_extern, "cc no longer emits section-based relocations here"
    names = {s["name"] for s in obj.symbols()}
    assert any(n.startswith(("l", "L")) for n in names), (
        "cc no longer emits assembler-local temporaries here"
    )


def test_section_set_matches_ld_r(tmp_path):
    ours, theirs = _merged_pair(tmp_path)
    p_ours, _r, _s = _shape(ours)
    p_theirs, _r2, _s2 = _shape(theirs)
    assert set(p_ours) == set(p_theirs), (sorted(p_ours), sorted(p_theirs))


def test_every_section_matches_ld_r(tmp_path):
    ours, theirs = _merged_pair(tmp_path)
    p_ours, r_ours, _ = _shape(ours)
    p_theirs, r_theirs, _ = _shape(theirs)
    for key in p_theirs:
        assert p_ours[key] == p_theirs[key], (
            f"{key}: payload differs\n  pcc: {p_ours[key].hex()}\n"
            f"  ld:  {p_theirs[key].hex()}"
        )
        assert r_ours[key] == r_theirs[key], (
            f"{key}: relocations differ\n  pcc: {r_ours[key]}\n"
            f"  ld:  {r_theirs[key]}"
        )


def test_symbol_table_matches_ld_r(tmp_path):
    """Including that assembler temporaries are dropped, exactly as ld does."""
    ours, theirs = _merged_pair(tmp_path)
    _p, _r, s_ours = _shape(ours)
    _p2, _r2, s_theirs = _shape(theirs)
    assert s_ours == s_theirs, (sorted(s_ours), sorted(s_theirs))
    assert not any(n.startswith(("l", "L")) for n in s_ours), sorted(s_ours)


def test_compact_unwind_uses_symbol_targets_like_ld(tmp_path):
    """The specific semantic that was measured, pinned on its own.

    A compact-unwind entry must carry a zeroed function-address field plus an
    extern relocation naming the owning function — not a section target with
    the address baked in.
    """
    ours, theirs = _merged_pair(tmp_path)
    for data, tag in ((ours, "pcc"), (theirs, "ld")):
        obj = spec.parse_object(data)
        section = next(
            (s for s in obj.sections() if s["sectname_str"] == "__compact_unwind"),
            None,
        )
        if section is None:
            return
        payload = obj.data[section["offset"]:section["offset"] + section["size"]]
        import struct

        for offset in range(0, len(payload), 32):
            func_addr, = struct.unpack_from("<Q", payload, offset)
            assert func_addr == 0, (tag, offset, func_addr)
        assert all(r["r_extern"] for r in obj.relocations(section)), tag
