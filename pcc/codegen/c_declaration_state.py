"""File-scope declaration state records for the C frontend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileScopeObjectState:
    type_key: str
    linkage: str
    definition_kind: str
    symbol_name: str
    ir_type: object


@dataclass
class FileScopeFunctionState:
    type_key: str
    function_type: object
    linkage: str
    defined: bool
    symbol_name: str


class CodegenError(Exception):
    """Raised for a C construct that cannot be lowered faithfully."""


class ExternGlobalRef:
    def __init__(self, symbol_name, ir_type) -> None:
        self.symbol_name = symbol_name
        self.ir_type = ir_type
