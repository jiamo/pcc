from __future__ import annotations

"""Shared IR/data model layer for the self backend.

This module is target-neutral. It contains the parsed LLVM-IR-facing data model
used by target emitters, plus layout helpers that are independent of any
specific register set or calling convention.
"""

from dataclasses import dataclass, field

from . import BackendUnavailable


def _dot_numeric_text_key_id(text: str) -> int:
    suffix = ""
    if len(text) > 2 and text.startswith("%."):
        suffix = text[2:]
    elif len(text) > 1 and text.startswith("."):
        suffix = text[1:]
    if suffix and suffix.isdigit():
        return int(suffix)
    return -1


def text_key_names_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    left_id = _dot_numeric_text_key_id(left)
    if left_id < 0:
        return False
    return left_id == _dot_numeric_text_key_id(right)


def text_collection_contains(values, key: str) -> bool:
    if key in values:
        return True
    key_id = _dot_numeric_text_key_id(key)
    if key_id < 0:
        return False
    for existing in values:
        if key_id == _dot_numeric_text_key_id(existing):
            return True
    return False


def text_key_mapping_get(mapping, key: str):
    """Return a text-keyed mapping value despite a false hash miss."""
    result = mapping.get(key)
    if result is not None:
        return result
    for existing_key, existing_value in mapping.items():
        if text_key_names_equal(existing_key, key):
            return existing_value
    return None


def parsed_function_value_slot(func, key: str):
    return text_key_mapping_get(func.value_slots, key)


def _align_to(value: int, alignment: int) -> int:
    if value == 0:
        return 0
    return ((value + alignment - 1) // alignment) * alignment


@dataclass(frozen=True)
class TypeDesc:
    kind: str
    width: int = 0
    pointee: "TypeDesc | None" = None
    count: int = 0
    elem: "TypeDesc | None" = None
    name: str = ""
    fields: tuple["TypeDesc", ...] = ()

    @property
    def is_void(self) -> bool:
        return self.kind == "void"

    @property
    def is_int(self) -> bool:
        return self.kind == "int"

    @property
    def is_fp(self) -> bool:
        return self.kind == "fp"

    @property
    def is_ptr(self) -> bool:
        return self.kind == "ptr"

    @property
    def is_array(self) -> bool:
        return self.kind == "array"

    @property
    def is_struct(self) -> bool:
        return self.kind == "struct"

    @property
    def bits(self) -> int:
        if self.is_ptr:
            return 64
        if self.is_int or self.is_fp:
            return self.width
        return 0

    @property
    def slot_size(self) -> int:
        if self.is_void:
            return 0
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        if self.is_array:
            elem: TypeDesc = self.elem
            assert elem is not None
            stride = _align_to(elem.slot_size, elem.align)
            return stride * self.count
        if self.is_struct:
            offset = 0
            max_align = 1
            for member in self.fields:
                offset = _align_to(offset, member.align)
                offset += member.slot_size
                max_align = max(max_align, member.align)
            return _align_to(offset, max_align)
        if self.width <= 8:
            return 1
        if self.width <= 16:
            return 2
        return 4

    @property
    def value_slot_size(self) -> int:
        if self.is_void:
            return 0
        if self.is_array or self.is_struct:
            return self.slot_size
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        return 4

    @property
    def align(self) -> int:
        if self.is_void:
            return 1
        if self.is_array:
            elem: TypeDesc = self.elem
            assert elem is not None
            return elem.align
        if self.is_struct:
            result = 1
            for member in self.fields:
                if member.align > result:
                    result = member.align
            return result
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        if self.width <= 8:
            return 1
        if self.width <= 16:
            return 2
        return 4

    @property
    def value_align(self) -> int:
        if self.is_void:
            return 1
        if self.is_array or self.is_struct:
            return self.align
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        return 4

    def ptr(self) -> "TypeDesc":
        return TypeDesc("ptr", pointee=self)

    def describe(self) -> str:
        if self.is_void:
            return "void"
        if self.is_int:
            return f"i{self.width}"
        if self.is_fp:
            return "float" if self.width <= 32 else "double"
        if self.is_array:
            elem: TypeDesc = self.elem
            assert elem is not None
            return f"[{self.count} x {elem.describe()}]"
        if self.is_struct:
            return self.name or "<anon-struct>"
        pointee: TypeDesc = self.pointee
        assert pointee is not None
        return pointee.describe() + "*"

    def field_offset(self, index: int) -> int:
        if not self.is_struct:
            raise BackendUnavailable(
                f"field_offset requested on non-struct {self.describe()}"
            )
        if index < 0 or index >= len(self.fields):
            raise BackendUnavailable(
                f"struct field index {index} out of range for {self.describe()}"
            )
        offset = 0
        for field_index, member in enumerate(self.fields):
            offset = _align_to(offset, member.align)
            if field_index == index:
                return offset
            offset += member.slot_size
        raise BackendUnavailable(
            f"struct field index {index} out of range for {self.describe()}"
        )

    def field_type(self, index: int) -> "TypeDesc":
        if not self.is_struct:
            raise BackendUnavailable(
                f"field_type requested on non-struct {self.describe()}"
            )
        return self.fields[index]


def aggregate_member_info(
    value_type: TypeDesc, indices: tuple[int, ...]
) -> tuple[TypeDesc, int]:
    current = value_type
    offset = 0
    for index in indices:
        if current.is_array:
            if index < 0 or index >= current.count:
                raise BackendUnavailable(
                    f"array index {index} out of range for {current.describe()}"
                )
            assert current.elem is not None
            stride = _align_to(current.elem.slot_size, current.elem.align)
            offset += index * stride
            current = current.elem
            continue
        if current.is_struct:
            offset += current.field_offset(index)
            current = current.field_type(index)
            continue
        raise BackendUnavailable(
            f"aggregate member requested on non-aggregate {current.describe()}"
        )
    return current, offset


@dataclass(frozen=True)
class ArgInfo:
    name: str
    type: TypeDesc


@dataclass(frozen=True)
class PhiIncoming:
    value: str
    label: str


@dataclass(frozen=True)
class PhiInstr:
    dest: str
    type: TypeDesc
    incoming: tuple[PhiIncoming, ...]


@dataclass(frozen=True)
class ParsedInstr:
    kind: str
    data: tuple


@dataclass
class ParsedBlock:
    name: str
    raw_lines: list[str] = field(default_factory=list)
    phis: list[PhiInstr] = field(default_factory=list)
    instructions: list[ParsedInstr] = field(default_factory=list)
    terminator: ParsedInstr | None = None


@dataclass(frozen=True)
class SlotInfo:
    offset: int
    type: TypeDesc


@dataclass(frozen=True)
class AllocaInfo:
    offset: int
    allocated_type: TypeDesc


@dataclass(frozen=True)
class GlobalDef:
    name: str
    type: TypeDesc
    initializer: str
    is_constant: bool
    is_internal: bool


@dataclass(frozen=True)
class ParsedModule:
    triple: str
    globals_: tuple[GlobalDef, ...]
    functions: tuple["ParsedFunction", ...]


@dataclass
class ParsedFunction:
    name: str
    ret_type: TypeDesc
    args: list[ArgInfo]
    is_global: bool
    is_vararg: bool
    blocks: list[ParsedBlock]
    value_types: dict[str, TypeDesc] = field(default_factory=dict)
    value_slots: dict[str, SlotInfo] = field(default_factory=dict)
    alloca_slots: dict[str, AllocaInfo] = field(default_factory=dict)
    block_map: dict[str, ParsedBlock] = field(default_factory=dict)
    # Membership must be equality-based during native bootstrap.  A set lookup
    # can falsely miss an equal text key produced through another runtime path.
    used_values: list[str] = field(default_factory=list)
    hidden_sret_slot: SlotInfo | None = None
    frame_size: int = 0


I1 = TypeDesc("int", 1)


__all__ = [
    "aggregate_member_info",
    "ArgInfo",
    "AllocaInfo",
    "GlobalDef",
    "I1",
    "ParsedBlock",
    "ParsedFunction",
    "ParsedInstr",
    "PhiIncoming",
    "PhiInstr",
    "SlotInfo",
    "TypeDesc",
    "_align_to",
    "parsed_function_value_slot",
    "text_collection_contains",
    "text_key_mapping_get",
    "text_key_names_equal",
]
