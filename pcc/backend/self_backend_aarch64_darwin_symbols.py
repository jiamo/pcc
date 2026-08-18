from __future__ import annotations

import re

from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import check_simple_symbol_name


def asm_symbol(name: str, module_symbols: PreparedModuleSymbols) -> str:
    check_simple_symbol_name(name)
    return asm_symbol_prevalidated(name, module_symbols)


def asm_symbol_prevalidated(
    name: str, module_symbols: PreparedModuleSymbols
) -> str:
    """Mangle a symbol already validated at the parse boundary."""

    if name in module_symbols.internal_symbols:
        return f"_{module_symbols.internal_prefix}{name}"
    return f"_{name}"


def sanitize_label(value: str) -> str:
    text = value.replace(".", "dot")
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def block_label(func_name: str, block_name: str) -> str:
    return f"L_{func_name}_{sanitize_label(block_name)}"


def block_edge_label(func_name: str, source: str, target: str) -> str:
    return f"L_{func_name}_{sanitize_label(source)}_to_{sanitize_label(target)}"
