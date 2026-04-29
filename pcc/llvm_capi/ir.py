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
        return f"<{self._name} {str(self)}>"

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
        return f"i{self.width}"

    def __call__(self, value: int) -> "Constant":
        """``IntType(32)(0)`` shorthand for ``Constant(IntType(32), 0)``."""
        return Constant(self, value)


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
            return f"ptr addrspace({self.addrspace})"
        return "ptr"


class ArrayType(Type):
    _name = "array"

    def __init__(self, element: Type, count: int) -> None:
        self.element = element
        self.count = count

    def __str__(self) -> str:
        return f"[{self.count} x {self.element}]"

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
        body = ", ".join(str(e) for e in self.elements)
        if self.packed:
            return f"<{{ {body} }}>"
        return f"{{ {body} }}"


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
            return f"%{self.name} = type opaque"
        body = ", ".join(str(e) for e in self.elements)
        if self.packed:
            return f"%{self.name} = type <{{ {body} }}>"
        return f"%{self.name} = type {{ {body} }}"

    def __str__(self) -> str:
        # As an operand, refer by its name — actual body is emitted
        # at module top via ``get_declaration``.
        return f"%{self.name}"


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
        args_text = ", ".join(str(a) for a in self.args)
        if self.var_arg:
            args_text = f"{args_text}, ..." if args_text else "..."
        return f"{self.return_type} ({args_text})"


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
            body = old[eq + 3:]
            head = old[:eq] + " = "
        else:
            body = old
            head = ""
        # First token is opcode; inject flags between opcode and rest.
        split = body.find(" ")
        if split >= 0:
            op = body[:split]
            tail = body[split + 1:]
        else:
            op = body
            tail = ""
        flag_text = ("".join(f" {f}" for f in self._flags)).lstrip()
        if flag_text:
            new = f"{head}{op} {flag_text} {tail}"
        else:
            new = f"{head}{op} {tail}"
        self._instr.text = new

    def bitcast(self, target_ty: Type) -> "Value":
        expr = f"bitcast ({self.type} {self._ref} to {target_ty})"
        return Value(target_ty, expr)

    def gep(self, indices, inbounds: bool = True) -> "Value":
        indices_list = list(indices)
        idx_text = ", ".join(f"{i.type} {i}" for i in indices_list)
        base_ty = self.type.pointee if isinstance(self.type, PointerType) else self.type
        result_pointee = _type_gep_result(base_ty, indices_list[1:])
        inb = "inbounds " if inbounds else ""
        expr = (
            f"getelementptr {inb}({base_ty}, {self.type} "
            f"{self._ref}, {idx_text})"
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
        expr = f"inttoptr ({self.type} {self._ref} to {target_ty})"
        return Value(target_ty, expr)

    def bitcast(self, target_ty: Type) -> Value:
        """Constant-expression bitcast. Same shape as ``inttoptr``."""
        expr = f"bitcast ({self.type} {self._ref} to {target_ty})"
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
            parts = [Constant(ty.element, v)._ref for v in value]
            # Each operand is printed as ``<elem_ty> <val>``
            body = ", ".join(f"{ty.element} {p}" for p in parts)
            return f"[{body}]"
        if isinstance(ty, BaseStructType):
            # struct constant: { <ty1> <val1>, <ty2> <val2>, ... }
            parts = []
            for elem_ty, val in zip(ty.elements, value):
                c = Constant(elem_ty, val)
                parts.append(f"{elem_ty} {c._ref}")
            return "{ " + ", ".join(parts) + " }"
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
        self._ref = f"%.{index + 1}"

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        self._ref = f"%{value}" if value else f"%.{self.index + 1}"


class InstructionRecord:
    """A single emitted instruction — text form + opname tag.

    Codegen sometimes needs to know "is this an alloca?" or "is this
    a terminator?" when hoisting or positioning new instructions.
    llvmlite's Instruction has ``opname``; we expose the same property
    derived from the first token of the emitted text."""

    __slots__ = ("text", "opname", "block")

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
            stripped = stripped[eq + 3:]
    # First whitespace-delimited token is the opcode
    idx = stripped.find(" ")
    return stripped[:idx] if idx >= 0 else stripped


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
        self._instrs.append(InstructionRecord(line, _opname_of(line), self))

    def insert(self, idx: int, line: str) -> None:
        self._instrs.insert(
            idx, InstructionRecord(line, _opname_of(line), self),
        )

    def render(self) -> str:
        """Render as ``name:\\n  instr1\\n  instr2\\n``."""
        header = f"{self.name}:\n"
        body = "\n".join(f"  {r.text}" for r in self._instrs)
        return header + (body + "\n" if body else "")


class Function:
    """A function in a Module. Holds a signature, blocks, and a name
    counter for temp %N identifiers."""

    def __init__(
        self,
        module: "Module",
        function_type: FunctionType,
        name: str = "",
    ) -> None:
        self.module = module
        self.ftype = function_type
        # llvmlite also exposes ``function_type``; alias both so codegen
        # code that reads either attribute works.
        self.function_type = function_type
        # As a value, a function has a pointer type — matches llvmlite
        # (``Function.type`` is ``PointerType(FunctionType, 0)``).
        self.type = PointerType(function_type)
        self.name = name
        self.blocks: list[Block] = []
        # Formal parameter objects
        self.args = tuple(
            Argument(ty, i) for i, ty in enumerate(function_type.args)
        )
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
            name = f"bb{self._block_counter}"
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
        return f".{self._name_counter}"

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
        while f"{name}.{n}" in self._name_registry:
            n += 1
        self._name_registry[f"{name}.{n}"] = 1
        return f"{name}.{n}"

    def __str__(self) -> str:
        """Function-as-operand: render as ``@name`` so it can be used
        directly as a call target or inside constant expressions."""
        return f"@{self.name}"

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
        for arg in self.args:
            if arg._name:
                arg_parts.append(f"{arg.type} %{arg._name}")
            else:
                arg_parts.append(f"{arg.type} {arg._ref}")
        if fty.var_arg:
            arg_parts.append("...")
        args_text = ", ".join(arg_parts)
        linkage = f"{self.linkage} " if self.linkage else ""

        # Personality + attributes (declarations don't carry them).
        pers_text = ""
        attrs_text = ""
        if self.attributes.personality is not None:
            pers = self.attributes.personality
            pers_ty = PointerType(pers.ftype)
            pers_text = f" personality {pers_ty} @{pers.name}"
        if self.attributes._attrs:
            attrs_text = " " + " ".join(sorted(self.attributes._attrs))

        if not self.blocks:
            arg_type_only = ", ".join(str(t) for t in fty.args)
            if fty.var_arg:
                arg_type_only = f"{arg_type_only}, ..." if arg_type_only else "..."
            return (
                f"declare {linkage}{ret_ty} @{self.name}({arg_type_only})\n"
            )
        body = "\n".join(b.render() for b in self.blocks)
        return (
            f"define {linkage}{ret_ty} @{self.name}({args_text})"
            f"{attrs_text}{pers_text} {{\n"
            f"{body}}}\n"
        )


class FunctionAttributes:
    """Function attributes set (``noreturn``, ``alwaysinline``, etc.) plus
    a ``personality`` slot for EH-emitting functions. Rendered inline
    with the function definition."""

    def __init__(self) -> None:
        self._attrs: set[str] = set()
        self.personality: Optional["Function"] = None

    def add(self, attr: str) -> None:
        self._attrs.add(attr)

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
        self._ref = f"@{name}"
        module._globals.append(self)
        module.globals[name] = self

    def gep(self, indices, inbounds: bool = True) -> Value:
        """Constant-expression GEP on this global — emits inline as
        ``getelementptr (inbounds) (<ty>, ptr @name, i32 i0, i32 i1, ...)``.
        Used by codegen to materialize a pointer-to-first-element of
        a global array or struct without a separate builder call."""
        indices_list = list(indices)
        idx_parts = []
        for v in indices_list:
            idx_parts.append(f"{v.type} {v}")
        idx_text = ", ".join(idx_parts)
        inb = "inbounds " if inbounds else ""
        result_pointee = _type_gep_result(self.value_type, indices_list[1:])
        expr = (
            f"getelementptr {inb}({self.value_type}, {self.type} "
            f"@{self.name}, {idx_text})"
        )
        return Value(PointerType(result_pointee), expr)

    def render(self) -> str:
        linkage = f"{self.linkage} " if self.linkage else ""
        kind = "constant" if self.global_constant else "global"
        init_text = ""
        if self.initializer is not None:
            init_text = f" {self.initializer._ref}" if isinstance(
                self.initializer, Value
            ) else f" {self.initializer}"
        else:
            init_text = " zeroinitializer" if self.linkage != "external" else ""
        align_text = f", align {self.align}" if self.align else ""
        return (
            f"@{self.name} = {linkage}{kind} {self.value_type}{init_text}"
            f"{align_text}\n"
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
        return f"{base}.{n}"

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
        if self.name:
            parts.append(f'; ModuleID = "{self.name}"')
        # Always emit triple + datalayout (even if empty) to match llvmlite
        parts.append(f'target triple = "{self.triple}"')
        parts.append(f'target datalayout = "{self.data_layout}"')
        parts.append("")
        # Identified struct type declarations — must precede globals
        # and functions that reference them.
        for t in self.context.identified_types.values():
            parts.append(t.get_declaration())
        if self.context.identified_types:
            parts.append("")
        for gv in self._globals:
            parts.append(gv.render())
        for fn in self._functions:
            parts.append(fn.render())
        return "\n".join(parts)


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
        blk = getattr(instr_or_block, "block", None) or self._block
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
            return f".{fn._name_counter}"
        return f"{name}.{fn._name_counter}"

    def _next(self, name: str, ty: Type) -> Value:
        """Produce a new local SSA value with ``name`` (or a fresh temp
        if empty). Named temporaries use a function-wide serial suffix,
        trading llvmlite text parity for O(1) self-hosted codegen."""
        return Value(ty, f"%{self._next_name(name)}")

    # ------------- return / branch / terminator -------------

    def ret(self, value: Value) -> Value:
        self._emit(f"ret {value.type} {value}")
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def ret_void(self) -> Value:
        self._emit("ret void")
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def branch(self, target: Block) -> Value:
        self._emit(f"br label %{target.name}")
        if self._block is not None:
            self._block._terminated = True
        return Value(VoidType(), "")

    def cbranch(self, cond: Value, t: Block, f: Block) -> Value:
        self._emit(f"br i1 {cond}, label %{t.name}, label %{f.name}")
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
        self._emit(sw._render())
        if self._block is not None:
            self._block._terminated = True
        return sw

    # ------------- memory -------------

    def alloca(self, ty: Type, size: Optional[Value] = None, name: str = "") -> Value:
        v = self._next(name, PointerType(ty))
        size_text = f", {size.type} {size}" if size is not None else ""
        self._emit(f"{v} = alloca {ty}{size_text}")
        return v

    def load(self, ptr: Value, name: str = "", align: Optional[int] = None) -> Value:
        pointee = ptr.type.pointee if isinstance(ptr.type, PointerType) else ptr.type
        v = self._next(name, pointee)
        align_text = f", align {align}" if align else ""
        self._emit(f"{v} = load {pointee}, {ptr.type} {ptr}{align_text}")
        return v

    def store(self, value: Value, ptr: Value, align: Optional[int] = None) -> Value:
        align_text = f", align {align}" if align else ""
        self._emit(
            f"store {value.type} {value}, {ptr.type} {ptr}{align_text}"
        )
        return Value(VoidType(), "")

    def fence(self, ordering: str, syncscope: Optional[str] = None) -> Value:
        scope_text = f'syncscope("{syncscope}") ' if syncscope else ""
        self._emit(f"fence {scope_text}{ordering}")
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
        idx_text = ", ".join(f"{i.type} {i}" for i in indices_list)
        inb = "inbounds " if inbounds else ""
        self._emit(
            f"{v} = getelementptr {inb}{base_ty}, {ptr.type} {ptr}, {idx_text}"
        )
        return v

    # ------------- casts -------------

    def bitcast(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = bitcast {value.type} {value} to {ty}")
        return v

    def sext(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = sext {value.type} {value} to {ty}")
        return v

    def zext(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = zext {value.type} {value} to {ty}")
        return v

    def trunc(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = trunc {value.type} {value} to {ty}")
        return v

    def ptrtoint(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = ptrtoint {value.type} {value} to {ty}")
        return v

    def inttoptr(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = inttoptr {value.type} {value} to {ty}")
        return v

    def sitofp(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = sitofp {value.type} {value} to {ty}")
        return v

    def uitofp(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = uitofp {value.type} {value} to {ty}")
        return v

    def fptosi(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = fptosi {value.type} {value} to {ty}")
        return v

    def fptoui(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = fptoui {value.type} {value} to {ty}")
        return v

    def fpext(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = fpext {value.type} {value} to {ty}")
        return v

    def fptrunc(self, value: Value, ty: Type, name: str = "") -> Value:
        v = self._next(name, ty)
        self._emit(f"{v} = fptrunc {value.type} {value} to {ty}")
        return v

    # ------------- integer arithmetic -------------

    def _int_binop(self, op: str, lhs: Value, rhs: Value, name: str) -> Value:
        v = self._next(name, lhs.type)
        self._emit(f"{v} = {op} {lhs.type} {lhs}, {rhs}")
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
        rec = self._emit(f"{v} = {op} {lhs.type} {lhs}, {rhs}")
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
        rec = self._emit(f"{v} = fneg {value.type} {value}")
        v._instr = rec
        return v

    # ------------- comparisons -------------

    def icmp_signed(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {"==": "eq", "!=": "ne", "<": "slt", "<=": "sle",
                    ">": "sgt", ">=": "sge"}
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(f"{v} = icmp {pred} {a.type} {a}, {b}")
        return v

    def icmp_unsigned(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {"==": "eq", "!=": "ne", "<": "ult", "<=": "ule",
                    ">": "ugt", ">=": "uge"}
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(f"{v} = icmp {pred} {a.type} {a}, {b}")
        return v

    def fcmp_ordered(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {"==": "oeq", "!=": "one", "<": "olt", "<=": "ole",
                    ">": "ogt", ">=": "oge"}
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(f"{v} = fcmp {pred} {a.type} {a}, {b}")
        return v

    def fcmp_unordered(self, op: str, a: Value, b: Value, name: str = "") -> Value:
        pred_map = {"==": "ueq", "!=": "une", "<": "ult", "<=": "ule",
                    ">": "ugt", ">=": "uge"}
        pred = pred_map.get(op, op)
        v = self._next(name, IntType(1))
        self._emit(f"{v} = fcmp {pred} {a.type} {a}, {b}")
        return v

    # ------------- call -------------

    def call(
        self,
        fn: Function | Value,
        args: Iterable[Value],
        name: str = "",
        tail: bool = False,
    ) -> Value:
        args_list = list(args)
        args_text = ", ".join(f"{a.type} {a}" for a in args_list)
        if isinstance(fn, Function):
            callee_ref = f"@{fn.name}"
            ret_ty = fn.ftype.return_type
            arg_types = ", ".join(str(t) for t in fn.ftype.args)
            if fn.ftype.var_arg:
                arg_types = f"{arg_types}, ..." if arg_types else "..."
            sig_text = f"{ret_ty} ({arg_types})"
        else:
            # Function pointer value
            callee_ref = str(fn)
            # Must have a FunctionType somewhere; require caller to
            # pre-cast to a FunctionType-pointee pointer.
            if isinstance(fn.type, PointerType) and isinstance(fn.type.pointee, FunctionType):
                fty = fn.type.pointee
                ret_ty = fty.return_type
                arg_types = ", ".join(str(t) for t in fty.args)
                if fty.var_arg:
                    arg_types = f"{arg_types}, ..." if arg_types else "..."
                sig_text = f"{ret_ty} ({arg_types})"
            else:
                ret_ty = VoidType()
                sig_text = "void ()"

        tail_prefix = "tail " if tail else ""
        if isinstance(ret_ty, VoidType):
            self._emit(f"{tail_prefix}call {sig_text} {callee_ref}({args_text})")
            return Value(VoidType(), "")
        v = self._next(name, ret_ty)
        self._emit(
            f"{v} = {tail_prefix}call {sig_text} {callee_ref}({args_text})"
        )
        return v

    # ------------- select / phi -------------

    def select(
        self, cond: Value, then_v: Value, else_v: Value, name: str = "",
    ) -> Value:
        v = self._next(name, then_v.type)
        self._emit(
            f"{v} = select {cond.type} {cond}, {then_v.type} {then_v}, "
            f"{else_v.type} {else_v}"
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
        args_text = ", ".join(f"{a.type} {a}" for a in args_list)
        if isinstance(fn, Function):
            callee_ref = f"@{fn.name}"
            ret_ty = fn.ftype.return_type
            arg_types = ", ".join(str(t) for t in fn.ftype.args)
            sig_text = f"{ret_ty} ({arg_types})"
        else:
            callee_ref = str(fn)
            fty = fn.type.pointee
            ret_ty = fty.return_type
            arg_types = ", ".join(str(t) for t in fty.args)
            if fty.var_arg:
                arg_types = f"{arg_types}, ..." if arg_types else "..."
            sig_text = f"{ret_ty} ({arg_types})"

        if isinstance(ret_ty, VoidType):
            self._emit(
                f"invoke {sig_text} {callee_ref}({args_text}) "
                f"to label %{normal_block.name} unwind label %{unwind_block.name}"
            )
            if self._block is not None:
                self._block._terminated = True
            return Value(VoidType(), "")
        v = self._next(name, ret_ty)
        self._emit(
            f"{v} = invoke {sig_text} {callee_ref}({args_text}) "
            f"to label %{normal_block.name} unwind label %{unwind_block.name}"
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
        self._emit(f"{v} = atomicrmw {op} {ptr.type} {ptr}, {val.type} {val} {ordering}")
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
        pair_ty = LiteralStructType([cmp.type, IntType(1)])
        v = self._next(name, pair_ty)
        self._emit(
            f"{v} = cmpxchg {ptr.type} {ptr}, {cmp.type} {cmp}, "
            f"{val.type} {val} {success_ordering} {failure_ordering}"
        )
        return v

    def landingpad(
        self, ty: Type, name: str = "", cleanup: bool = False,
    ) -> "LandingPadInstr":
        v = LandingPadInstr(self, ty, self._next_name(name), cleanup)
        self._emit(v._placeholder_line)
        return v

    def extract_value(self, agg: Value, indices, name: str = "") -> Value:
        if isinstance(indices, int):
            indices = [indices]
        idx_text = ", ".join(str(i) for i in indices)
        # Element type is tricky without struct-type info; default to i32
        # for aggregates we don't know (pcc's uses are typically phi-
        # aggregates of (ptr, i32) where index 1 → i32).
        if isinstance(agg.type, LiteralStructType) and isinstance(indices[0], int):
            elem_ty = agg.type.elements[indices[0]]
        else:
            elem_ty = IntType(32)
        v = self._next(name, elem_ty)
        self._emit(f"{v} = extractvalue {agg.type} {agg}, {idx_text}")
        return v


# ---------------------------------------------------------------------------
# Phi + LandingPad — mutated after construction via add_incoming / add_clause
# ---------------------------------------------------------------------------


class PhiInstr(Value):
    """Phi node — initially an empty placeholder; ``add_incoming`` appends
    incoming-value clauses before the IR is rendered."""

    def __init__(self, builder: IRBuilder, ty: Type, name: str) -> None:
        self.type = ty
        self._ref = f"%{name}"
        self._builder = builder
        self._incomings: list[tuple[Value, Block]] = []
        self._placeholder_line = ""
        self._refresh()

    def _refresh(self) -> None:
        pairs = ", ".join(
            f"[{v}, %{b.name}]" for v, b in self._incomings
        )
        self._placeholder_line = (
            f"{self} = phi {self.type} {pairs}" if pairs
            else f"{self} = phi {self.type}"
        )
        # Update the emitted line in-place — scan records for the
        # prior placeholder and replace its text field.
        blk: Block = self._builder._block
        if blk is None:
            return
        for rec in blk._instrs:
            if rec.text.startswith(str(self) + " = phi"):
                rec.text = self._placeholder_line
                return

    def add_incoming(self, value: Value, block: Block) -> None:
        self._incomings.append((value, block))
        self._refresh()


class LandingPadInstr(Value):
    """Landingpad — exception personality + clauses."""

    def __init__(self, builder: IRBuilder, ty: Type, name: str, cleanup: bool) -> None:
        self.type = ty
        self._ref = f"%{name}"
        self._builder = builder
        self._clauses: list[str] = []
        self._cleanup = cleanup
        self._placeholder_line = ""
        self._refresh()

    def _refresh(self) -> None:
        clause_text = "\n    ".join(self._clauses) if self._clauses else ""
        cleanup_text = " cleanup" if self._cleanup else ""
        if clause_text:
            line = f"{self} = landingpad {self.type}\n    {clause_text}{cleanup_text}"
        else:
            line = f"{self} = landingpad {self.type}{cleanup_text}"
        self._placeholder_line = line
        blk = self._builder.block
        if blk is None:
            return
        for rec in blk._instrs:
            if rec.text.startswith(str(self) + " = landingpad"):
                rec.text = line
                return

    def add_clause(self, clause: "CatchClause | FilterClause") -> None:
        self._clauses.append(clause.render())
        self._refresh()


class CatchClause:
    """Catch clause for a landingpad: ``catch <ty> <val>``."""

    def __init__(self, value: Value) -> None:
        self.value = value

    def render(self) -> str:
        return f"catch {self.value.type} {self.value}"


class SwitchInstr:
    """``switch <ty> <val>, label %<default> [ <case-list> ]``.

    Created by ``IRBuilder.switch``. Cases accumulate via
    ``add_case(int_value, target_block)``; each case appears on its
    own indented line in the final text.
    """

    def __init__(
        self, builder: IRBuilder, value: Value, default: Block,
    ) -> None:
        self.builder = builder
        self.value = value
        self.default = default
        self.cases: list[tuple[int, Block]] = []

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
        cases_text = " ".join(
            f"{val_ty} {iv}, label %{blk.name}"
            for iv, blk in self.cases
        )
        body = f" [ {cases_text} ]" if cases_text else " [ ]"
        return (
            f"switch {val_ty} {self.value}, label %{self.default.name}{body}"
        )

    def _refresh(self) -> None:
        blk = self.builder.block
        if blk is None:
            return
        # Replace the placeholder line in the current block's
        # instruction list. Expected prefix: "switch <ty>".
        new_line = self._render()
        for rec in blk._instrs:
            if rec.text.startswith("switch "):
                rec.text = new_line
                return


class FilterClause:
    """Filter clause: ``filter <ty> [<val>, ...]``."""

    def __init__(self, ty: Type, values: Iterable[Value]) -> None:
        self.ty = ty
        self.values = tuple(values)

    def render(self) -> str:
        vals_text = ", ".join(f"{v.type} {v}" for v in self.values)
        return f"filter {self.ty} [{vals_text}]"


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


def LiteralStructType___init__3(arg0, arg1, arg2):
    return LiteralStructType((arg0, arg1, arg2))


def IRBuilder_call0(builder, fn):
    return IRBuilder.call(builder, fn, ())


def IRBuilder_call1(builder, fn, arg0):
    return IRBuilder.call(builder, fn, (arg0,))


def IRBuilder_call2(builder, fn, arg0, arg1):
    return IRBuilder.call(builder, fn, (arg0, arg1))


def IRBuilder_call3(builder, fn, arg0, arg1, arg2):
    return IRBuilder.call(builder, fn, (arg0, arg1, arg2))


def IRBuilder_call4(builder, fn, arg0, arg1, arg2, arg3):
    return IRBuilder.call(builder, fn, (arg0, arg1, arg2, arg3))


def IRBuilder_call5(builder, fn, arg0, arg1, arg2, arg3, arg4):
    return IRBuilder.call(builder, fn, (arg0, arg1, arg2, arg3, arg4))


def IRBuilder_call6(builder, fn, arg0, arg1, arg2, arg3, arg4, arg5):
    return IRBuilder.call(builder, fn, (arg0, arg1, arg2, arg3, arg4, arg5))


def IRBuilder_call7(builder, fn, arg0, arg1, arg2, arg3, arg4, arg5, arg6):
    return IRBuilder.call(builder, fn, (arg0, arg1, arg2, arg3, arg4, arg5, arg6))


def IRBuilder_call_dyn(builder, fn, args):
    return IRBuilder.call(builder, fn, args)


def IRBuilder_gep1(builder, ptr, idx0):
    return IRBuilder.gep(builder, ptr, (idx0,))


def IRBuilder_gep2_inbounds(builder, ptr, idx0, idx1):
    return IRBuilder.gep(builder, ptr, (idx0, idx1), inbounds=True)


def IRBuilder_add_incoming(phi: PhiInstr, value: Value, block: Block):
    phi._incomings.append((value, block))
    phi._refresh()


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
    "Type", "VoidType", "IntType", "HalfType", "FloatType", "DoubleType",
    "PointerType", "ArrayType", "BaseStructType",
    "LiteralStructType", "IdentifiedStructType", "FunctionType",
    "Context", "global_context",
    # Values / constants
    "Value", "Constant", "Undefined",
    # Containers
    "Argument", "Block", "Function", "FunctionAttributes",
    "GlobalVariable", "Module",
    # Builder + insts
    "IRBuilder", "PhiInstr", "LandingPadInstr", "SwitchInstr",
    "CatchClause", "FilterClause",
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
