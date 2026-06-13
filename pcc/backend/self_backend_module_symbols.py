from __future__ import annotations

"""Target-neutral module-symbol preparation for the self backend."""

from dataclasses import dataclass

from .self_backend_ir import GlobalDef, ParsedFunction


@dataclass(frozen=True)
class PreparedModuleSymbols:
    internal_prefix: str
    defined_symbols: frozenset[str]
    internal_symbols: frozenset[str]


def _stable_symbol_digest(text: str) -> str:
    """Return a deterministic 40-bit digest without a host hashlib edge."""
    # Keep every intermediate inside pcc's proven tagged-small-int lane.  The
    # self emitter is itself compiled by pcc, so a hash that intentionally
    # relies on u64 overflow would make its own symbol namespace depend on an
    # unproven bignum/value-lane transition.
    modulus = 1099511627776
    value = 0
    i = 0
    while i < len(text):
        value = (value * 131 + ord(text[i])) % modulus
        i += 1
    digits = "0123456789abcdef"
    out = ""
    shift = 36
    while shift >= 0:
        out += digits[(value >> shift) & 15]
        shift -= 4
    return out


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
    internal_prefix = "__pccmod_" + _stable_symbol_digest(prefix_seed) + "_"
    return PreparedModuleSymbols(
        internal_prefix=internal_prefix,
        defined_symbols=defined_symbols,
        internal_symbols=internal_symbols,
    )
