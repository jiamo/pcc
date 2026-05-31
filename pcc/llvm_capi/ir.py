"""pcc.llvm_capi.ir — text-first IR builder (β4.1 Tier 1-3).

Drop-in replacement for ``llvmlite.ir`` on the pcc codegen path.
Constructs LLVM IR as text incrementally (no object graph), then
``str(module)`` yields a complete IR string which downstream passes
(``llvmlite.binding.parse_assembly`` or ``pcc.llvm_capi.parse``)
consume.

API parity (Tier 1-3) with ``llvmlite.ir`` — same class names, same
method signatures — so codegen call sites are **zero-change** when
switching via ``PCC_USE_LLVMCAPI=1``.

**Scope exclusions** (β4.3 work, not here):
- Debug info (``DIToken``, ``module.add_debug_info``): raises NotImplementedError
- Metadata ``!N`` auto-numbering: skeleton only, not byte-perfect to llvmlite
- Rare ir.* long-tail: use ``__getattr__`` shim that raises clear errors

The implementation philosophy matches ``pcc.parse.py_parse`` /
``c_parse_driver``: static typed Python, no heavy reflection, ready
for self-host compilation.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Optional


def _hex64(value: int) -> str:
    digits = "0123456789ABCDEF"
    out = ""
    shift = 60
    while shift >= 0:
        out += digits[(value >> shift) & 15]
        shift -= 4
    return "0x" + out


def _float64_to_bits_ir(f: float) -> int:
    """Return the IEEE 754 binary64 bit pattern using pcc-friendly ops."""
    if f != f:
        return 0x7FF8000000000000
    inf = 1e309
    if f == inf:
        return 0x7FF0000000000000
    if f == -inf:
        return 0xFFF0000000000000
    if f == 0.0:
        text = str(f)
        if len(text) > 0 and text[0] == "-":
            return 0x8000000000000000
        return 0
    sign = 0
    if f < 0.0:
        sign = 1
        f = -f
    exp = 0
    while f >= 2.0:
        f = f * 0.5
        exp += 1
    while f < 1.0:
        f = f * 2.0
        exp -= 1
    mantissa_bits = int((f - 1.0) * 4503599627370496.0)
    biased_exp = exp + 1023
    if biased_exp <= 0:
        return sign << 63
    if biased_exp >= 0x7FF:
        return (sign << 63) | 0x7FF0000000000000
    return (sign << 63) | (biased_exp << 52) | mantissa_bits


def _bits_to_float64_ir(bits: int) -> float:
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    inf = 1e309
    if biased_exp == 0x7FF:
        if mantissa == 0:
            return -inf if sign else inf
        return inf - inf
    if biased_exp == 0:
        if mantissa == 0:
            return -0.0 if sign else 0.0
        f = mantissa / 4503599627370496.0
        i = 0
        while i < 11:
            f = f * 0.0009765625
            i += 1
        f = f * 2.0
        return -f if sign else f
    m_frac = 1.0 + mantissa / 4503599627370496.0
    exp = biased_exp - 1023
    f = m_frac
    if exp >= 0:
        i = 0
        while i < exp:
            f = f * 2.0
            i += 1
    else:
        i = 0
        while i < -exp:
            f = f * 0.5
            i += 1
    return -f if sign else f


def _env_flag_enabled(name: str) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


_DEBUG_IR_RENDER_ENABLED = _env_flag_enabled("PCC_DEBUG_IR_RENDER")
_DEBUG_IR_CALL_ENABLED = _env_flag_enabled("PCC_DEBUG_IR_CALL")


def _debug_ir_render_enabled() -> bool:
    return _DEBUG_IR_RENDER_ENABLED


def _debug_ir_call_enabled() -> bool:
    return _DEBUG_IR_CALL_ENABLED


def _debug_ir_render_enabled_uncached() -> bool:
    value = str(os.environ.get("PCC_DEBUG_IR_RENDER", "") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _debug_ir_call_enabled_uncached() -> bool:
    value = str(os.environ.get("PCC_DEBUG_IR_CALL", "") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _join_text(parts, sep: str) -> str:
    out_parts = []
    i = 0
    while i < len(parts):
        out_parts.append(str(parts[i]))
        i += 1
    return sep.join(out_parts)


def _round_to_float32_ir(f: float) -> float:
    inf = 1e309
    if f != f or f == inf or f == -inf or f == 0.0:
        return f
    bits = _float64_to_bits_ir(f)
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    f32_exp = biased_exp - 1023 + 127
    bits_to_drop = 52 - 23
    keep = mantissa >> bits_to_drop
    halfway = 1 << (bits_to_drop - 1)
    remainder = mantissa & ((1 << bits_to_drop) - 1)
    if remainder > halfway:
        keep += 1
    elif remainder == halfway and (keep & 1):
        keep += 1
    if keep >= (1 << 23):
        keep = 0
        f32_exp += 1
    if f32_exp >= 255:
        return -inf if sign else inf
    if f32_exp <= 0:
        return -0.0 if sign else 0.0
    new_biased_exp = f32_exp - 127 + 1023
    new_mantissa = keep << bits_to_drop
    new_bits = (sign << 63) | (new_biased_exp << 52) | new_mantissa
    return _bits_to_float64_ir(new_bits)


def _round_to_float16_ir(f: float) -> float:
    inf = 1e309
    if f != f or f == inf or f == -inf or f == 0.0:
        return f
    bits = _float64_to_bits_ir(f)
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    f16_exp = biased_exp - 1023 + 15
    bits_to_drop = 52 - 10
    keep = mantissa >> bits_to_drop
    halfway = 1 << (bits_to_drop - 1)
    remainder = mantissa & ((1 << bits_to_drop) - 1)
    if remainder > halfway:
        keep += 1
    elif remainder == halfway and (keep & 1):
        keep += 1
    if keep >= (1 << 10):
        keep = 0
        f16_exp += 1
    if f16_exp >= 31:
        return -inf if sign else inf
    if f16_exp <= 0:
        return -0.0 if sign else 0.0
    new_biased_exp = f16_exp - 15 + 1023
    new_mantissa = keep << bits_to_drop
    new_bits = (sign << 63) | (new_biased_exp << 52) | new_mantissa
    return _bits_to_float64_ir(new_bits)


# ---------------------------------------------------------------------------
# Type hierarchy — each Type knows how to render itself as LLVM text.
# ---------------------------------------------------------------------------


class Type:
    """Base class for all LLVM types."""

    # Subclasses set a short name used by repr. LLVM text comes from __str__.
    _name: str = "type"

    def __str__(self) -> str:  # LLVM text form, e.g. ``i32``, ``i8*``
        raise NotImplementedError

    def __repr__(self) -> str:
        return "<" + str(self._name) + " " + str(self) + ">"

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and str(self) == str(other)

    def __hash__(self) -> int:
        return hash((type(self), str(self)))

    def as_pointer(self, addrspace: int = 0) -> "PointerType":
        """Return a ``PointerType`` wrapping this type."""
        return PointerType(self, addrspace=addrspace)


class _SingletonType(Type):
    """Scalar types with no parameters — interned per subclass."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


class VoidType(_SingletonType):
    _name = "void"

    def __str__(self) -> str:
        return "void"


class IntType(Type):
    """Integer type with a given bit width.

    Interned per width — ``IntType(64) is IntType(64)`` holds true.
    Matches llvmlite, and keeps codegen ``value.type is _I64``
    identity checks working.
    """

    _name = "int"
    _cache: dict = {}

    def __new__(cls, width: int):
        existing = cls._cache.get(width)
        if existing is not None:
            return existing
        obj = super().__new__(cls)
        obj.width = width
        cls._cache[width] = obj
        return obj

    def __init__(self, width: int) -> None:
        # Keep this visible to pcc's native class-field collector.
        # __new__ handles CPython interning, but the self-host scaffold
        # constructs IntType instances through __init__ directly.
        self.width = width

    def __str__(self) -> str:
        return "i" + str(self.width)

    def __call__(self, value: int) -> "Constant":
        """``IntType(32)(0)`` shorthand for ``Constant(IntType(32), 0)``."""
        return Constant(self, value)


_I1 = IntType(1)


class HalfType(_SingletonType):
    _name = "half"

    def __str__(self) -> str:
        return "half"


class FloatType(_SingletonType):
    _name = "float"

    def __str__(self) -> str:
        return "float"


class DoubleType(_SingletonType):
    _name = "double"

    def __str__(self) -> str:
        return "double"


class PointerType(Type):
    """Pointer to a pointee type. LLVM 15+ uses opaque pointers (``ptr``)
    — we emit the opaque form by default to match llvmlite's current
    output; the pointee is tracked only for type-checking in the builder."""

    _name = "ptr"

    def __init__(self, pointee: Type, addrspace: int = 0) -> None:
        self.pointee = pointee
        self.addrspace = addrspace

    def __str__(self) -> str:
        # Opaque pointer form (LLVM 15+). addrspace qualifier inline.
        if self.addrspace:
            return "ptr addrspace(" + str(self.addrspace) + ")"
        return "ptr"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, PointerType)
            and self.addrspace == other.addrspace
            and self.pointee == other.pointee
        )

    def __hash__(self) -> int:
        return hash((PointerType, self.addrspace, self.pointee))


def _same_llvm_text_type(a: Type, b: Type) -> bool:
    return str(a) == str(b)


def _same_tracked_type(a: Type, b: Type) -> bool:
    if isinstance(a, PointerType) and isinstance(b, PointerType):
        return a.addrspace == b.addrspace and _same_tracked_type(a.pointee, b.pointee)
    return type(a) is type(b) and str(a) == str(b)


class ArrayType(Type):
    _name = "array"

    def __init__(self, element: Type, count: int) -> None:
        self.element = element
        self.count = count

    def __str__(self) -> str:
        return "[" + str(self.count) + " x " + str(self.element) + "]"

    def gep(self, indices) -> Type:
        """Type-level GEP — array has uniform element type, so any
        index into it yields ``element`` (recursed if nested)."""
        idxs = list(indices)
        if not idxs:
            return self
        result = self.element
        if len(idxs) > 1:
            if isinstance(result, (BaseStructType, ArrayType)):
                return result.gep(idxs[1:])
        return result


class BaseStructType(Type):
    """Base class for both literal (anonymous) and identified (named)
    struct types. Shared ``gep`` helper computes the element type for
    a constant-expression GEP on the struct type; pcc codegen uses
    this to resolve typed accesses at compile time."""

    elements: tuple = ()

    def gep(self, indices) -> Type:
        """Type-level GEP — return the field type at index ``indices[0]``
        (pcc only uses single-index GEP on structs, matching llvmlite's
        static check). Extra indices recurse into nested struct types."""
        idxs = list(indices)
        if not idxs:
            return self
        first = idxs[0]
        # Accept either a Python int or a Constant.
        if isinstance(first, Value):
            try:
                first = int(first.value)  # Constant-like
            except (AttributeError, TypeError, ValueError):
                # Unknown index — best-effort fall through
                return self
        if first < 0 or first >= len(self.elements):
            return self
        result = self.elements[first]
        if len(idxs) > 1 and isinstance(result, (BaseStructType, ArrayType)):
            return result.gep(idxs[1:])
        return result


class LiteralStructType(BaseStructType):
    """Anonymous struct with inline field types (``{i32, ptr}``)."""

    _name = "struct"

    def __init__(self, elements: Iterable[Type], packed: bool = False) -> None:
        self.elements = tuple(elements)
        self.packed = packed

    def __str__(self) -> str:
        body_parts = []
        for e in self.elements:
            body_parts.append(str(e))
        body = _join_text(body_parts, ", ")
        if self.packed:
            return "<{ " + body + " }>"
        return "{ " + body + " }"


class IdentifiedStructType(BaseStructType):
    """Named struct — e.g. ``%struct.Point = type { i32, i32 }``.

    In LLVM IR, identified struct types are module-level declarations
    referenced by their ``%name`` prefix. Codegen uses
    ``Context.get_identified_type(name)`` to intern these per-name,
    then ``set_body(...)`` fills in the elements.
    """

    _name = "id_struct"

    def __init__(self, context: "Context", name: str) -> None:
        self.context = context
        self.name = name
        self.elements: tuple = ()
        self._body_set = False
        self.packed = False

    @property
    def is_opaque(self) -> bool:
        """llvmlite parity: ``True`` until ``set_body`` is called."""
        return not self._body_set

    def set_body(self, body, packed: bool = False) -> None:
        """Set the struct body. ``body`` is an iterable of Types.

        **Signature difference from llvmlite**: llvmlite uses
        ``set_body(*elements)`` (vararg, audit-flagged). We take a
        single iterable. Callers should route through
        ``pcc.llvm_capi.compat.set_struct_body(ty, body)`` which
        adapts to whichever backend is active.
        """
        self.elements = tuple(body)
        self.packed = packed
        self._body_set = True

    def get_declaration(self) -> str:
        """Top-level type declaration text for the module."""
        if not self._body_set:
            return "%" + str(self.name) + " = type opaque"
        body_parts = []
        for e in self.elements:
            body_parts.append(str(e))
        body = _join_text(body_parts, ", ")
        if self.packed:
            return "%" + str(self.name) + " = type <{ " + body + " }>"
        return "%" + str(self.name) + " = type { " + body + " }"

    def __str__(self) -> str:
        # As an operand, refer by its name — actual body is emitted
        # at module top via ``get_declaration``.
        return "%" + str(self.name)


def _type_gep_result(base_ty: Type, indices) -> Type:
    idxs = list(indices)
    result: Type = base_ty
    i = 0
    while i < len(idxs):
        if isinstance(result, ArrayType):
            result = result.element
        elif isinstance(result, BaseStructType):
            first = idxs[i]
            if isinstance(first, Value):
                try:
                    first = int(first.value)
                except (AttributeError, TypeError, ValueError):
                    return result
            if first < 0 or first >= len(result.elements):
                return result
            result = result.elements[first]
        else:
            return result
        i += 1
    return result


class Context:
    """LLVM context — interns identified struct types per name."""

    def __init__(self) -> None:
        self.identified_types: dict[str, IdentifiedStructType] = {}

    def get_identified_type(self, name: str) -> IdentifiedStructType:
        t = self.identified_types.get(name)
        if t is None:
            t = IdentifiedStructType(self, name)
            self.identified_types[name] = t
        return t


# Module-level global context — matches llvmlite's ``global_context``.
global_context = Context()


class FunctionType(Type):
    """Function type: ``return_ty (arg_ty, ...)`` — never appears naked in
    IR text but used to emit function prototypes."""

    _name = "fn"

    def __init__(
        self,
        return_type: Type,
        args: Iterable[Type],
        var_arg: bool = False,
    ) -> None:
        self.return_type = return_type
        self.args = tuple(args)
        self.var_arg = var_arg

    def __str__(self) -> str:
        arg_parts = []
        for a in self.args:
            arg_parts.append(str(a))
        args_text = _join_text(arg_parts, ", ")
        if self.var_arg:
            args_text = args_text + ", ..." if args_text else "..."
        return str(self.return_type) + " (" + args_text + ")"


# ---------------------------------------------------------------------------
# Values — anything that can appear as an operand. All carry a ``.type``.
# ---------------------------------------------------------------------------


class Value:
    """A named SSA value: carries a type and a reference form (``%tmp3``
    or ``@globvar`` or a constant literal).

    Optionally carries ``_instr`` — a back-reference to the
    ``InstructionRecord`` this Value's defining instruction produced.
    Setting ``.flags`` rewrites that record to include the fast-math
    flags (matches llvmlite's mutable ``flags`` list on instrs)."""

    __slots__ = (
        "type",
        "_ref",
        "_instr",
        "_flags",
        "_is_unsigned",
        "_pcc_unsigned_pointee",
        "_pcc_unsigned_return",
    )

    def __init__(self, ty: Type, ref: str) -> None:
        self.type = ty
        self._ref = ref  # text used when this value is referenced as an operand
        self._instr = None
        self._flags: list[str] = []
        self._is_unsigned = False
        self._pcc_unsigned_pointee = False
        self._pcc_unsigned_return = False

    def __str__(self) -> str:
        return self._ref

    @property
    def flags(self) -> list[str]:
        return list(self._flags)

    @flags.setter
    def flags(self, value) -> None:
        self._flags = list(value)
        self._refresh_flags()

    def _refresh_flags(self) -> None:
        """Rewrite the defining instruction's text to include the
        current fast-math flags after the opcode. No-op if we don't
        have a back-reference to the record (e.g. for constants)."""
        if self._instr is None:
            return
        old = self._instr.text
        # Find the opcode position: optional "%name = " prefix, then opcode.
        eq = old.find(" = ")
        if eq >= 0:
            body = old[eq + 3 :]
            head = old[:eq] + " = "
        else:
            body = old
            head = ""
        # First token is opcode; inject flags between opcode and rest.
        split = body.find(" ")
        if split >= 0:
            op = body[:split]
            tail = body[split + 1 :]
        else:
            op = body
            tail = ""
        flag_parts = []
        for f in self._flags:
            flag_parts.append(" " + str(f))
        flag_text = _join_text(flag_parts, "").lstrip()
        if flag_text:
            new = str(head) + str(op) + " " + flag_text + " " + str(tail)
        else:
            new = str(head) + str(op) + " " + str(tail)
        self._instr.block._replace_record_text(self._instr, new)

    def bitcast(self, target_ty: Type) -> "Value":
        if _same_tracked_type(self.type, target_ty):
            return self
        expr = (
            "bitcast ("
            + str(self.type)
            + " "
            + str(self._ref)
            + " to "
            + str(target_ty)
            + ")"
        )
        return Value(target_ty, expr)

    def gep(self, indices, inbounds: bool = True) -> "Value":
        indices_list = list(indices)
        idx_parts = []
        for i in indices_list:
            idx_parts.append(str(i.type) + " " + str(i))
        idx_text = _join_text(idx_parts, ", ")
        base_ty = self.type.pointee if isinstance(self.type, PointerType) else self.type
        result_pointee = _type_gep_result(base_ty, indices_list[1:])
        inb = "inbounds " if inbounds else ""
        expr = (
            "getelementptr "
            + inb
            + "("
            + str(base_ty)
            + ", "
            + str(self.type)
            + " "
            + str(self._ref)
            + ", "
            + idx_text
            + ")"
        )
        return Value(PointerType(result_pointee), expr)


class Constant(Value):
    """Compile-time constant. ``ref`` is the constant's LLVM-text form."""

    def __init__(self, ty: Type, value) -> None:
        self.type = ty
        self.value = value
        self._ref = self._format(ty, value)
        self._instr = None
        self._flags: list[str] = []

    def inttoptr(self, target_ty: Type) -> Value:
        """Constant-expression inttoptr: returns a Value whose ref is
        the inline ``inttoptr (<ty> <val> to <target>)`` form.
        Matches llvmlite's chained-constant API used by codegen
        for label-address tables."""
        expr = (
            "inttoptr ("
            + str(self.type)
            + " "
            + str(self._ref)
            + " to "
            + str(target_ty)
            + ")"
        )
        return Value(target_ty, expr)

    def bitcast(self, target_ty: Type) -> Value:
        """Constant-expression bitcast. Same shape as ``inttoptr``."""
        if _same_tracked_type(self.type, target_ty):
            return self
        expr = (
            "bitcast ("
            + str(self.type)
            + " "
            + str(self._ref)
            + " to "
            + str(target_ty)
            + ")"
        )
        return Value(target_ty, expr)

    @staticmethod
    def _format_int(value) -> str:
        if value == 0:
            return "0"
        neg = value < 0
        if neg:
            value = 0 - value
        out = ""
        while value > 0:
            digit = value % 10
            if digit == 0:
                ch = "0"
            elif digit == 1:
                ch = "1"
            elif digit == 2:
                ch = "2"
            elif digit == 3:
                ch = "3"
            elif digit == 4:
                ch = "4"
            elif digit == 5:
                ch = "5"
            elif digit == 6:
                ch = "6"
            elif digit == 7:
                ch = "7"
            elif digit == 8:
                ch = "8"
            else:
                ch = "9"
            out = ch + out
            value = value // 10
        if neg:
            return "-" + out
        return out

    @staticmethod
    def _format_i8_array(value) -> str | None:
        parts = ['c"']
        digits = "0123456789ABCDEF"
        for v in value:
            if isinstance(v, Value) or not isinstance(v, int):
                return None
            if v < 0 or v > 255:
                return None
            hi = (v >> 4) & 15
            lo = v & 15
            parts.append("\\")
            parts.append(digits[hi])
            parts.append(digits[lo])
        parts.append('"')
        return _join_text(parts, "")

    @staticmethod
    def _format(ty: Type, value) -> str:
        # Already a Value/Constant — use its pre-formatted ref text.
        if isinstance(value, Value):
            return value._ref
        if value is None:
            # ``null`` / zeroinitializer depending on type
            if isinstance(ty, (PointerType,)):
                return "null"
            return "zeroinitializer"
        if isinstance(ty, IntType):
            if isinstance(value, bool):
                return "1" if value else "0"
            return Constant._format_int(value)
        if isinstance(ty, (FloatType, DoubleType, HalfType)):
            f = value * 1.0
            if isinstance(ty, FloatType):
                f = _round_to_float32_ir(f)
            elif isinstance(ty, HalfType):
                f = _round_to_float16_ir(f)
            raw = _float64_to_bits_ir(f)
            return _hex64(raw)
        if isinstance(ty, ArrayType):
            # [count x elem] [ <elem1>, <elem2>, ... ]
            # value is a sequence of Python-side values
            elem_ty = ty.element
            if isinstance(elem_ty, IntType) and elem_ty.width == 8:
                c_string = Constant._format_i8_array(value)
                if c_string is not None:
                    return c_string
            parts = []
            if isinstance(elem_ty, IntType):
                for v in value:
                    if isinstance(v, Value):
                        parts.append(v._ref)
                    elif isinstance(v, bool):
                        parts.append("1" if v else "0")
                    else:
                        parts.append(Constant._format_int(v))
            else:
                for v in value:
                    if isinstance(v, Value):
                        parts.append(v._ref)
                    else:
                        parts.append(Constant(elem_ty, v)._ref)
            # Each operand is printed as ``<elem_ty> <val>``
            elem_text = str(elem_ty)
            body_parts = []
            for p in parts:
                body_parts.append(elem_text + " " + str(p))
            body = _join_text(body_parts, ", ")
            return "[" + body + "]"
        if isinstance(ty, BaseStructType):
            # struct constant: { <ty1> <val1>, <ty2> <val2>, ... }
            parts = []
            for elem_ty, val in zip(ty.elements, value):
                c = Constant(elem_ty, val)
                parts.append(str(elem_ty) + " " + str(c._ref))
            return "{ " + _join_text(parts, ", ") + " }"
        # Fallback: stringify the raw value
        return str(value)


class Undefined:
    """Singleton sentinel for ``undef``."""

    def __str__(self) -> str:
        return "undef"


Undefined = Undefined()  # module-level singleton instance


# ---------------------------------------------------------------------------
# Function + Block containers
# ---------------------------------------------------------------------------


class Argument(Value):
    """Formal parameter of a Function.

    llvmlite convention: unnamed args get ``%.1``, ``%.2``, ... (note
    the leading ``.`` — these are *named* identifiers, not numbered
    unnamed ones). This keeps them out of the anonymous-temp number
    space so named locals inside the body don't collide with them.

    Codegen typically assigns a human name via ``arg.name = "x"``
    after construction — the property setter flips ``_ref`` to
    ``%x`` so instruction operands pick up the new name too.
    """

    def __init__(self, ty: Type, index: int) -> None:
        self.type = ty
        self.index = index
        self._name = ""
        # Match llvmlite: args are ``%.1``, ``%.2``, ... (1-based)
        self._ref = "%." + str(index + 1)

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        self._ref = "%" + str(value) if value else "%." + str(self.index + 1)


class InstructionRecord:
    """A single emitted instruction — text form + opname tag.

    Codegen sometimes needs to know "is this an alloca?" or "is this
    a terminator?" when hoisting or positioning new instructions.
    llvmlite's Instruction has ``opname``; we expose the same property
    derived from the first token of the emitted text."""

    def __init__(self, text: str, opname: str, block: "Block") -> None:
        self.text = text
        self.opname = opname
        self.block = block

    def __str__(self) -> str:
        return self.text


def _opname_of(line: str) -> str:
    """Extract the LLVM opname from an emitted instruction text line."""
    stripped = line.strip()
    # Skip past an optional ``%name =`` assignment prefix
    if stripped.startswith("%"):
        eq = stripped.find(" = ")
        if eq >= 0:
            stripped = stripped[eq + 3 :]
    # The self-hosted pcc-Python runtime has to keep these opname strings in
    # long-lived InstructionRecord fields. Returning stable literals for the
    # common opcodes avoids allocating a short slice solely to store metadata.
    if stripped.startswith("call "):
        return "call"
    if stripped.startswith("br "):
        return "br"
    if stripped.startswith("ret void"):
        return "ret void"
    if stripped.startswith("ret "):
        return "ret"
    if stripped.startswith("switch "):
        return "switch"
    if stripped.startswith("alloca "):
        return "alloca"
    if stripped.startswith("load "):
        return "load"
    if stripped.startswith("store "):
        return "store"
    if stripped.startswith("unreachable"):
        return "unreachable"
    if stripped.startswith("phi "):
        return "phi"
    # First whitespace-delimited token is the opcode
    idx = stripped.find(" ")
    return stripped[:idx] if idx >= 0 else stripped


def _render_instruction_lines(
    lines: list[str],
    start: int,
    end: int,
) -> str:
    out = ""
    i = start
    while i < end:
        out = out + "  "
        out = out + str(lines[i])
        out = out + "\n"
        i += 1
    return out


class Block:
    """A basic block — a list of ``InstructionRecord`` objects plus an
    auto-assigned label. Instructions are kept as records that the
    Function serializer assembles into the final IR text."""

    def __init__(self, parent: "Function", name: str) -> None:
        self.parent = parent
        # llvmlite also exposes the containing function as ``.function``
        # on BasicBlock — alias for codegen that uses that name.
        self.function = parent
        self.name = name
        self._instrs: list[InstructionRecord] = []
        self._text_lines: list[str] = []
        self._terminated = False

    @property
    def is_terminated(self) -> bool:
        """True if the block ends with a terminator instruction."""
        return self._terminated

    @property
    def instructions(self) -> list[InstructionRecord]:
        """Emitted instructions as records (``.opname``, ``.text``)."""
        return list(self._instrs)

    def append(self, line: str) -> None:
        op = _opname_of(line)
        self._instrs.append(InstructionRecord(line, op, self))
        self._text_lines.append(line)

    def insert(self, idx: int, line: str) -> None:
        self._instrs.insert(
            idx,
            InstructionRecord(line, _opname_of(line), self),
        )
        self._text_lines.insert(idx, line)

    def _replace_record_text(self, rec: InstructionRecord, text: str) -> None:
        rec.text = text
        i = 0
        while i < len(self._instrs):
            if self._instrs[i] is rec:
                self._text_lines[i] = text
                return
            i += 1

    def render(self) -> str:
        """Render as ``name:\\n  instr1\\n  instr2\\n``."""
        header = str(self.name) + ":\n"
        if (
            _debug_ir_render_enabled()
            and self.parent.name == "user_prog_Parser_make"
        ):
            try:
                sys.stderr.write(
                    "[pcc.ir.block] "
                    + str(self.name)
                    + " lines="
                    + str(len(self._text_lines))
                    + "\n"
                )
                j = 0
                while j < len(self._text_lines):
                    line = self._text_lines[j]
                    if not isinstance(line, str):
                        sys.stderr.write(
                            "[pcc.ir.block] nonstr line i="
                            + str(j)
                            + " type="
                            + str(type(line).__name__)
                            + "\n"
                        )
                        break
                    if self.name == "t.owned.cont.15":
                        sys.stderr.write(
                            "[pcc.ir.block] line i="
                            + str(j)
                            + " len="
                            + str(len(line))
                            + " text="
                            + line[:80]
                            + "\n"
                        )
                    j += 1
            except Exception:
                pass
        if (
            _debug_ir_render_enabled()
            and self.name == "entry"
            and self.parent.name == "user_pcc_parse_py_lex__is_digit_code"
        ):
            try:
                first_text = "<none>"
                if len(self._instrs) > 0:
                    first_text = str(self._instrs[0].text)
                sys.stderr.write(
                    "[pcc.ir.render] block entry instrs="
                    + str(len(self._instrs))
                    + " first="
                    + first_text
                    + "\n"
                )
            except Exception:
                pass
        body = _render_instruction_lines(self._text_lines, 0, len(self._text_lines))
        if (
            _debug_ir_render_enabled()
            and self.parent.name == "user_prog_Parser_make"
        ):
            try:
                sys.stderr.write(
                    "[pcc.ir.block] rendered "
                    + str(self.name)
                    + " type="
                    + str(type(body).__name__)
                    + " len="
                    + str(len(body))
                    + "\n"
                )
            except Exception:
                pass
        if (
            _debug_ir_render_enabled()
            and self.name == "entry"
            and self.parent.name == "user_pcc_parse_py_lex__is_digit_code"
        ):
            try:
                first_part = "<none>"
                first_part_len = -1
                first_render_len = -1
                second_part = "<none>"
                second_part_len = -1
                second_render_len = -1
                render2_len = -1
                render3_len = -1
                if len(self._instrs) > 0:
                    first_part = "  " + str(self._instrs[0].text)
                    first_part_len = len(first_part)
                    first_render_len = len(
                        _render_instruction_lines(self._text_lines, 0, 1)
                    )
                    if len(self._instrs) > 1:
                        second_part = "  " + str(self._instrs[1].text)
                        second_part_len = len(second_part)
                        second_render_len = len(
                            _render_instruction_lines(self._text_lines, 1, 2)
                        )
                    render2_len = len(_render_instruction_lines(self._text_lines, 0, 2))
                    render3_len = len(_render_instruction_lines(self._text_lines, 0, 3))
                sys.stderr.write(
                    "[pcc.ir.render] block entry stream=1 body_len="
                    + str(len(body))
                    + " parts="
                    + str(len(self._instrs))
                    + " first_part_len="
                    + str(first_part_len)
                    + " first_render_len="
                    + str(first_render_len)
                    + " second_part_len="
                    + str(second_part_len)
                    + " second_render_len="
                    + str(second_render_len)
                    + " render2_len="
                    + str(render2_len)
                    + " render3_len="
                    + str(render3_len)
                    + " first_part="
                    + first_part
                    + " second_part="
                    + second_part
                    + "\n"
                )
            except Exception:
                pass
        return header + body


class Function(Value):
    """A function in a Module. Holds a signature, blocks, and a name
    counter for temp %N identifiers.

    Subclasses ``Value`` because in LLVM IR a Function is also a typed
    SSA value: a global pointer to a function type. Codegen call sites
    that take ``Value`` accept ``Function`` directly without a separate
    downcast.
    """

    def __init__(
        self,
        module: Module,
        function_type: FunctionType,
        name: str = "",
    ) -> None:
        # As a value, a function has a pointer type — matches llvmlite
        # (``Function.type`` is ``PointerType(FunctionType, 0)``).
        Value.__init__(self, PointerType(function_type), "@" + str(name))
        self.module = module
        self.ftype = function_type
        # llvmlite also exposes ``function_type``; alias both so codegen
        # code that reads either attribute works.
        self.function_type = function_type
        self.name = name
        self.blocks: list[Block] = []
        # Formal parameter objects
        args_list: list[Argument] = []
        i = 0
        n_args = len(function_type.args)
        while i < n_args:
            args_list.append(Argument(function_type.args[i], i))
            i += 1
        self.args = tuple(args_list)
        # Anonymous-temp counter continues from args (%.1..%.N are args,
        # next temp is %.{N+1}). llvmlite uses the ``.`` prefix on all
        # auto-numbered identifiers — matches lets canonical-IR diff
        # stay clean.
        self._name_counter = len(self.args)
        self._block_counter = 0
        # Track explicit names used by blocks and the small number of
        # callers that require llvmlite-style stable deduplication. SSA
        # instruction names are allocated from ``_name_counter`` instead;
        # pcc-Python's dict is too expensive for per-instruction use in
        # self-hosted codegen.
        self._name_registry: dict[str, int] = {}
        self.linkage = ""
        self.attributes = FunctionAttributes()
        self.calling_convention = ""
        # Mark external if no blocks ever appended — subset of llvmlite's
        # behavior (``declare`` vs ``define``).
        module._functions.append(self)
        module.globals[name] = self

    def append_basic_block(self, name: str = "") -> Block:
        if not name:
            name = "bb" + str(self._block_counter)
            self._block_counter += 1
        else:
            # Dedup against existing blocks + SSA names in this
            # function. Matches llvmlite: repeated
            # ``append_basic_block("then")`` yields ``then``, ``then.1``...
            name = self._unique(name)
        blk = Block(self, name)
        self.blocks.append(blk)
        return blk

    def _fresh(self) -> str:
        """Generate a fresh ``.N`` identifier for anonymous temps
        (llvmlite convention: ``%.N``)."""
        self._name_counter += 1
        return "." + str(self._name_counter)

    def _unique(self, name: str) -> str:
        """Return a unique variant of ``name`` within this function.
        First use returns ``name`` unchanged; subsequent uses return
        ``name.1``, ``name.2``, etc. Matches llvmlite's behavior."""
        n = self._name_registry.get(name, 0)
        self._name_registry[name] = n + 1
        if n == 0:
            return name
        # Keep scanning upward in case ``name.K`` has already been
        # registered explicitly elsewhere.
        candidate = str(name) + "." + str(n)
        while candidate in self._name_registry:
            n += 1
            candidate = str(name) + "." + str(n)
        self._name_registry[candidate] = 1
        return candidate

    def __str__(self) -> str:
        """Function-as-operand: render as ``@name`` so it can be used
        directly as a call target or inside constant expressions."""
        return "@" + str(self.name)

    @property
    def return_value(self) -> "Value":
        """Sentinel value representing the function's return slot. Only
        its ``.type`` is meaningful — used by codegen to check the
        declared return type without looking up ``ftype``."""
        return Value(self.ftype.return_type, "<return>")

    @property
    def is_declaration(self) -> bool:
        return not self.blocks

    @property
    def entry_basic_block(self) -> "Block":
        """First block in the function — matches llvmlite's attribute."""
        if not self.blocks:
            raise ValueError("function has no blocks")
        return self.blocks[0]

    @property
    def basic_blocks(self) -> list["Block"]:
        """All basic blocks — matches llvmlite's attribute."""
        return list(self.blocks)

    def render(self) -> str:
        """Render the function as LLVM IR text."""
        fty = self.ftype
        ret_ty = fty.return_type
        # Argument list text
        arg_parts = []
        i = 0
        while i < len(self.args):
            arg = self.args[i]
            if i < len(fty.args):
                arg_ty = fty.args[i]
            else:
                arg_ty = arg.type
            if arg._name:
                arg_parts.append(str(arg_ty) + " %" + str(arg._name))
            else:
                arg_parts.append(str(arg_ty) + " " + str(arg._ref))
            i += 1
        if fty.var_arg:
            arg_parts.append("...")
        args_text = _join_text(arg_parts, ", ")
        linkage = str(self.linkage) + " " if self.linkage else ""

        # Personality + attributes (declarations don't carry them).
        pers_text = ""
        attrs_text = ""
        if self.attributes.personality is not None:
            pers = self.attributes.personality
            pers_ty = PointerType(pers.ftype)
            pers_text = " personality " + str(pers_ty) + " @" + str(pers.name)
        if self.attributes._attrs:
            attrs_text = " " + _join_text(sorted(self.attributes._attrs), " ")

        if not self.blocks:
            arg_type_parts = []
            i = 0
            while i < len(fty.args):
                t = fty.args[i]
                arg_type_parts.append(str(t))
                i += 1
            arg_type_only = _join_text(arg_type_parts, ", ")
            if fty.var_arg:
                arg_type_only = arg_type_only + ", ..." if arg_type_only else "..."
            return (
                "declare "
                + linkage
                + str(ret_ty)
                + " @"
                + str(self.name)
                + "("
                + arg_type_only
                + ")"
                + attrs_text
                + "\n"
            )
        if _debug_ir_render_enabled() and self.name == "user_pcc_parse_py_lex__is_digit_code":
            try:
                entry_instrs = -1
                first_text = "<none>"
                if len(self.blocks) > 0:
                    entry_instrs = len(self.blocks[0]._instrs)
                    if entry_instrs > 0:
                        first_text = str(self.blocks[0]._instrs[0].text)
                sys.stderr.write(
                    "[pcc.ir.render] function "
                    + str(self.name)
                    + " blocks="
                    + str(len(self.blocks))
                    + " entry_instrs="
                    + str(entry_instrs)
                    + " first="
                    + first_text
                    + "\n"
                )
            except Exception:
                pass
        body_parts = []
        i = 0
        while i < len(self.blocks):
            b = self.blocks[i]
            rendered_block = b.render()
            if _debug_ir_render_enabled() and self.name == "user_prog_Parser_make":
                try:
                    if not isinstance(rendered_block, str):
                        sys.stderr.write(
                            "[pcc.ir.function] nonstr block i="
                            + str(i)
                            + " name="
                            + str(b.name)
                            + " type="
                            + str(type(rendered_block).__name__)
                            + "\n"
                        )
                except Exception:
                    pass
            body_parts.append(rendered_block)
            i += 1
        body = _join_text(body_parts, "\n")
        if _debug_ir_render_enabled() and self.name == "user_prog_Parser_make":
            try:
                sys.stderr.write(
                    "[pcc.ir.function] body type="
                    + str(type(body).__name__)
                    + " len="
                    + str(len(body))
                    + "\n"
                )
            except Exception:
                pass
        out = "define "
        out = out + linkage
        out = out + str(ret_ty)
        out = out + " @"
        out = out + str(self.name)
        out = out + "("
        out = out + args_text
        out = out + ")"
        out = out + attrs_text
        out = out + pers_text
        out = out + " {\n"
        out = out + body
        out = out + "}\n"
        return out


def _value_ref(value) -> str:
    """Render an operand reference without relying on virtual ``__str__``.

    Self-hosted pcc currently exercises this path before all Python object
    dispatch edge cases are closed.  Function operands have an unambiguous LLVM
    spelling, so keep that critical case explicit.
    """
    if isinstance(value, Function):
        return "@" + str(value.name)
    try:
        name = value.name
        value.ftype
        if name:
            return "@" + str(name)
    except AttributeError:
        pass
    try:
        ref = value._ref
        if ref:
            return ref
    except AttributeError:
        pass
    try:
        index = value.index
        if index is not None:
            return "%." + str(index + 1)
    except AttributeError:
        pass
    return str(value)


def _looks_like_function(value) -> bool:
    if isinstance(value, Function):
        return True
    try:
        ftype = value.ftype
        name = value.name
    except AttributeError:
        return False
    return name is not None and _looks_like_function_type(ftype)


def _looks_like_pointer_type(value) -> bool:
    if isinstance(value, PointerType):
        return True
    try:
        pointee = value.pointee
    except AttributeError:
        return False
    return pointee is not None


def _looks_like_function_type(value) -> bool:
    if isinstance(value, FunctionType):
        return True
    try:
        return_type = value.return_type
        args = value.args
    except AttributeError:
        return False
    return return_type is not None and args is not None


def _looks_like_void_type(value) -> bool:
    if isinstance(value, VoidType):
        return True
    return str(value) == "void"


class FunctionAttributes:
    """Function attributes set (``noreturn``, ``alwaysinline``, etc.) plus
    a ``personality`` slot for EH-emitting functions. Rendered inline
    with the function definition."""

    def __init__(self) -> None:
        # NOTE: backed by a list (sorted on read) instead of a set —
        # pcc1 self-host has a UAF in py_set.add on freshly-constructed
        # sets under tight heap. See
        # docs/investigations/pcc1-stage2-runtime-abi-set-segfault.md.
        self._attrs: list[str] = []
        self.personality: Optional["Function"] = None

    def add(self, attr: str) -> None:
        if attr not in self._attrs:
            self._attrs.append(attr)

    def __bool__(self) -> bool:
        return bool(self._attrs) or self.personality is not None


# ---------------------------------------------------------------------------
# GlobalVariable
# ---------------------------------------------------------------------------


class GlobalVariable(Value):
    """``@name = [linkage] global <ty> <init>`` (or external / internal)."""

    def __init__(
        self,
        module: "Module",
        ty: Type,
        name: str,
        addrspace: int = 0,
    ) -> None:
        self.type = PointerType(ty, addrspace=addrspace)
        self.value_type = ty
        self.name = name
        self.linkage = ""
        self.global_constant = False
        self.initializer: Optional[Value] = None
        self.addrspace = addrspace
        self.section = ""
        self.align: Optional[int] = None
        self.unnamed_addr = False
        self._ref = "@" + str(name)
        module._globals.append(self)
        module.globals[name] = self

    def gep(self, indices, inbounds: bool = True) -> Value:
        """Constant-expression GEP on this global — emits inline as
        ``getelementptr (inbounds) (<ty>, ptr @name, i32 i0, i32 i1, ...)``.
        Used by codegen to materialize a pointer-to-first-element of
        a global array or struct without a separate builder call."""
        indices_list = []
        for idx in indices:
            indices_list.append(idx)
        idx_parts = []
        for v in indices_list:
            idx_parts.append(str(v.type) + " " + str(v))
        idx_text = _join_text(idx_parts, ", ")
        inb = "inbounds " if inbounds else ""
        result_pointee = _type_gep_result(self.value_type, indices_list[1:])
        expr = (
            "getelementptr "
            + inb
            + "("
            + str(self.value_type)
            + ", "
            + str(self.type)
            + " @"
            + str(self.name)
            + ", "
            + idx_text
            + ")"
        )
        return Value(PointerType(result_pointee), expr)

    def render(self) -> str:
        linkage = str(self.linkage) + " " if self.linkage else ""
        kind = "constant" if self.global_constant else "global"
        init_text = ""
        if self.initializer is not None:
            init_text = " " + str(self.initializer)
        else:
            init_text = " zeroinitializer" if self.linkage != "external" else ""
        align_text = ", align " + str(self.align) if self.align else ""
        return (
            "@"
            + str(self.name)
            + " = "
            + linkage
            + kind
            + " "
            + str(self.value_type)
            + init_text
            + align_text
            + "\n"
        )


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class Module:
    """Top-level IR container. Holds globals, functions, triple + layout
    strings, and named metadata."""

    def __init__(self, name: str = "", context: Optional[object] = None) -> None:
        self.name = name
        # Match llvmlite defaults so canonical-form diffs stay empty
        # when the caller doesn't explicitly set these.
        self.triple = "unknown-unknown-unknown"
        self.data_layout = ""
        self._functions: list[Function] = []
        self._globals: list[GlobalVariable] = []
        self.globals: dict = {}
        # Named metadata (e.g. ``!llvm.dbg.cu = !{!0}``) — β4.3 surface.
        self._named_metadata: dict[str, list] = {}
        self.context = context or global_context
        self._name_counters: dict[str, int] = {}

    def get_unique_name(self, base: str) -> str:
        """Return a unique variant of ``base`` within this module's
        global namespace. Matches llvmlite's ``Module.get_unique_name``."""
        n = self._name_counters.get(base, 0)
        self._name_counters[base] = n + 1
        if n == 0:
            return base
        return str(base) + "." + str(n)

    @property
    def functions(self) -> list[Function]:
        return list(self._functions)

    @property
    def global_values(self) -> list[GlobalVariable]:
        return list(self._globals)

    def get_global(self, name: str):
        """Retrieve a global by name (Function or GlobalVariable).
        Returns None if absent — matches llvmlite's ``.globals.get``."""
        return self.globals.get(name)

    def add_global(self, gv: GlobalVariable) -> None:
        if gv not in self._globals:
            self._globals.append(gv)
        self.globals[gv.name] = gv

    def add_named_metadata(self, name: str, node=None) -> None:
        """β4.3 placeholder — collect named metadata nodes."""
        if name not in self._named_metadata:
            self._named_metadata[name] = []
        if node is not None:
            self._named_metadata[name].append(node)

    def add_debug_info(self, node_type: str, fields: dict, is_distinct: bool = False):
        """β4.3 surface — raises for now."""
        raise NotImplementedError(
            "pcc.llvm_capi.ir debug info (DIFile/DISubprogram/...) "
            "is a β4.3 scope item. Currently hit via emit_debug=True."
        )

    def __str__(self) -> str:
        parts: list[str] = []
        if _debug_ir_render_enabled():
            try:
                sys.stderr.write(
                    "[pcc.ir.module] start funcs="
                    + str(len(self._functions))
                    + " globals="
                    + str(len(self._globals))
                    + "\n"
                )
            except Exception:
                pass
        if self.name:
            parts.append('; ModuleID = "' + str(self.name) + '"')
        # Always emit triple + datalayout (even if empty) to match llvmlite
        parts.append('target triple = "' + str(self.triple) + '"')
        parts.append('target datalayout = "' + str(self.data_layout) + '"')
        parts.append("")
        # Identified struct type declarations — must precede globals
        # and functions that reference them.
        identified_type_values = list(self.context.identified_types.values())
        i = 0
        while i < len(identified_type_values):
            t = identified_type_values[i]
            parts.append(t.get_declaration())
            i += 1
        if self.context.identified_types:
            parts.append("")
        i = 0
        while i < len(self._globals):
            gv = self._globals[i]
            parts.append(gv.render())
            i += 1
        i = 0
        while i < len(self._functions):
            fn = self._functions[i]
            rendered_fn = fn.render()
            if _debug_ir_render_enabled():
                try:
                    if not isinstance(rendered_fn, str):
                        sys.stderr.write(
                            "[pcc.ir.module] nonstr function i="
                            + str(i)
                            + " name="
                            + str(fn.name)
                            + " type="
                            + str(type(rendered_fn).__name__)
                            + "\n"
                        )
                except Exception:
                    pass
            parts.append(rendered_fn)
            i += 1
        out = _join_text(parts, "\n")
        if _debug_ir_render_enabled():
            try:
                sys.stderr.write(
                    "[pcc.ir.module] end parts="
                    + str(len(parts))
                    + " out="
                    + str(len(out))
                    + "\n"
                )
            except Exception:
                pass
        return out


# ---------------------------------------------------------------------------
# IRBuilder
# ---------------------------------------------------------------------------


class IRBuilder:
    """Builder for emitting instructions into a Block.

    API-compatible subset of ``llvmlite.ir.IRBuilder`` covering the
    Tier 1-3 operations from docs/plans/llvmcapi-beta4-backlog.md.
    """

    # Sentinel for "append at end" insertion position. Using a huge
    # int keeps arithmetic with _anchor safe (``saved + 1``) — llvmlite
    # uses an actual index, not a symbol.
    _END = 10**18

    def __init__(self, block: Optional[Block] = None) -> None:
        # llvmlite uses ``_block`` internally; some pcc helpers poke
        # that name directly (marshal._stash_overflow_slot). Keep a
        # single private storage and expose via both names.
        self._block: Optional[Block] = block
        # Insertion position within the block — int index. _END means
        # "append to end" (clamped by _emit before use).
        self._pos: int = self._END
        self._fn: Optional[Function] = block.parent if block else None

    @property
    def block(self) -> Optional[Block]:
        return self._block

    @block.setter
    def block(self, value: Optional[Block]) -> None:
        self._block = value

    # llvmlite names its internal insertion cursor ``_anchor``. Some
    # pcc codegen helpers (marshal._stash_overflow_slot) save/restore
    # it around nested emissions. Expose a property alias over ``_pos``
    # so that code keeps working.
    @property
    def _anchor(self) -> int:
        if self._pos == self._END and self._block is not None:
            return len(self._block._instrs)
        return int(self._pos)

    @_anchor.setter
    def _anchor(self, value) -> None:
        self._pos = int(value)

    @property
    def function(self) -> Optional["Function"]:
        """Current function — matches llvmlite's ``IRBuilder.function``."""
        return self._fn

    # ------------- positioning -------------

    def position_at_end(self, block: Block) -> None:
        self._block = block
        self._fn = block.parent
        self._pos = self._END

    def position_at_start(self, block: Block) -> None:
        self._block = block
        self._fn = block.parent
        self._pos = 0

    def position_before(self, instr_or_block) -> None:
        """Position before a specific instruction record or at the
        start of a block. Matches llvmlite's API."""
        if isinstance(instr_or_block, Block):
            self.position_at_start(instr_or_block)
            return
        if isinstance(instr_or_block, InstructionRecord):
            # Find the record's index within its block
            blk = instr_or_block.block
            self._block = blk
            self._fn = blk.parent
            for i, rec in enumerate(blk._instrs):
                if rec is instr_or_block:
                    self._pos = i
                    return
            # Fallback — not found, position at end
            self._pos = "end"
            return
        # Fallback — position at start of the inferred block
        try:
            blk = instr_or_block.block
        except AttributeError:
            blk = None
        if blk is None:
            blk = self._block
        if blk is not None:
            self.position_at_start(blk)

    def append_basic_block(self, name: str = "") -> Block:
        """Shortcut — create a block in the current function."""
        if self._fn is None:
            raise ValueError("no current function")
        return self._fn.append_basic_block(name)

    # ------------- emission helpers -------------

    def _emit(self, line: str) -> "InstructionRecord":
        """Append ``line`` to the current block at the current position.

        Keeps ``_pos`` tracking the *next-insert* index correctly:
        after a positioned append (``_pos == len``), we advance ``_pos``
        so subsequent emits continue appending rather than starting to
        insert between already-emitted instructions. This mirrors
        llvmlite's anchor semantics.

        Returns the created ``InstructionRecord`` so callers can
        attach the defining instruction to the returned ``Value`` —
        required for mutable-flags rewrites (fast-math contract flag).
        """
        if self._block is None:
            raise ValueError("IRBuilder has no current block")
        blk = self._block
        n = len(blk._instrs)
        if self._pos >= n:
            blk.append(line)
            if self._pos != self._END:
                self._pos = len(blk._instrs)
            return blk._instrs[-1]
        blk.insert(self._pos, line)
        rec = blk._instrs[self._pos]
        self._pos += 1
        return rec

    def _next_name(self, name: str) -> str:
        fn = self._fn
        if fn is None:
            raise ValueError("IRBuilder has no current function")
        fn._name_counter += 1
        if name == "":
            return "." + str(fn._name_counter)
        return name + "." + str(fn._name_counter)

    def _next(self, name: str, ty: Type) -> Value:
        """Produce a new local SSA value with ``name`` (or a fresh temp
        if empty). Named temporaries use a function-wide serial suffix,
        trading llvmlite text parity for O(1) self-hosted codegen."""
        fn = self._fn
        if fn is None:
            raise ValueError("IRBuilder has no current function")
        fn._name_counter += 1
        if name == "":
            ref = "%."
            ref = ref + str(fn._name_counter)
        else:
            ref = "%"
            ref = ref + str(name)
            ref = ref + "."
            ref = ref + str(fn._name_counter)
        return Value(ty, ref)

    # ------------- return / branch / terminator -------------

    def ret(self, value: Value) -> Value:
        self._emit("ret " + str(value.type) + " " + str(value))
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def ret_void(self) -> Value:
        self._emit("ret void")
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def branch(self, target: Block) -> Value:
        self._emit("br label %" + str(target.name))
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def cbranch(self, cond: Value, t: Block, f: Block) -> Value:
        line = "br i1 "
        line = line + str(cond)
        line = line + ", label %"
        line = line + str(t.name)
        line = line + ", label %"
        line = line + str(f.name)
        self._emit(line)
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def unreachable(self) -> Value:
        self._emit("unreachable")
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def switch(self, value: Value, default_block: Block) -> "SwitchInstr":
        """Emit a ``switch`` terminator. Returns a ``SwitchInstr`` that
        the caller extends via ``add_case(int_value, target_block)``."""
        sw = SwitchInstr(self, value, default_block)
        sw._instr = self._emit(sw._render())
        if self._block is not None:
            self._block._terminated = True
        return sw

    # ------------- memory -------------

    def alloca(self, ty: Type, size: Optional[Value] = None, name: str = "") -> Value:
        v = self._next(name, PointerType(ty))
        size_text = ""
        if size is not None:
            size_text = ", " + str(size.type) + " " + str(size)
        self._emit(str(v) + " = alloca " + str(ty) + size_text)
        return v

    def load(self, ptr: Value, name: str = "", align: Optional[int] = None) -> Value:
        pointee = ptr.type.pointee if isinstance(ptr.type, PointerType) else ptr.type
        v = self._next(name, pointee)
        align_text = ", align " + str(align) if align else ""
        line = str(v)
        line = line + " = load "
        line = line + str(pointee)
        line = line + ", "
        line = line + str(ptr.type)
        line = line + " "
        line = line + str(ptr)
        line = line + align_text
        self._emit(line)
        return v

    def store(self, value: Value, ptr: Value, align: Optional[int] = None) -> Value:
        align_text = ", align " + str(align) if align else ""
        stored_ty = ptr.type.pointee if isinstance(ptr.type, PointerType) else value.type
        parts = [
            "store ",
            str(stored_ty),
            " ",
            _value_ref(value),
            ", ",
            str(ptr.type),
            " ",
            _value_ref(ptr),
            align_text,
        ]
        self._emit(_join_text(parts, ""))
        return Value(VoidType(), "")

    def load_atomic(
        self,
        ptr: Value,
        ordering: str,
        align: int,
        name: str = "",
        typ: Optional[Type] = None,
    ) -> Value:
        pointee = (
            typ
            if typ is not None
            else (ptr.type.pointee if isinstance(ptr.type, PointerType) else ptr.type)
        )
        v = self._next(name, pointee)
        self._emit(
            str(v)
            + " = load atomic "
            + str(pointee)
            + ", "
            + str(ptr.type)
            + " "
            + str(ptr)
            + " "
            + str(ordering)
            + ", align "
            + str(align)
        )
        return v

    def store_atomic(
        self,
        value: Value,
        ptr: Value,
        ordering: str,
        align: int,
    ) -> Value:
        self._emit(
            "store atomic "
            + str(value.type)
            + " "
            + str(value)
            + ", "
            + str(ptr.type)
            + " "
            + str(ptr)
            + " "
            + str(ordering)
            + ", align "
            + str(align)
        )
        return Value(VoidType(), "")

    def fence(self, ordering: str, syncscope: Optional[str] = None) -> Value:
        scope_text = 'syncscope("' + str(syncscope) + '") ' if syncscope else ""
        self._emit("fence " + scope_text + str(ordering))
        return Value(VoidType(), "")

    def gep(
        self,
        ptr: Value,
        indices: Iterable[Value],
        inbounds: bool = False,
        name: str = "",
    ) -> Value:
        # Result pointee = drill into the base type using indices[1:]
        # (the first index steps through the pointer itself).
        indices_list = list(indices)
        base_ty = ptr.type.pointee if isinstance(ptr.type, PointerType) else ptr.type
        result_pointee = base_ty
        result_pointee = _type_gep_result(base_ty, indices_list[1:])
        # GEP result is a pointer to the drilled type
        result_ptr_ty = PointerType(result_pointee)
        v = self._next(name, result_ptr_ty)
        idx_parts = []
        for i in indices_list:
            idx_parts.append(str(i.type) + " " + str(i))
        idx_text = _join_text(idx_parts, ", ")
        inb = "inbounds " if inbounds else ""
        line = str(v)
        line = line + " = getelementptr "
        line = line + inb
        line = line + str(base_ty)
        line = line + ", "
        line = line + str(ptr.type)
        line = line + " "
        line = line + str(ptr)
        line = line + ", "
        line = line + idx_text
        self._emit(line)
        return v

    # ------------- casts -------------

    def bitcast(self, value: Value, ty: Type, name: str = "") -> Value:
        try:
            value_ty = value.type
        except AttributeError:
            value_ty = None
        value_ref = _value_ref(value)
        if isinstance(value, Function):
            fn_name = value.name
            fn_type = value.ftype
            value_ty = PointerType(fn_type)
            value_ref = "@" + str(fn_name)
        if value_ty is None:
            value_ty = value.type
        if _same_tracked_type(value_ty, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = bitcast "
            + str(value_ty)
            + " "
            + str(value_ref)
            + " to "
            + str(ty)
        )
        return v

    def sext(self, value: Value, ty: Type, name: str = "") -> Value:
        if _same_llvm_text_type(value.type, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = sext "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def zext(self, value: Value, ty: Type, name: str = "") -> Value:
        if _same_llvm_text_type(value.type, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = zext "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def trunc(self, value: Value, ty: Type, name: str = "") -> Value:
        if _same_llvm_text_type(value.type, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = trunc "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def ptrtoint(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = ptrtoint "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def inttoptr(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = inttoptr "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def sitofp(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = sitofp "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def uitofp(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = uitofp "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def fptosi(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = fptosi "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def fptoui(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = fptoui "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def fpext(self, value: Value, ty: Type, name: str = "") -> Value:
        if _same_llvm_text_type(value.type, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = fpext "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    def fptrunc(self, value: Value, ty: Type, name: str = "") -> Value:
        if _same_llvm_text_type(value.type, ty):
            return value
        v = self._next(name, ty)
        self._emit(
            str(v)
            + " = fptrunc "
            + str(value.type)
            + " "
            + str(value)
            + " to "
            + str(ty)
        )
        return v

    # ------------- integer arithmetic -------------

    def _int_binop(self, op: str, lhs: Value, rhs: Value, name: str) -> Value:
        lhs_ty = lhs.type
        rhs_ty = rhs.type
        if not isinstance(lhs_ty, Type):
            if isinstance(rhs_ty, Type):
                lhs_ty = rhs_ty
            else:
                lhs_ty = IntType(64)
        v = self._next(name, lhs_ty)
        parts = [
            str(v),
            " = ",
            str(op),
            " ",
            str(lhs_ty),
            " ",
            _value_ref(lhs),
            ", ",
            _value_ref(rhs),
        ]
        self._emit(_join_text(parts, ""))
        return v

    def add(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("add", a, b, name)

    def sub(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("sub", a, b, name)

    def mul(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("mul", a, b, name)

    def sdiv(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("sdiv", a, b, name)

    def udiv(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("udiv", a, b, name)

    def srem(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("srem", a, b, name)

    def urem(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("urem", a, b, name)

    def and_(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("and", a, b, name)

    def or_(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("or", a, b, name)

    def xor(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("xor", a, b, name)

    def shl(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("shl", a, b, name)

    def ashr(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("ashr", a, b, name)

    def lshr(self, a: Value, b: Value, name: str = "") -> Value:
        return self._int_binop("lshr", a, b, name)

    def neg(self, value: Value, name: str = "") -> Value:
        zero = Constant(value.type, 0)
        return self.sub(zero, value, name)

    def not_(self, value: Value, name: str = "") -> Value:
        """Bitwise NOT / logical complement. Emits ``xor value, -1``
        (or for i1: ``xor value, 1``). Matches llvmlite."""
        # For i1 (boolean), XOR with 1 flips the bit. For wider int,
        # XOR with -1 (all ones) gives bitwise complement.
        if isinstance(value.type, IntType) and value.type.width == 1:
            ones = Constant(value.type, 1)
        else:
            ones = Constant(value.type, -1)
        return self.xor(value, ones, name)

    # ------------- floating arithmetic -------------

    def _fp_binop(self, op: str, lhs: Value, rhs: Value, name: str) -> Value:
        v = self._next(name, lhs.type)
        rec = self._emit(
            str(v)
            + " = "
            + str(op)
            + " "
            + str(lhs.type)
            + " "
            + str(lhs)
            + ", "
            + str(rhs)
        )
        # Attach the emitted record so flags (fast-math contract etc.)
        # can be added retroactively via ``v.flags = [...]``.
        v._instr = rec
        return v

    def fadd(self, a: Value, b: Value, name: str = "") -> Value:
        return self._fp_binop("fadd", a, b, name)

    def fsub(self, a: Value, b: Value, name: str = "") -> Value:
        return self._fp_binop("fsub", a, b, name)

    def fmul(self, a: Value, b: Value, name: str = "") -> Value:
        return self._fp_binop("fmul", a, b, name)

    def fdiv(self, a: Value, b: Value, name: str = "") -> Value:
        return self._fp_binop("fdiv", a, b, name)

    def frem(self, a: Value, b: Value, name: str = "") -> Value:
        return self._fp_binop("frem", a, b, name)

    def fneg(self, value: Value, name: str = "") -> Value:
        v = self._next(name, value.type)
        rec = self._emit(
            str(v) + " = fneg " + str(value.type) + " " + str(value)
        )
        v._instr = rec
        return v

    # ------------- comparisons -------------

    def icmp_signed(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {
            "==": "eq",
            "!=": "ne",
            "<": "slt",
            "<=": "sle",
            ">": "sgt",
            ">=": "sge",
        }
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        line = str(v)
        line = line + " = icmp "
        line = line + str(pred)
        line = line + " "
        line = line + str(a.type)
        line = line + " "
        line = line + str(a)
        line = line + ", "
        line = line + str(b)
        self._emit(line)
        return v

    def icmp_unsigned(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {
            "==": "eq",
            "!=": "ne",
            "<": "ult",
            "<=": "ule",
            ">": "ugt",
            ">=": "uge",
        }
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        line = str(v)
        line = line + " = icmp "
        line = line + str(pred)
        line = line + " "
        line = line + str(a.type)
        line = line + " "
        line = line + str(a)
        line = line + ", "
        line = line + str(b)
        self._emit(line)
        return v

    def fcmp_ordered(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {
            "==": "oeq",
            "!=": "one",
            "<": "olt",
            "<=": "ole",
            ">": "ogt",
            ">=": "oge",
        }
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(
            str(v)
            + " = fcmp "
            + str(pred)
            + " "
            + str(a.type)
            + " "
            + str(a)
            + ", "
            + str(b)
        )
        return v

    def fcmp_unordered(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {
            "==": "ueq",
            "!=": "une",
            "<": "ult",
            "<=": "ule",
            ">": "ugt",
            ">=": "uge",
        }
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(
            str(v)
            + " = fcmp "
            + str(pred)
            + " "
            + str(a.type)
            + " "
            + str(a)
            + ", "
            + str(b)
        )
        return v

    # ------------- call -------------

    def call(
        self,
        fn: Function | Value,
        args: Iterable[Value],
        name: str = "",
        tail: bool = False,
    ) -> Value:
        return _irbuilder_call_from_args_list(self, fn, list(args), name, tail)

    def call4_i32(
        self,
        fn: Function | Value,
        arg0: Value,
        arg1: Value,
        arg2: Value,
        arg3: int,
        name: str = "",
        tail: bool = False,
    ) -> Value:
        """Emit a four-argument call whose final operand is a raw i32.

        Kept as a small compatibility wrapper for call sites that pass a raw
        source line number into runtime helpers such as py_exc_append_frame.
        """
        del tail
        return self.call(
            fn,
            [arg0, arg1, arg2, Constant(IntType(32), arg3)],
            name=name,
        )

    # ------------- select / phi -------------

    def select(
        self,
        cond: Value,
        then_v: Value,
        else_v: Value,
        name: str = "",
    ) -> Value:
        v = self._next(name, then_v.type)
        self._emit(
            str(v)
            + " = select "
            + str(cond.type)
            + " "
            + str(cond)
            + ", "
            + str(then_v.type)
            + " "
            + str(then_v)
            + ", "
            + str(else_v.type)
            + " "
            + str(else_v)
        )
        return v

    def phi(self, ty: Type, name: str = "") -> "PhiInstr":
        phi_name = self._next_name(name)
        v = PhiInstr(self, ty, phi_name)
        self._emit(v._placeholder_line)
        return v

    # ------------- landingpad / invoke / extract_value -------------

    def invoke(
        self,
        fn: Function | Value,
        args: Iterable[Value],
        normal_block: Block,
        unwind_block: Block,
        name: str = "",
    ) -> Value:
        args_list = list(args)
        arg_parts = []
        for a in args_list:
            arg_parts.append(str(a.type) + " " + str(a))
        args_text = _join_text(arg_parts, ", ")
        if isinstance(fn, Function):
            callee_ref = "@" + str(fn.name)
            ret_ty = fn.ftype.return_type
            arg_type_parts = []
            for t in fn.ftype.args:
                arg_type_parts.append(str(t))
            arg_types = _join_text(arg_type_parts, ", ")
            sig_text = str(ret_ty) + " (" + arg_types + ")"
        else:
            callee_ref = str(fn)
            fty = fn.type.pointee
            ret_ty = fty.return_type
            arg_type_parts = []
            for t in fty.args:
                arg_type_parts.append(str(t))
            arg_types = _join_text(arg_type_parts, ", ")
            if fty.var_arg:
                arg_types = arg_types + ", ..." if arg_types else "..."
            sig_text = str(ret_ty) + " (" + arg_types + ")"

        if isinstance(ret_ty, VoidType):
            self._emit(
                "invoke "
                + sig_text
                + " "
                + callee_ref
                + "("
                + args_text
                + ") to label %"
                + str(normal_block.name)
                + " unwind label %"
                + str(unwind_block.name)
            )
            if self._block is not None:
                self._block._terminated = True
            return Value(VoidType(), "")
        v = self._next(name, ret_ty)
        self._emit(
            str(v)
            + " = invoke "
            + sig_text
            + " "
            + callee_ref
            + "("
            + args_text
            + ") to label %"
            + str(normal_block.name)
            + " unwind label %"
            + str(unwind_block.name)
        )
        if self._block is not None:
            self._block._terminated = True
        return v

    def atomic_rmw(
        self,
        op: str,
        ptr: Value,
        val: Value,
        ordering: str,
        name: str = "",
    ) -> Value:
        v = self._next(name, val.type)
        self._emit(
            str(v)
            + " = atomicrmw "
            + str(op)
            + " "
            + str(ptr.type)
            + " "
            + str(ptr)
            + ", "
            + str(val.type)
            + " "
            + str(val)
            + " "
            + str(ordering)
        )
        return v

    def cmpxchg(
        self,
        ptr: Value,
        cmp: Value,
        val: Value,
        success_ordering: str,
        failure_ordering: str,
        name: str = "",
    ) -> Value:
        pair_ty = LiteralStructType([cmp.type, _I1])
        v = self._next(name, pair_ty)
        self._emit(
            str(v)
            + " = cmpxchg "
            + str(ptr.type)
            + " "
            + str(ptr)
            + ", "
            + str(cmp.type)
            + " "
            + str(cmp)
            + ", "
            + str(val.type)
            + " "
            + str(val)
            + " "
            + str(success_ordering)
            + " "
            + str(failure_ordering)
        )
        return v

    def landingpad(
        self,
        ty: Type,
        name: str = "",
        cleanup: bool = False,
    ) -> "LandingPadInstr":
        v = LandingPadInstr(self, ty, self._next_name(name), cleanup)
        self._emit(v._placeholder_line)
        return v

    def extract_value(self, agg: Value, indices, name: str = "") -> Value:
        if isinstance(indices, int):
            indices = [indices]
        idx_parts = []
        for i in indices:
            idx_parts.append(str(i))
        idx_text = _join_text(idx_parts, ", ")
        # Element type is tricky without struct-type info; default to i32
        # for aggregates we don't know (pcc's uses are typically phi-
        # aggregates of (ptr, i32) where index 1 → i32).
        if isinstance(agg.type, LiteralStructType) and isinstance(indices[0], int):
            elem_ty = agg.type.elements[indices[0]]
        else:
            elem_ty = IntType(32)
        v = self._next(name, elem_ty)
        self._emit(
            str(v)
            + " = extractvalue "
            + str(agg.type)
            + " "
            + str(agg)
            + ", "
            + idx_text
        )
        return v


def _irbuilder_call_from_args_list(
    builder: IRBuilder,
    fn: Function | Value,
    args_list,
    name: str = "",
    tail: bool = False,
) -> Value:
    if _debug_ir_call_enabled():
        try:
            sys.stderr.write(
                "[pcc.ir.call] enter argc=" + str(len(args_list)) + "\n"
            )
        except Exception:
            pass
    expected_arg_types = []
    if _looks_like_function(fn):
        callee_ref = "@" + str(fn.name)
        ret_ty = fn.ftype.return_type
        arg_type_parts = []
        for t in fn.ftype.args:
            expected_arg_types.append(t)
            arg_type_parts.append(str(t))
        arg_types = _join_text(arg_type_parts, ", ")
        if fn.ftype.var_arg:
            arg_types = arg_types + ", ..." if arg_types else "..."
        sig_text = str(ret_ty) + " (" + arg_types + ")"
    else:
        callee_ref = str(fn)
        if _looks_like_pointer_type(fn.type) and _looks_like_function_type(
            fn.type.pointee
        ):
            fty = fn.type.pointee
            ret_ty = fty.return_type
            arg_type_parts = []
            for t in fty.args:
                expected_arg_types.append(t)
                arg_type_parts.append(str(t))
            arg_types = _join_text(arg_type_parts, ", ")
            if fty.var_arg:
                arg_types = arg_types + ", ..." if arg_types else "..."
            sig_text = str(ret_ty) + " (" + arg_types + ")"
        else:
            ret_ty = VoidType()
            sig_text = "void ()"

    if _debug_ir_call_enabled():
        try:
            sys.stderr.write(
                "[pcc.ir.call] sig callee="
                + str(callee_ref)
                + " expected="
                + str(len(expected_arg_types))
                + "\n"
            )
        except Exception:
            pass

    arg_parts = []
    i = 0
    while i < len(args_list):
        a = args_list[i]
        if i < len(expected_arg_types):
            arg_ty = expected_arg_types[i]
        else:
            arg_ty = a.type
        arg_parts.append(str(arg_ty) + " " + _value_ref(a))
        i += 1
    args_text = ", ".join(arg_parts)
    tail_prefix = "tail " if tail else ""
    if _looks_like_void_type(ret_ty):
        line = tail_prefix + "call " + sig_text + " " + callee_ref + "("
        line = line + args_text + ")"
        if _debug_ir_call_enabled():
            try:
                sys.stderr.write("[pcc.ir.call] emit void\n")
            except Exception:
                pass
        builder._emit(line)
        return Value(VoidType(), "")
    v = builder._next(name, ret_ty)
    line = str(v) + " = " + tail_prefix + "call " + sig_text + " " + callee_ref
    line = line + "(" + args_text + ")"
    if _debug_ir_call_enabled():
        try:
            sys.stderr.write("[pcc.ir.call] emit value\n")
        except Exception:
            pass
    builder._emit(line)
    return v


# ---------------------------------------------------------------------------
# Phi + LandingPad — mutated after construction via add_incoming / add_clause
# ---------------------------------------------------------------------------


class PhiInstr(Value):
    """Phi node — initially an empty placeholder; ``add_incoming`` appends
    incoming-value clauses before the IR is rendered."""

    def __init__(self, builder: IRBuilder, ty: Type, name: str) -> None:
        self.type = ty
        self._ref = "%" + str(name)
        self._builder = builder
        self._incomings: list[tuple[Value, Block]] = []
        self._placeholder_line = ""
        self._refresh()

    def _refresh(self) -> None:
        pair_parts = []
        for v, b in self._incomings:
            pair_parts.append("[" + str(v) + ", %" + str(b.name) + "]")
        pairs = ""
        if len(pair_parts) == 1:
            pairs = pair_parts[0]
        elif len(pair_parts) >= 2:
            pairs = pair_parts[0] + ", " + pair_parts[1]
            i = 2
            while i < len(pair_parts):
                pairs = pairs + ", " + pair_parts[i]
                i += 1
        if pairs:
            self._placeholder_line = (
                str(self) + " = phi " + str(self.type) + " " + pairs
            )
        else:
            self._placeholder_line = str(self) + " = phi " + str(self.type)
        # Update the emitted line in-place — scan records for the
        # prior placeholder and replace its text field.
        blk: Block = self._builder._block
        if blk is None:
            return
        i = 0
        while i < len(blk._instrs):
            rec = blk._instrs[i]
            if rec.text.startswith(str(self) + " = phi"):
                blk._replace_record_text(rec, self._placeholder_line)
                return
            i += 1

    def add_incoming(self, value: Value, block: Block) -> None:
        self._incomings.append((value, block))
        self._refresh()


class LandingPadInstr(Value):
    """Landingpad — exception personality + clauses."""

    def __init__(self, builder: IRBuilder, ty: Type, name: str, cleanup: bool) -> None:
        self.type = ty
        self._ref = "%" + str(name)
        self._builder = builder
        self._clauses: list[str] = []
        self._cleanup = cleanup
        self._placeholder_line = ""
        self._refresh()

    def _refresh(self) -> None:
        clause_text = _join_text(self._clauses, "\n    ") if self._clauses else ""
        cleanup_text = " cleanup" if self._cleanup else ""
        if clause_text:
            line = (
                str(self)
                + " = landingpad "
                + str(self.type)
                + "\n    "
                + clause_text
                + cleanup_text
            )
        else:
            line = str(self) + " = landingpad " + str(self.type) + cleanup_text
        self._placeholder_line = line
        blk = self._builder.block
        if blk is None:
            return
        i = 0
        while i < len(blk._instrs):
            rec = blk._instrs[i]
            if rec.text.startswith(str(self) + " = landingpad"):
                blk._replace_record_text(rec, line)
                return
            i += 1

    def add_clause(self, clause: "CatchClause | FilterClause") -> None:
        self._clauses.append(clause.render())
        self._refresh()


class CatchClause:
    """Catch clause for a landingpad: ``catch <ty> <val>``."""

    def __init__(self, value: Value) -> None:
        self.value = value

    def render(self) -> str:
        return "catch " + str(self.value.type) + " " + str(self.value)


class SwitchInstr:
    """``switch <ty> <val>, label %<default> [ <case-list> ]``.

    Created by ``IRBuilder.switch``. Cases accumulate via
    ``add_case(int_value, target_block)``; each case appears on its
    own indented line in the final text.
    """

    def __init__(
        self,
        builder: IRBuilder,
        value: Value,
        default: Block,
    ) -> None:
        self.builder = builder
        self.value = value
        self.default = default
        self.cases: list[tuple[int, Block]] = []
        self._instr = None

    def add_case(self, int_value, target: Block) -> None:
        """Register a ``<int> -> %target`` case. ``int_value`` may be
        a Python int or a Constant."""
        if isinstance(int_value, Value):
            try:
                int_value = int(int_value.value)
            except (AttributeError, TypeError, ValueError):
                int_value = int_value._ref
        self.cases.append((int_value, target))
        # Refresh the rendered line in-place so the final text carries
        # all accumulated cases.
        self._refresh()

    def _render(self) -> str:
        val_ty = self.value.type
        parts = []
        for iv, blk in self.cases:
            parts.append(str(val_ty) + " " + str(iv) + ", label %" + str(blk.name))
        cases_text = _join_text(parts, " ")
        body = " [ " + cases_text + " ]" if cases_text else " [ ]"
        return (
            "switch "
            + str(val_ty)
            + " "
            + str(self.value)
            + ", label %"
            + str(self.default.name)
            + body
        )

    def _refresh(self) -> None:
        blk = self.builder.block
        if blk is None:
            return
        # Replace the placeholder line in the current block's
        # instruction list. Expected prefix: "switch <ty>".
        new_line = self._render()
        if self._instr is not None:
            self._instr.block._replace_record_text(self._instr, new_line)
            return
        idx = 0
        while idx < len(blk._instrs):
            rec = blk._instrs[idx]
            if rec.text.startswith("switch "):
                blk._replace_record_text(rec, new_line)
                return
            idx += 1


class FilterClause:
    """Filter clause: ``filter <ty> [<val>, ...]``."""

    def __init__(self, ty: Type, values: Iterable[Value]) -> None:
        self.ty = ty
        self.values = tuple(values)

    def render(self) -> str:
        val_parts = []
        for v in self.values:
            val_parts.append(str(v.type) + " " + str(v))
        vals_text = _join_text(val_parts, ", ")
        return "filter " + str(self.ty) + " [" + vals_text + "]"


# ---------------------------------------------------------------------------
# ON-mode scaffold ABI helpers.
# ---------------------------------------------------------------------------
#
# The Python frontend's closed-world IR scaffold calls these helpers from the
# compiler being self-hosted. They allocate real pcc.llvm_capi.ir objects and
# keep target-program runtime symbols as IR objects, not native addresses in
# the compiler binary.


def scaffold_IntType(width: int):
    return IntType(width)


def scaffold_PointerType(pointee):
    return PointerType(pointee)


def scaffold_ArrayType(element, count: int):
    return ArrayType(element, count)


def scaffold_Constant_i64(ty, value: int):
    return Constant(ty, value)


def scaffold_Constant_f64(ty, value: float):
    return Constant(ty, value)


def scaffold_Constant_none(ty):
    return Constant(ty, None)


def scaffold_Constant_obj(ty, value):
    return Constant(ty, value)


def scaffold_IRBuilder(block):
    return IRBuilder(block)


def scaffold_Context():
    return Context()


def scaffold_IdentifiedStructType(context, name):
    return IdentifiedStructType(context, name)


def scaffold_Value(ty, ref):
    return Value(ty, str(ref))


def VoidType___init__():
    return VoidType()


def FloatType___init__():
    return FloatType()


def HalfType___init__():
    return HalfType()


def DoubleType___init__():
    return DoubleType()


def FunctionType___init__0(return_type):
    return FunctionType(return_type, ())


def FunctionType___init__1(return_type, arg0):
    return FunctionType(return_type, (arg0,))


def FunctionType___init__1_varargs(return_type, arg0):
    return FunctionType(return_type, (arg0,), var_arg=True)


def FunctionType___init__0_dyn_va(return_type, var_arg):
    return FunctionType(return_type, (), var_arg=bool(var_arg))


def FunctionType___init__1_dyn_va(return_type, arg0, var_arg):
    return FunctionType(return_type, (arg0,), var_arg=bool(var_arg))


def FunctionType___init__2_dyn_va(return_type, arg0, arg1, var_arg):
    return FunctionType(return_type, (arg0, arg1), var_arg=bool(var_arg))


def FunctionType___init__2(return_type, arg0, arg1):
    return FunctionType(return_type, (arg0, arg1))


def FunctionType___init___dyn(return_type, args, var_arg):
    return FunctionType(return_type, args, var_arg=bool(var_arg))


def Function___init___named(module, function_type, name):
    return Function(module, function_type, name=name)


def GlobalVariable___init___named(module, ty, name):
    return GlobalVariable(module, ty, name=name)


def Module___init___named(name):
    return Module(name=name)


def scaffold_Module___init__():
    return Module(name="")


def LiteralStructType___init__1(arg0):
    return LiteralStructType((arg0,))


def LiteralStructType___init__2(arg0, arg1):
    return LiteralStructType((arg0, arg1))


def LiteralStructType___init__3(arg0, arg1, arg2):
    return LiteralStructType((arg0, arg1, arg2))


def LiteralStructType___init__4(arg0, arg1, arg2, arg3):
    return LiteralStructType((arg0, arg1, arg2, arg3))


def LiteralStructType___init__5(arg0, arg1, arg2, arg3, arg4):
    return LiteralStructType((arg0, arg1, arg2, arg3, arg4))


def LiteralStructType___init__6(arg0, arg1, arg2, arg3, arg4, arg5):
    return LiteralStructType((arg0, arg1, arg2, arg3, arg4, arg5))


def LiteralStructType___init__7(arg0, arg1, arg2, arg3, arg4, arg5, arg6):
    return LiteralStructType((arg0, arg1, arg2, arg3, arg4, arg5, arg6))


def IRBuilder_call0(builder, fn):
    return _irbuilder_call_from_args_list(builder, fn, [])


def IRBuilder_call1(builder, fn, arg0):
    return _irbuilder_call_from_args_list(builder, fn, [arg0])


def IRBuilder_call2(builder, fn, arg0, arg1):
    return _irbuilder_call_from_args_list(builder, fn, [arg0, arg1])


def IRBuilder_call3(builder, fn, arg0, arg1, arg2):
    return _irbuilder_call_from_args_list(builder, fn, [arg0, arg1, arg2])


def IRBuilder_call4(builder, fn, arg0, arg1, arg2, arg3):
    return _irbuilder_call_from_args_list(builder, fn, [arg0, arg1, arg2, arg3])


def IRBuilder_call4_i32(builder, fn, arg0, arg1, arg2, arg3: int):
    return IRBuilder.call4_i32(builder, fn, arg0, arg1, arg2, arg3)


def IRBuilder_call5(builder, fn, arg0, arg1, arg2, arg3, arg4):
    return _irbuilder_call_from_args_list(builder, fn, [arg0, arg1, arg2, arg3, arg4])


def IRBuilder_call6(builder, fn, arg0, arg1, arg2, arg3, arg4, arg5):
    return _irbuilder_call_from_args_list(
        builder, fn, [arg0, arg1, arg2, arg3, arg4, arg5]
    )


def IRBuilder_call7(builder, fn, arg0, arg1, arg2, arg3, arg4, arg5, arg6):
    return _irbuilder_call_from_args_list(
        builder, fn, [arg0, arg1, arg2, arg3, arg4, arg5, arg6]
    )


def IRBuilder_call_dyn(builder, fn, args):
    return IRBuilder.call(builder, fn, args)


def IRBuilder_emit_raw(builder, line: str):
    return IRBuilder._emit(builder, line)


def IRBuilder_next_value(builder, name: str, typ):
    return IRBuilder._next(builder, name, typ)


def IRBuilder_current_instruction_count(builder) -> int:
    block = builder._block
    if block is None:
        return 0
    return len(block._instrs)


def IRBuilder_instruction_text_at(builder, index: int) -> str:
    block = builder._block
    if block is None:
        return ""
    if index < 0 or index >= len(block._instrs):
        return ""
    return block._instrs[index].text


def IRBuilder_gep1(builder, ptr, idx0):
    return IRBuilder.gep(builder, ptr, (idx0,))


def IRBuilder_gep2_inbounds(builder, ptr, idx0, idx1):
    return IRBuilder.gep(builder, ptr, (idx0, idx1), inbounds=True)


def IRBuilder_add_incoming(phi: PhiInstr, value: Value, block: Block):
    phi._incomings.append((value, block))
    phi._refresh()


def scaffold_SwitchInstr_add_case_i64(
    switch_inst: SwitchInstr,
    int_value: int,
    target: Block,
):
    switch_inst.cases.append((int_value, target))
    new_line = switch_inst._render()
    for blk in switch_inst.default.parent.blocks:
        for rec in blk._instrs:
            if rec.text.startswith("switch ") and f"label %{switch_inst.default.name}" in rec.text:
                blk._replace_record_text(rec, new_line)
                return


def IRBuilder_as_pointer(ty):
    return PointerType(ty)


def scaffold_IRBuilder_append_basic_block(builder, name):
    return scaffold_Function_append_basic_block(builder._fn, name)


def scaffold_Function_append_basic_block(fn, name):
    if not name:
        name = "bb"
    blk = Block(fn, name)
    fn.blocks.append(blk)
    return blk


# ---------------------------------------------------------------------------
# Public re-exports matching llvmlite.ir
# ---------------------------------------------------------------------------

__all__ = [
    # Types
    "Type",
    "VoidType",
    "IntType",
    "HalfType",
    "FloatType",
    "DoubleType",
    "PointerType",
    "ArrayType",
    "BaseStructType",
    "LiteralStructType",
    "IdentifiedStructType",
    "FunctionType",
    "Context",
    "global_context",
    # Values / constants
    "Value",
    "Constant",
    "Undefined",
    # Containers
    "Argument",
    "Block",
    "Function",
    "FunctionAttributes",
    "GlobalVariable",
    "Module",
    # Builder + insts
    "IRBuilder",
    "IRBuilder_emit_raw",
    "IRBuilder_next_value",
    "IRBuilder_current_instruction_count",
    "IRBuilder_instruction_text_at",
    "PhiInstr",
    "LandingPadInstr",
    "SwitchInstr",
    "CatchClause",
    "FilterClause",
]


# ---------------------------------------------------------------------------
# llvmlite sub-module shims
# ---------------------------------------------------------------------------
#
# llvmlite exposes some names via submodules (``ir.values.Constant``,
# ``ir.types.IntType``, etc.). pcc code uses a few of these paths; expose
# the same attribute chains from this single module so existing imports
# keep working without source edits.


class _ValuesShim:
    """Stand-in for ``llvmlite.ir.values`` — just re-exports Value and
    Constant from this module. pcc.codegen.c_codegen uses
    ``ir.values.Constant(...)``."""


values = _ValuesShim()
values.Value = Value
values.Constant = Constant
values.Argument = Argument
values.Function = Function
values.GlobalVariable = GlobalVariable
