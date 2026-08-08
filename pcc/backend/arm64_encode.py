"""A64 instruction encoder for the self backend's own asm vocabulary.

LINK-P1-MACHO-OBJ-SWITCH slice 1: the self path today emits textual asm and
shells out to as(1); direct object emission needs these bytes encoded by pcc.
The vocabulary is the *emitter's*, not the ISA's — the sized inventory
(docs/goal/evidence/2026-08-01-obj-switch-encoder-sized.md) is ~78 mnemonics,
and this module implements the measured core, one operand shape at a time,
exactly as the self backend writes them.

Fail closed everywhere: an unknown mnemonic, register spelling, immediate
range, or operand shape raises `EncodeError`. A silent mis-encoding produces
a plausible instruction stream that crashes far from the bug; a refusal names
the missing shape and points at the differential suite to extend.

The oracle is as(1): `tests/python/test_arm64_encode.py` assembles every
supported shape with the system assembler and byte-compares.

`assemble_text()` is two-pass (labels first, then encoding) and returns the
code bytes plus the extern relocations in `macho_obj` form, so its output
plugs straight into `emit_object`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import macho_spec as spec
from .aarch64_fp_immediates import direct_fp_immediate_encoding
from .macho_obj import DataInCodeRegion, Relocation


class EncodeError(Exception):
    """The instruction is outside the differentially proven subset."""


# --- operand parsing --------------------------------------------------------

_XREG_ALIASES = {"fp": 29, "lr": 30}


def _reg(tok: str) -> tuple[int, bool]:
    """Parse a register -> (number, is64). sp/zr map to 31 in their domains."""
    t = tok.strip().lower().rstrip(",")
    if t == "sp":
        return 31, True
    if t == "wsp":
        return 31, False
    if t == "xzr":
        return 31, True
    if t == "wzr":
        return 31, False
    if t in _XREG_ALIASES:
        return _XREG_ALIASES[t], True
    if t and t[0] in "xw" and t[1:].isdigit():
        n = int(t[1:])
        if 0 <= n <= 30:
            return n, t[0] == "x"
    raise EncodeError(f"bad register {tok!r}")


def _imm(tok: str) -> int:
    t = tok.strip().rstrip(",")
    if not t.startswith("#"):
        raise EncodeError(f"expected immediate, got {tok!r}")
    return int(t[1:], 0)


def _fp_imm8(tok: str) -> int:
    """Encode the direct FP literals emitted by the self backend.

    AArch64's scalar ``fmov`` immediate is an encoded 8-bit floating-point
    value rather than an IEEE bit pattern.  The materializer currently emits
    only these two direct literals; all other constants travel through a GPR.
    Keep this inventory exact and fail closed when the emitter grows.
    """
    t = tok.strip().rstrip(",")
    if not t.startswith("#"):
        raise EncodeError(f"expected floating immediate, got {tok!r}")
    literal = t[1:]
    encoded = direct_fp_immediate_encoding(literal)
    if encoded is None:
        raise EncodeError(f"floating immediate {tok!r} is not proven")
    return encoded


def _vreg8b(tok: str) -> int:
    """Parse 'vN.8b' -> N (the only vector shape the emitter uses: popcount)."""
    t = tok.strip().rstrip(",").lower()
    if t.endswith(".8b") and t.startswith("v") and t[1:-3].isdigit():
        n = int(t[1:-3])
        if 0 <= n <= 31:
            return n
    raise EncodeError(f"bad vector register {tok!r} (only vN.8b is proven)")


def _freg(tok: str) -> tuple[int, str]:
    """Parse a float register -> (number, 'd'|'s')."""
    t = tok.strip().lower().rstrip(",")
    if t and t[0] in "ds" and t[1:].isdigit():
        n = int(t[1:])
        if 0 <= n <= 31:
            return n, t[0]
    raise EncodeError(f"bad float register {tok!r}")


def _reg_kind(tok: str) -> str:
    """'x'/'w'/'d'/'s' for dispatching mixed-domain instructions."""
    t = tok.strip().lower().rstrip(",")
    if t in ("sp", "xzr", "lr", "fp") or (t and t[0] == "x" and t[1:].isdigit()):
        return "x"
    if t in ("wsp", "wzr") or (t and t[0] == "w" and t[1:].isdigit()):
        return "w"
    if t and t[0] == "d" and t[1:].isdigit():
        return "d"
    if t and t[0] == "s" and t[1:].isdigit():
        return "s"
    raise EncodeError(f"bad register {tok!r}")


_COND = {
    "eq": 0, "ne": 1, "cs": 2, "hs": 2, "cc": 3, "lo": 3, "mi": 4, "pl": 5,
    "vs": 6, "vc": 7, "hi": 8, "ls": 9, "ge": 10, "lt": 11, "gt": 12, "le": 13,
}


def _cond(tok: str) -> int:
    t = tok.strip().rstrip(",").lower()
    if t not in _COND:
        raise EncodeError(f"bad condition code {tok!r}")
    return _COND[t]


def _split_operands(rest: str) -> list[str]:
    """Split on commas not inside brackets: 'x0, [x1, #8]' -> ['x0', '[x1, #8]']."""
    out, depth, cur = [], 0, ""
    for ch in rest:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _mem(tok: str) -> tuple[int, int, str]:
    """Parse '[xN]' / '[xN, #imm]' -> (base, imm, mode). mode: '', 'pre', 'post'
    is handled by the caller for ldp/stp."""
    t = tok.strip()
    if not (t.startswith("[") and (t.endswith("]") or t.endswith("]!"))):
        raise EncodeError(f"bad memory operand {tok!r}")
    pre = t.endswith("]!")
    inner = t[1:-2] if pre else t[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    base, base64 = _reg(parts[0])
    if not base64:
        raise EncodeError(f"memory base must be 64-bit: {tok!r}")
    imm = _imm(parts[1]) if len(parts) > 1 else 0
    if len(parts) > 2:
        raise EncodeError(f"unsupported memory operand {tok!r}")
    return base, imm, "pre" if pre else ""


# --- bitmask (logical) immediates -------------------------------------------


def _logical_imm(value: int, is64: bool) -> tuple[int, int, int]:
    """Encode a bitmask immediate -> (N, immr, imms); EncodeError if impossible."""
    size = 64 if is64 else 32
    mask = (1 << size) - 1
    value &= mask
    if value in (0, mask):
        raise EncodeError(f"immediate {value:#x} is not a valid bitmask immediate")
    # Find the smallest element size whose repetition produces the value.
    for esize in (2, 4, 8, 16, 32, 64):
        if esize > size:
            break
        emask = (1 << esize) - 1
        elem = value & emask
        repeated = 0
        for i in range(0, size, esize):
            repeated |= elem << i
        if repeated != value:
            continue
        # elem must be a rotated run of ones: rotate until it is 0..01..1
        for rot in range(esize):
            rotated = ((elem >> rot) | (elem << (esize - rot))) & emask
            ones = bin(rotated).count("1")
            if rotated == (1 << ones) - 1 and 0 < ones < esize:
                immr = (esize - rot) % esize
                imms = ({2: 0x3C, 4: 0x38, 8: 0x30, 16: 0x20, 32: 0x00, 64: 0x00}[esize]
                        | (ones - 1))
                n_bit = 1 if esize == 64 else 0
                return n_bit, immr, imms
    raise EncodeError(f"immediate {value:#x} is not a valid bitmask immediate")


# --- per-family encoders -----------------------------------------------------


def _sf(is64: bool) -> int:
    return 1 << 31 if is64 else 0


def _enc_addsub_reg(op_sub: int, rd, rn, rm, is64: bool, set_flags=0) -> int:
    return (
        _sf(is64) | (op_sub << 30) | (set_flags << 29) | 0x0B000000
        | (rm << 16) | (rn << 5) | rd
    )


def _enc_addsub_ext(op_sub: int, rd, rn, rm, is64: bool, set_flags=0) -> int:
    """ADD/SUB (extended register) — the only register form that means SP.

    In the shifted-register form above, register number 31 decodes as XZR,
    so `sub sp, sp, x15` assembled that way becomes `sub xzr, xzr, x15`: a
    silent no-op that leaves the frame unallocated. Only the extended form
    reads 31 as SP. option=UXTX (64-bit) / UXTW (32-bit), imm3=0.
    """
    option = 3 if is64 else 2
    return (
        _sf(is64) | (op_sub << 30) | (set_flags << 29) | 0x0B200000
        | (rm << 16) | (option << 13) | (rn << 5) | rd
    )


def _is_sp_token(tok: str) -> bool:
    return tok.strip().lower().rstrip(",") in ("sp", "wsp")


def _enc_addsub_imm(op_sub: int, rd, rn, imm: int, is64: bool, set_flags=0) -> int:
    if not 0 <= imm <= 0xFFF:
        raise EncodeError(f"add/sub immediate {imm} out of unsigned 12-bit range")
    return (
        _sf(is64) | (op_sub << 30) | (set_flags << 29) | 0x11000000
        | (imm << 10) | (rn << 5) | rd
    )


def _enc_logical_reg(opc: int, rd, rn, rm, is64: bool) -> int:
    # opc: 0 and, 1 orr, 2 eor, 3 ands
    return _sf(is64) | (opc << 29) | 0x0A000000 | (rm << 16) | (rn << 5) | rd


def _enc_movewide(opc: int, rd, imm16: int, shift: int, is64: bool) -> int:
    # opc: 2 movz, 3 movk
    if not 0 <= imm16 <= 0xFFFF:
        raise EncodeError(f"move-wide immediate {imm16} out of 16-bit range")
    if shift % 16 != 0 or shift // 16 > (3 if is64 else 1):
        raise EncodeError(f"move-wide shift {shift} invalid")
    return (
        _sf(is64) | (opc << 29) | 0x12800000
        | ((shift // 16) << 21) | (imm16 << 5) | rd
    )


def _enc_ldst_unscaled(size: int, opc: int, rt, rn, imm9: int) -> int:
    if not -256 <= imm9 <= 255:
        raise EncodeError(f"unscaled offset {imm9} out of 9-bit signed range")
    return (
        (size << 30) | 0x38000000 | (opc << 22)
        | ((imm9 & 0x1FF) << 12) | (rn << 5) | rt
    )


def _enc_ldst_unsigned(size: int, opc: int, rt, rn, imm: int) -> int:
    scale = size
    if imm % (1 << scale) != 0:
        raise EncodeError(f"offset {imm} not aligned for the access size")
    imm12 = imm >> scale
    if not 0 <= imm12 <= 0xFFF:
        raise EncodeError(f"offset {imm} out of unsigned-offset range")
    return (
        (size << 30) | 0x39000000 | (opc << 22)
        | (imm12 << 10) | (rn << 5) | rt
    )


def _enc_ldstp(load: int, mode: str, rt, rt2, rn, imm: int, is64: bool) -> int:
    scale = 3 if is64 else 2
    if imm % (1 << scale) != 0:
        raise EncodeError(f"pair offset {imm} not aligned")
    imm7 = imm >> scale
    if not -64 <= imm7 <= 63:
        raise EncodeError(f"pair offset {imm} out of 7-bit range")
    base = {"pre": 0x29800000, "post": 0x28C00000, "signed": 0x29000000}[mode]
    if mode == "signed" and load:
        base |= 1 << 22
    elif load:
        base |= 1 << 22
    word = base | ((imm7 & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt
    if is64:
        word |= 0x80000000
    return word


# --- the assembler -----------------------------------------------------------


@dataclass
class AssembledText:
    code: bytes
    relocations: list[Relocation]
    undefined: list[str]
    labels: dict[str, int]
    data_in_code: list[DataInCodeRegion]


def _is_symbol(tok: str) -> bool:
    t = tok.strip()
    return bool(t) and (t[0] == "_" or t[0].isalpha()) and "@" not in t


def assemble_text(asm_text: str) -> AssembledText:
    """Two-pass assembly of one __text body written in the self backend's
    dialect.

    The only directives accepted here are a matched ``.data_region`` /
    ``.end_data_region`` pair and numeric data within it.  Mach-O needs those
    bytes kept in ``__text`` plus an LC_DATA_IN_CODE range; treating them as
    instructions would make disassembly, atomization, and linker transforms
    incorrect.
    """
    lines: list[tuple[str, str | bytes]] = []  # (insn|data, payload)
    labels: dict[str, int] = {}
    data_in_code: list[DataInCodeRegion] = []
    active_data_region: tuple[int, int] | None = None
    pc = 0

    region_kinds = {
        "": spec.DICE_KIND_DATA,
        "jt8": spec.DICE_KIND_JUMP_TABLE8,
        "jt16": spec.DICE_KIND_JUMP_TABLE16,
        "jt32": spec.DICE_KIND_JUMP_TABLE32,
    }

    def encode_inline_data(line: str) -> bytes:
        directive, _, rest = line.partition(" ")
        if directive == ".space":
            try:
                count = int(rest.strip(), 0)
            except ValueError as exc:
                raise EncodeError(f"bad inline {line!r}") from exc
            if count < 0:
                raise EncodeError(f"bad inline {line!r}")
            return b"\0" * count
        widths = {".byte": 1, ".short": 2, ".long": 4, ".quad": 8}
        width = widths[directive]
        out = bytearray()
        for item in (piece.strip() for piece in rest.split(",")):
            if not item:
                continue
            try:
                value = int(item, 0)
            except ValueError as exc:
                raise EncodeError(
                    f"symbol-valued inline data not proven: {line!r}"
                ) from exc
            out += (value & ((1 << (width * 8)) - 1)).to_bytes(
                width, "little"
            )
        return bytes(out)

    for raw in asm_text.splitlines():
        line = raw.split(";")[0].split("//")[0].strip()
        if not line:
            continue
        directive = line.split(None, 1)[0]
        if directive == ".p2align":
            parts = line.split()
            try:
                align_log2 = int(parts[1], 0) if len(parts) == 2 else -1
            except ValueError as exc:
                raise EncodeError(f"bad text alignment {line!r}") from exc
            if not 0 <= align_log2 <= 2:
                raise EncodeError(f"bad text alignment {line!r}")
            mask = (1 << align_log2) - 1
            padding = (-pc) & mask
            if padding:
                if active_data_region is None:
                    raise EncodeError(
                        "non-data text alignment padding is not proven"
                    )
                lines.append(("data", b"\0" * padding))
                pc += padding
            continue
        if directive == ".data_region":
            if active_data_region is not None:
                raise EncodeError("nested .data_region is not proven")
            parts = line.split()
            spelling = parts[1] if len(parts) == 2 else ""
            if len(parts) > 2 or spelling not in region_kinds:
                raise EncodeError(f"bad data-region kind in {line!r}")
            active_data_region = (pc, region_kinds[spelling])
            continue
        if directive == ".end_data_region":
            if line != ".end_data_region":
                raise EncodeError(f"bad data-region terminator {line!r}")
            if active_data_region is None:
                raise EncodeError(".end_data_region without .data_region")
            start, kind = active_data_region
            if pc == start:
                raise EncodeError("empty data-in-code region is not proven")
            data_in_code.append(DataInCodeRegion(start, pc - start, kind))
            active_data_region = None
            continue
        if directive in (".byte", ".short", ".long", ".quad", ".space"):
            if active_data_region is None:
                raise EncodeError(
                    f"inline data outside .data_region: {line!r}"
                )
            raw_data = encode_inline_data(line)
            lines.append(("data", raw_data))
            pc += len(raw_data)
            continue
        if line.startswith("."):
            raise EncodeError(
                f"directive {line.split()[0]!r} reached the instruction "
                "assembler; sections are the caller's job"
            )
        if line.endswith(":"):
            name = line[:-1].strip()
            if name in labels:
                raise EncodeError(f"duplicate label {name!r}")
            labels[name] = pc
            continue
        if active_data_region is not None:
            raise EncodeError("instruction inside .data_region is not proven")
        if pc % 4 != 0:
            raise EncodeError(
                f"instruction at unaligned __text offset {pc} after data region"
            )
        lines.append(("insn", line))
        pc += 4

    if active_data_region is not None:
        raise EncodeError("unterminated .data_region")

    code = bytearray()
    relocations: list[Relocation] = []
    undefined: set[str] = set()

    def resolve_branch(target: str, width_bits: int, at: int) -> int:
        if target not in labels:
            raise EncodeError(f"branch to unknown label {target!r}")
        delta = labels[target] - at
        if delta % 4 != 0:
            raise EncodeError("misaligned branch target")
        words = delta >> 2
        limit = 1 << (width_bits - 1)
        if not -limit <= words < limit:
            raise EncodeError(f"branch to {target!r} out of range")
        return words & ((1 << width_bits) - 1)

    # Atom model (subsections-via-symbols): every non-L label starts an atom.
    # A branch whose target lives in the SAME atom as the branch site has a
    # fixed offset no matter how the linker reorders subsections, so as(1)
    # resolves it inline (the recursive-call case); any cross-atom call gets
    # a BRANCH26 relocation instead. Verified against as(1) on both shapes.
    atom_starts = sorted(
        off for name, off in labels.items() if not name.startswith("L")
    )

    def same_atom(target: str, at: int) -> bool:
        if target not in labels or target.startswith("L"):
            return False
        enclosing = 0
        for start in atom_starts:
            if start <= at:
                enclosing = start
            else:
                break
        return labels[target] == enclosing

    for kind, payload in lines:
        if kind == "data":
            code += payload
            continue
        at = len(code)
        word = _encode_one(
            payload, at, labels, resolve_branch, relocations, undefined,
            same_atom,
        )
        code += word.to_bytes(4, "little")

    return AssembledText(
        code=bytes(code),
        relocations=relocations,
        undefined=sorted(undefined),
        labels=labels,
        data_in_code=data_in_code,
    )


def _encode_one(line, at, labels, resolve_branch, relocations, undefined,
                same_atom) -> int:
    parts = line.split(None, 1)
    mn = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    ops = _split_operands(rest)

    if mn == "ret":
        return 0xD65F03C0
    if mn == "nop":
        if ops:
            raise EncodeError(f"nop takes no operands: {line!r}")
        return 0xD503201F
    if mn == "paciasp":
        return 0xD503233F
    if mn == "autiasp":
        return 0xD50323BF

    if mn in ("add", "sub", "adds", "subs"):
        op_sub = 1 if mn.startswith("sub") else 0
        set_flags = 1 if mn in ("adds", "subs") else 0
        rd, d64 = _reg(ops[0])
        rn, n64 = _reg(ops[1])
        third = ops[2]
        if "@PAGEOFF" in third:
            if set_flags:
                raise EncodeError(f"{mn} with @PAGEOFF not proven")
            symbol = third.split("@")[0]
            undefined_or_local(symbol, labels, undefined)
            relocations.append(Relocation(
                at, symbol, spec.ARM64_RELOC_PAGEOFF12, pcrel=False))
            return _enc_addsub_imm(op_sub, rd, rn, 0, d64)
        if third.startswith("#"):
            return _enc_addsub_imm(op_sub, rd, rn, _imm(third), d64, set_flags)
        rm, _ = _reg(third)
        if _is_sp_token(ops[0]) or _is_sp_token(ops[1]):
            return _enc_addsub_ext(op_sub, rd, rn, rm, d64, set_flags)
        return _enc_addsub_reg(op_sub, rd, rn, rm, d64, set_flags)

    if mn == "cmp":
        rn, n64 = _reg(ops[0])
        if ops[1].startswith("#"):
            return _enc_addsub_imm(1, 31, rn, _imm(ops[1]), n64, set_flags=1)
        rm, _ = _reg(ops[1])
        if _is_sp_token(ops[0]):
            return _enc_addsub_ext(1, 31, rn, rm, n64, set_flags=1)
        return _enc_addsub_reg(1, 31, rn, rm, n64, set_flags=1)

    if mn in ("and", "orr", "eor"):
        opc = {"and": 0, "orr": 1, "eor": 2}[mn]
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        if ops[2].startswith("#"):
            if mn != "and":
                raise EncodeError(f"{mn} immediate not in the proven subset")
            n_bit, immr, imms = _logical_imm(_imm(ops[2]), d64)
            return (
                _sf(d64) | 0x12000000 | (n_bit << 22)
                | (immr << 16) | (imms << 10) | (rn << 5) | rd
            )
        rm, _ = _reg(ops[2])
        return _enc_logical_reg(opc, rd, rn, rm, d64)

    if mn == "mov":
        rd_tok = ops[0].strip().lower()
        rd, d64 = _reg(ops[0])
        rm_tok = ops[1].strip().lower()
        rm, m64 = _reg(ops[1])
        if d64 != m64:
            raise EncodeError(f"mov register widths differ: {line!r}")
        if rd_tok in ("sp", "wsp") or rm_tok in ("sp", "wsp"):
            # involves sp: alias of add #0
            return _enc_addsub_imm(0, rd, rm, 0, d64)
        return _enc_logical_reg(1, rd, 31, rm, d64)

    if mn in ("movz", "movk"):
        opc = 2 if mn == "movz" else 3
        rd, d64 = _reg(ops[0])
        imm16 = _imm(ops[1])
        shift = 0
        if len(ops) > 2:
            lsl = ops[2].split()
            if lsl[0].lower() != "lsl":
                raise EncodeError(f"bad move-wide shift {ops[2]!r}")
            shift = _imm(lsl[1])
        return _enc_movewide(opc, rd, imm16, shift, d64)

    if mn in ("asrv", "lslv", "lsrv"):
        op2 = {"lslv": 0b1000, "lsrv": 0b1001, "asrv": 0b1010}[mn]
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        rm, _ = _reg(ops[2])
        return (
            _sf(d64) | 0x1AC00000 | (rm << 16) | (op2 << 10) | (rn << 5) | rd
        )

    if mn == "adrp":
        rd, _ = _reg(ops[0])
        target = ops[1]
        if "@GOTPAGE" in target:
            symbol = target.split("@")[0]
            reloc_type = spec.ARM64_RELOC_GOT_LOAD_PAGE21
        elif "@PAGE" in target:
            symbol = target.split("@")[0]
            reloc_type = spec.ARM64_RELOC_PAGE21
        else:
            raise EncodeError(f"adrp needs @PAGE/@GOTPAGE: {line!r}")
        undefined_or_local(symbol, labels, undefined)
        relocations.append(Relocation(at, symbol, reloc_type, pcrel=True))
        return 0x90000000 | rd

    if (
        mn in ("ldur", "stur")
        and _reg_kind(ops[0]) in ("d", "s")
    ):
        rt, kind = _freg(ops[0])
        base, imm, mode = _mem(ops[1])
        if mode:
            raise EncodeError(f"{mn} with writeback not in the proven subset")
        size = 3 if kind == "d" else 2
        load = 1 if mn == "ldur" else 0
        # SIMD/FP unscaled load/store is the integer encoding with V=1.
        return _enc_ldst_unscaled(size, load, rt, base, imm) | (1 << 26)

    if mn in ("ldur", "stur", "ldurb", "sturb"):
        rt, t64 = _reg(ops[0])
        base, imm, mode = _mem(ops[1])
        if mode:
            raise EncodeError(f"{mn} with writeback not in the proven subset")
        if mn.endswith("b"):
            size, opc = 0, (1 if mn == "ldurb" else 0)
        else:
            size, opc = (3 if t64 else 2), (1 if mn == "ldur" else 0)
        return _enc_ldst_unscaled(size, opc, rt, base, imm)

    if mn in ("ldar", "ldaxr", "stlr", "ldaxrb", "stlrb"):
        rt, t64 = _reg(ops[0])
        base, imm, mode = _mem(ops[1])
        if imm or mode:
            raise EncodeError(f"{mn} takes a bare [Xn] address: {line!r}")
        if mn.endswith("b"):
            # byte variants: size bits 00 instead of 10/11
            if t64:
                raise EncodeError(f"{mn} takes a W register: {line!r}")
            base_op = {"ldaxrb": 0x085FFC00, "stlrb": 0x089FFC00}[mn]
            return base_op | (base << 5) | rt
        sf_bit = 0x40000000 if t64 else 0
        if mn == "ldar":
            return 0x88DFFC00 | sf_bit | (base << 5) | rt
        if mn == "ldaxr":
            return 0x885FFC00 | sf_bit | (base << 5) | rt
        return 0x889FFC00 | sf_bit | (base << 5) | rt

    if mn in ("stlxr", "stlxrb"):
        rs, s64 = _reg(ops[0])
        if s64:
            raise EncodeError(f"{mn} status register must be a W register: {line!r}")
        rt, t64 = _reg(ops[1])
        base, imm, mode = _mem(ops[2])
        if imm or mode:
            raise EncodeError(f"{mn} takes a bare [Xn] address: {line!r}")
        if mn == "stlxrb":
            if t64:
                raise EncodeError(f"stlxrb takes a W data register: {line!r}")
            return 0x0800FC00 | (rs << 16) | (base << 5) | rt
        sf_bit = 0x40000000 if t64 else 0
        return 0x8800FC00 | sf_bit | (rs << 16) | (base << 5) | rt

    if mn == "clrex":
        if ops:
            raise EncodeError(f"clrex operands not proven: {line!r}")
        return 0xD5033F5F

    if mn == "dmb":
        if len(ops) != 1 or ops[0].strip().lower() != "ish":
            raise EncodeError(f"dmb domain not in the proven subset: {line!r}")
        return 0xD5033BBF

    if mn in ("ldr", "str") and _reg_kind(ops[0]) in ("d", "s"):
        rt, kind = _freg(ops[0])
        base, imm, mode = _mem(ops[1])
        if mode:
            raise EncodeError(f"{mn} with writeback not in the proven subset")
        size = 3 if kind == "d" else 2
        load = 1 if mn == "ldr" else 0
        # SIMD/FP unsigned-offset load/store is the integer encoding with V=1.
        return _enc_ldst_unsigned(size, load, rt, base, imm) | (1 << 26)

    if mn in ("ldr", "str", "ldrb", "strb"):
        rt, t64 = _reg(ops[0])
        memop = ops[1]
        if "@GOTPAGEOFF" in memop:
            inner = memop.strip()[1:-1]
            base_tok, sym_tok = [p.strip() for p in inner.split(",")]
            base, _ = _reg(base_tok)
            symbol = sym_tok.split("@")[0]
            undefined_or_local(symbol, labels, undefined)
            relocations.append(Relocation(
                at, symbol, spec.ARM64_RELOC_GOT_LOAD_PAGEOFF12, pcrel=False))
            if mn != "ldr" or not t64:
                raise EncodeError("@GOTPAGEOFF only proven for 64-bit ldr")
            return _enc_ldst_unsigned(3, 1, rt, base, 0)
        base, imm, mode = _mem(memop)
        if mode:
            raise EncodeError(f"{mn} with writeback not in the proven subset")
        if mn.endswith("b"):
            size, opc = 0, (1 if mn == "ldrb" else 0)
        else:
            size, opc = (3 if t64 else 2), (1 if mn == "ldr" else 0)
        return _enc_ldst_unsigned(size, opc, rt, base, imm)

    if mn in ("ldp", "stp"):
        load = 1 if mn == "ldp" else 0
        rt, t64 = _reg(ops[0])
        rt2, _ = _reg(ops[1])
        memop = ops[2]
        post_imm = None
        if len(ops) > 3:  # post-index: [sp], #16
            post_imm = _imm(ops[3])
        base, imm, mode = _mem(memop)
        if post_imm is not None:
            if mode:
                raise EncodeError("both pre and post index")
            return _enc_ldstp(load, "post", rt, rt2, base, post_imm, t64)
        if mode == "pre":
            return _enc_ldstp(load, "pre", rt, rt2, base, imm, t64)
        return _enc_ldstp(load, "signed", rt, rt2, base, imm, t64)

    if mn == "b":
        target = ops[0]
        if _is_symbol(target) and target not in labels:
            raise EncodeError(f"b to external symbol {target!r} not proven")
        return 0x14000000 | resolve_branch(target, 26, at)

    if mn == "bl":
        target = ops[0]
        # Only assembler-local (L-prefixed) labels resolve inline. A call to
        # any real symbol gets a BRANCH26 relocation even when the target is
        # defined in the same file: .subsections_via_symbols lets the linker
        # reorder/dead-strip per-symbol subsections, so the offset cannot be
        # baked in (verified against as(1) on a same-file bl).
        if (target.startswith("L") and target in labels) or same_atom(target, at):
            return 0x94000000 | resolve_branch(target, 26, at)
        if target not in labels:
            undefined.add(target)
        relocations.append(Relocation(
            at, target, spec.ARM64_RELOC_BRANCH26, pcrel=True))
        return 0x94000000

    if mn.startswith("b.") and len(mn) == 4:
        return 0x54000000 | (resolve_branch(ops[0], 19, at) << 5) | _cond(mn[2:])

    if mn in ("cbz", "cbnz"):
        rt, t64 = _reg(ops[0])
        base = 0x35000000 if mn == "cbnz" else 0x34000000
        word = base | (resolve_branch(ops[1], 19, at) << 5) | rt
        if t64:
            word |= 0x80000000
        return word

    if mn == "csel":
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        rm, _ = _reg(ops[2])
        return (
            _sf(d64) | 0x1A800000 | (rm << 16)
            | (_cond(ops[3]) << 12) | (rn << 5) | rd
        )

    if mn == "cset":
        # alias of csinc rd, zr, zr, invert(cond)
        rd, d64 = _reg(ops[0])
        inv = _cond(ops[1]) ^ 1
        return (
            _sf(d64) | 0x1A800400 | (31 << 16) | (inv << 12) | (31 << 5) | rd
        )

    if mn in ("mul", "sdiv", "udiv", "smulh", "umulh"):
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        rm, _ = _reg(ops[2])
        if mn == "mul":  # alias of madd rd, rn, rm, zr
            return _sf(d64) | 0x1B007C00 | (rm << 16) | (rn << 5) | rd
        if mn in ("smulh", "umulh"):
            if not d64:
                raise EncodeError(f"{mn} is 64-bit only")
            u = 1 << 23 if mn == "umulh" else 0
            return 0x9B407C00 | u | (rm << 16) | (rn << 5) | rd
        return _sf(d64) | 0x1AC00800 | (rm << 16) | ((mn == "sdiv") << 10) | (rn << 5) | rd

    if mn in ("smull", "umull"):
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        rm, _ = _reg(ops[2])
        if not d64:
            raise EncodeError(f"{mn} destination must be 64-bit")
        u = 1 << 23 if mn == "umull" else 0
        return 0x9B207C00 | u | (rm << 16) | (rn << 5) | rd

    if mn in ("madd", "msub"):
        if len(ops) != 4:
            raise EncodeError(f"{mn} expects four same-width GPR operands")
        for operand in ops:
            if operand.strip().lower() == "sp":
                raise EncodeError(f"{mn} does not accept sp")
        rd, d64 = _reg(ops[0])
        rn, n64 = _reg(ops[1])
        rm, m64 = _reg(ops[2])
        ra, a64 = _reg(ops[3])
        if not (d64 == n64 == m64 == a64):
            raise EncodeError(f"{mn} expects four same-width GPR operands")
        if mn == "madd" and not d64:
            raise EncodeError("madd is proven only for the AArch64 i64 slice")
        subtract = 1 << 15 if mn == "msub" else 0
        return (
            _sf(d64)
            | 0x1B000000
            | subtract
            | (rm << 16)
            | (ra << 10)
            | (rn << 5)
            | rd
        )

    if mn == "neg":  # alias of sub rd, zr, rm
        rd, d64 = _reg(ops[0])
        rm, _ = _reg(ops[1])
        return _enc_addsub_reg(1, rd, 31, rm, d64)

    if mn in ("asr", "lsr", "lsl") and ops[2].startswith("#"):
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        sh = _imm(ops[2])
        width = 64 if d64 else 32
        if not 0 <= sh < width:
            raise EncodeError(f"{mn} shift {sh} out of range")
        n_bit = (1 << 22) if d64 else 0
        if mn == "asr":   # sbfm rd, rn, #sh, #width-1
            return _sf(d64) | 0x13000000 | n_bit | (sh << 16) | ((width - 1) << 10) | (rn << 5) | rd
        if mn == "lsr":   # ubfm rd, rn, #sh, #width-1
            return _sf(d64) | 0x53000000 | n_bit | (sh << 16) | ((width - 1) << 10) | (rn << 5) | rd
        # lsl: ubfm rd, rn, #(width-sh)%width, #(width-1-sh)
        immr = (width - sh) % width
        imms = width - 1 - sh
        return _sf(d64) | 0x53000000 | n_bit | (immr << 16) | (imms << 10) | (rn << 5) | rd

    if mn == "tst":  # alias of ands zr, rn, rm
        rn, n64 = _reg(ops[0])
        rm, _ = _reg(ops[1])
        return _enc_logical_reg(3, 31, rn, rm, n64)

    if mn in ("sxtb", "sxth", "sxtw"):
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        imms = {"sxtb": 7, "sxth": 15, "sxtw": 31}[mn]
        n_bit = (1 << 22) if d64 else 0
        return _sf(d64) | 0x13000000 | n_bit | (imms << 10) | (rn << 5) | rd

    if mn in ("clz", "rbit", "rev", "rev16"):
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        op2 = {"rbit": 0, "rev16": 1, "rev": 3 if d64 else 2, "clz": 4}[mn]
        return _sf(d64) | 0x5AC00000 | (op2 << 10) | (rn << 5) | rd

    if mn == "cneg":  # alias of csneg rd, rn, rn, invert(cond)
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        inv = _cond(ops[2]) ^ 1
        return _sf(d64) | 0x5A800400 | (rn << 16) | (inv << 12) | (rn << 5) | rd

    if mn == "csinv":
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        rm, _ = _reg(ops[2])
        return _sf(d64) | 0x5A800000 | (rm << 16) | (_cond(ops[3]) << 12) | (rn << 5) | rd

    if mn == "blr":
        rn, n64 = _reg(ops[0])
        if not n64:
            raise EncodeError("blr needs a 64-bit register")
        return 0xD63F0000 | (rn << 5)

    if mn == "brk":
        imm = _imm(ops[0])
        if not 0 <= imm <= 0xFFFF:
            raise EncodeError(f"brk immediate {imm} out of range")
        return 0xD4200000 | (imm << 5)

    # --- floating point (double, with fcvt/scvtf/fcvtzs domain crossings) ---

    if mn in ("fadd", "fsub", "fmul", "fdiv"):
        rd, kd = _freg(ops[0])
        rn, _ = _freg(ops[1])
        rm, _ = _freg(ops[2])
        if kd != "d":
            raise EncodeError(f"{mn} only proven for double precision")
        opc = {"fmul": 0x0800, "fdiv": 0x1800, "fadd": 0x2800, "fsub": 0x3800}[mn]
        return 0x1E600000 | opc | (rm << 16) | (rn << 5) | rd

    if mn in ("fneg", "fabs", "fsqrt"):
        rd, kd = _freg(ops[0])
        rn, _ = _freg(ops[1])
        if kd != "d":
            raise EncodeError(f"{mn} only proven for double precision")
        opc = {"fabs": 0x0C000, "fneg": 0x14000, "fsqrt": 0x1C000}[mn]
        return 0x1E600000 | opc | (rn << 5) | rd

    if mn == "fmov":
        if ops[1].strip().startswith("#"):
            rd, kd = _freg(ops[0])
            imm8 = _fp_imm8(ops[1])
            base = 0x1E601000 if kd == "d" else 0x1E201000
            return base | (imm8 << 13) | rd
        kd = _reg_kind(ops[0])
        ks = _reg_kind(ops[1])
        if kd == "d" and ks == "d":
            rd, _ = _freg(ops[0]); rn, _ = _freg(ops[1])
            return 0x1E604000 | (rn << 5) | rd
        if kd == "d" and ks == "x":
            rd, _ = _freg(ops[0]); rn, _ = _reg(ops[1])
            return 0x9E670000 | (rn << 5) | rd
        if kd == "x" and ks == "d":
            rd, _ = _reg(ops[0]); rn, _ = _freg(ops[1])
            return 0x9E660000 | (rn << 5) | rd
        raise EncodeError(f"fmov shape {line!r} not proven")

    if mn == "fcvt":
        rd, kd = _freg(ops[0])
        rn, ks = _freg(ops[1])
        if kd == "d" and ks == "s":
            return 0x1E22C000 | (rn << 5) | rd
        if kd == "s" and ks == "d":
            return 0x1E624000 | (rn << 5) | rd
        raise EncodeError(f"fcvt shape {line!r} not proven")

    if mn in ("scvtf", "ucvtf"):
        rd, kd = _freg(ops[0])
        rn, n64 = _reg(ops[1])
        if kd != "d":
            raise EncodeError(f"{mn} only proven for double destinations")
        u = 0x10000 if mn == "ucvtf" else 0
        return (0x9E620000 if n64 else 0x1E620000) | u | (rn << 5) | rd

    if mn in ("fcvtzs", "fcvtzu"):
        rd, d64 = _reg(ops[0])
        rn, ks = _freg(ops[1])
        if ks != "d":
            raise EncodeError(f"{mn} only proven from double sources")
        u = 0x10000 if mn == "fcvtzu" else 0
        return (0x9E780000 if d64 else 0x1E780000) | u | (rn << 5) | rd

    if mn == "uxtw":  # zero-extend w -> x
        rd, d64 = _reg(ops[0])
        rn, _ = _reg(ops[1])
        if not d64:
            raise EncodeError("uxtw destination must be 64-bit")
        # ubfm xd, xn, #0, #31
        return 0xD3407C00 | (rn << 5) | rd

    if mn == "cnt":  # popcount lane: cnt vN.8b, vM.8b
        rd = _vreg8b(ops[0])
        rn = _vreg8b(ops[1])
        return 0x0E205800 | (rn << 5) | rd

    if mn == "addv":  # addv bN, vM.8b
        bd = ops[0].strip().rstrip(",").lower()
        if not (bd.startswith("b") and bd[1:].isdigit()):
            raise EncodeError(f"addv destination {ops[0]!r} not proven")
        rd = int(bd[1:])
        rn = _vreg8b(ops[1])
        return 0x0E31B800 | (rn << 5) | rd

    if mn == "umov":  # umov wN, vM.b[0]
        rd, d64 = _reg(ops[0])
        if d64:
            raise EncodeError("umov only proven for w destinations")
        src = ops[1].strip().lower()
        if not src.endswith(".b[0]"):
            raise EncodeError(f"umov source {ops[1]!r} not proven")
        rn = _vreg8b(src.split(".")[0] + ".8b")
        return 0x0E013C00 | (rn << 5) | rd

    if mn == "fcmp":
        rn, kn = _freg(ops[0])
        if kn != "d":
            raise EncodeError("fcmp only proven for double precision")
        if ops[1].startswith("#"):
            if float(ops[1][1:]) != 0.0:
                raise EncodeError("fcmp immediate must be #0.0")
            return 0x1E602008 | (rn << 5)
        rm, _ = _freg(ops[1])
        return 0x1E602000 | (rm << 16) | (rn << 5)

    if mn == "fcsel":
        rd, kd = _freg(ops[0])
        rn, _ = _freg(ops[1])
        rm, _ = _freg(ops[2])
        if kd != "d":
            raise EncodeError("fcsel only proven for double precision")
        return 0x1E600C00 | (rm << 16) | (_cond(ops[3]) << 12) | (rn << 5) | rd

    raise EncodeError(f"mnemonic {mn!r} not in the proven subset: {line!r}")


def undefined_or_local(symbol: str, labels: dict, undefined: set) -> None:
    if symbol not in labels:
        undefined.add(symbol)
