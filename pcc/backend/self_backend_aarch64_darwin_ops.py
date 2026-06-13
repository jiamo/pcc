from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_abi import (
    abi_value_reg_names,
    aggregate_fits_reg_abi,
    reg_name,
)
from .self_backend_ir import TypeDesc


def emit_binop(op: str, value_type: TypeDesc) -> list[str]:
    if not value_type.is_int:
        raise BackendUnavailable(
            f"self backend only supports integer binops, got {value_type.describe()}"
        )
    r9 = reg_name(value_type, 9)
    r10 = reg_name(value_type, 10)
    r11 = reg_name(value_type, 11)
    mapping = {
        "add": f"  add {r11}, {r9}, {r10}",
        "sub": f"  sub {r11}, {r9}, {r10}",
        "mul": f"  mul {r11}, {r9}, {r10}",
        "sdiv": f"  sdiv {r11}, {r9}, {r10}",
        "udiv": f"  udiv {r11}, {r9}, {r10}",
        "and": f"  and {r11}, {r9}, {r10}",
        "or": f"  orr {r11}, {r9}, {r10}",
        "xor": f"  eor {r11}, {r9}, {r10}",
        "shl": f"  lslv {r11}, {r9}, {r10}",
        "lshr": f"  lsrv {r11}, {r9}, {r10}",
        "ashr": f"  asrv {r11}, {r9}, {r10}",
    }
    if op == "srem":
        return [
            f"  sdiv {r11}, {r9}, {r10}",
            f"  msub {r11}, {r11}, {r10}, {r9}",
        ]
    if op == "urem":
        return [
            f"  udiv {r11}, {r9}, {r10}",
            f"  msub {r11}, {r11}, {r10}, {r9}",
        ]
    if op not in mapping:
        raise BackendUnavailable(f"self backend does not support binop {op!r}")
    return [mapping[op]]


def emit_aggregate_bitwise_binop(
    op: str,
    value_type: TypeDesc,
    *,
    lhs_start: int,
    rhs_start: int,
    dest_start: int,
) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        raise BackendUnavailable(
            f"self backend aggregate bitwise binop expected aggregate type, got {value_type.describe()}"
        )
    if not aggregate_fits_reg_abi(value_type):
        raise BackendUnavailable(
            "self backend aggregate bitwise binop currently requires register-fit aggregates, got "
            f"{value_type.describe()}"
        )
    mnemonic = {"and": "and", "or": "orr", "xor": "eor"}.get(op)
    if mnemonic is None:
        raise BackendUnavailable(
            f"self backend aggregate binop currently only supports and/or/xor, got {op!r}"
        )
    lines: list[str] = []
    for lhs_reg, rhs_reg, dest_reg in zip(
        abi_value_reg_names(value_type, lhs_start),
        abi_value_reg_names(value_type, rhs_start),
        abi_value_reg_names(value_type, dest_start),
    ):
        lines.append(f"  {mnemonic} {dest_reg}, {lhs_reg}, {rhs_reg}")
    return lines


def emit_fbinop(op: str, value_type: TypeDesc) -> list[str]:
    if not value_type.is_fp:
        raise BackendUnavailable(
            f"self backend only supports floating binops on fp values, got {value_type.describe()}"
        )
    r9 = reg_name(value_type, 9)
    r10 = reg_name(value_type, 10)
    r11 = reg_name(value_type, 11)
    mapping = {
        "fadd": f"  fadd {r11}, {r9}, {r10}",
        "fsub": f"  fsub {r11}, {r9}, {r10}",
        "fmul": f"  fmul {r11}, {r9}, {r10}",
        "fdiv": f"  fdiv {r11}, {r9}, {r10}",
    }
    if op not in mapping:
        raise BackendUnavailable(f"self backend does not support floating binop {op!r}")
    return [mapping[op]]


def emit_cast(op: str, src_type: TypeDesc, dst_type: TypeDesc) -> list[str]:
    src9 = reg_name(src_type, 9)
    dst10 = reg_name(dst_type, 10)

    if op == "bitcast":
        if src_type.bits != dst_type.bits:
            raise BackendUnavailable(
                f"self backend bitcast requires same-size scalars, got {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.is_fp and dst_type.is_int:
            return [f"  fmov {dst10}, {src9}"]
        if src_type.is_int and dst_type.is_fp:
            return [f"  fmov {dst10}, {src9}"]
        return [f"  mov {dst10}, {src9}"]

    if op == "fpext":
        if not src_type.is_fp or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend fpext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvt {dst10}, {src9}"]

    if op == "fptrunc":
        if not src_type.is_fp or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend fptrunc mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvt {dst10}, {src9}"]

    if op == "ptrtoint":
        if not src_type.is_ptr or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend ptrtoint mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if dst_type.width > 32:
            return [f"  mov {dst10}, x9"]
        return [f"  mov {dst10}, w9"]

    if op == "inttoptr":
        if not src_type.is_int or not dst_type.is_ptr:
            raise BackendUnavailable(
                f"self backend inttoptr mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  mov x10, x9"]
        return [f"  mov w10, w9"]

    if op == "trunc":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend trunc mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width <= dst_type.width:
            raise BackendUnavailable(
                f"self backend trunc expects narrowing cast, got {src_type.describe()} -> {dst_type.describe()}"
            )
        if dst_type.width <= 32:
            return [f"  mov {dst10}, w9"]
        return [f"  mov {dst10}, x9"]

    if op == "zext":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend zext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width == 1:
            return ["  and w10, w9, #1"]
        if src_type.width <= 32 and dst_type.width > 32:
            return ["  mov w10, w9"]
        return [f"  mov {dst10}, {src9}"]

    if op == "sext":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend sext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width == 8:
            return ["  sxtb x10, w9" if dst_type.width > 32 else "  sxtb w10, w9"]
        if src_type.width == 16:
            return ["  sxth x10, w9" if dst_type.width > 32 else "  sxth w10, w9"]
        if src_type.width == 32 and dst_type.width > 32:
            return ["  sxtw x10, w9"]
        if src_type.width == 1:
            return (
                [
                    "  and w10, w9, #1",
                    "  neg w10, w10",
                ]
                if dst_type.width <= 32
                else [
                    "  and w10, w9, #1",
                    "  neg x10, x10",
                ]
            )
        return [f"  mov {dst10}, {src9}"]

    if op == "sitofp":
        if not src_type.is_int or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend sitofp mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  scvtf {dst10}, x9"]
        return [f"  scvtf {dst10}, w9"]

    if op == "uitofp":
        if not src_type.is_int or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend uitofp mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  ucvtf {dst10}, x9"]
        return [f"  ucvtf {dst10}, w9"]

    if op == "fptosi":
        if not src_type.is_fp or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend fptosi mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvtzs {dst10}, {src9}"]

    if op == "fptoui":
        if not src_type.is_fp or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend fptoui mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvtzu {dst10}, {src9}"]

    raise BackendUnavailable(f"self backend does not support cast op {op!r}")


def sign_extend_int_reg(value_type: TypeDesc, reg: str) -> list[str]:
    if not value_type.is_int:
        raise BackendUnavailable(
            f"self backend sign-extension helper expects int type, got {value_type.describe()}"
        )
    width = value_type.width
    if width >= 32:
        return []
    if width == 1:
        return [
            f"  and {reg}, {reg}, #1",
            f"  neg {reg}, {reg}",
        ]
    if width == 8:
        return [f"  sxtb {reg}, {reg}"]
    if width == 16:
        return [f"  sxth {reg}, {reg}"]
    shift = 32 - width
    return [
        f"  lsl {reg}, {reg}, #{shift}",
        f"  asr {reg}, {reg}, #{shift}",
    ]


def aarch64_cc(cond: str) -> str:
    mapping = {
        "eq": "eq",
        "ne": "ne",
        "slt": "lt",
        "sle": "le",
        "sgt": "gt",
        "sge": "ge",
        "ult": "lo",
        "ule": "ls",
        "ugt": "hi",
        "uge": "hs",
    }
    if cond not in mapping:
        raise BackendUnavailable(f"self backend does not support icmp {cond!r}")
    return mapping[cond]


def emit_fcmp_result(cond: str) -> list[str]:
    direct = {
        "oeq": "eq",
        "ogt": "gt",
        "oge": "ge",
        "olt": "mi",
        "ole": "ls",
        "ord": "vc",
        "une": "ne",
        "ugt": "hi",
        "uge": "pl",
        "ult": "lt",
        "ule": "le",
        "uno": "vs",
    }
    if cond in direct:
        return [f"  cset w11, {direct[cond]}"]
    if cond == "ueq":
        return [
            "  cset w11, eq",
            "  cset w12, vs",
            "  orr w11, w11, w12",
        ]
    if cond == "one":
        return [
            "  cset w11, mi",
            "  cset w12, gt",
            "  orr w11, w11, w12",
        ]
    raise BackendUnavailable(f"self backend does not support fcmp {cond!r}")
