from __future__ import annotations

"""Target-neutral module-symbol preparation for the self backend."""

import hashlib
from dataclasses import dataclass

from .self_backend_ir import GlobalDef, ParsedFunction


@dataclass(frozen=True)
class PreparedModuleSymbols:
    internal_prefix: str
    defined_symbols: frozenset[str]
    internal_symbols: frozenset[str]


def prepare_module_symbols(
    ir_text: str,
    globals_: list[GlobalDef],
    functions: list[ParsedFunction],
) -> PreparedModuleSymbols:
    defined_symbols = frozenset(
        {global_.name for global_ in globals_} | {func.name for func in functions}
    )
    internal_symbols = frozenset(
        {global_.name for global_ in globals_ if global_.is_internal}
        | {func.name for func in functions if not func.is_global}
    )
    public_symbols = sorted(
        {global_.name for global_ in globals_ if not global_.is_internal}
        | {func.name for func in functions if func.is_global}
    )
    if public_symbols:
        prefix_seed = "\n".join(public_symbols)
    else:
        prefix_seed = "\n".join(sorted(defined_symbols))
    internal_prefix = (
        "__pccmod_"
        + hashlib.sha1(prefix_seed.encode("utf-8")).hexdigest()[:10]
        + "_"
    )
    return PreparedModuleSymbols(
        internal_prefix=internal_prefix,
        defined_symbols=defined_symbols,
        internal_symbols=internal_symbols,
    )
