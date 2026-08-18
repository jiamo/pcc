from __future__ import annotations

"""Target-neutral preparation helpers for parsed self-backend modules."""

from dataclasses import dataclass

from .self_backend_ir import ParsedFunction, ParsedModule
from .self_backend_module_symbols import PreparedModuleSymbols, prepare_module_symbols
from .self_backend_parse import parse_self_backend_module
from .self_backend_stackprep import assign_stack_slots
from .self_backend_verify import verify_parsed_module


@dataclass(frozen=True)
class PreparedSelfBackendModule:
    triple: str
    globals_: list
    functions: list[ParsedFunction]
    module_symbols: PreparedModuleSymbols


def prepare_parsed_function(func: ParsedFunction) -> None:
    func.block_map = {block.name: block for block in func.blocks}
    for arg in func.args:
        func.value_types[arg.name] = arg.type


def prepare_parsed_functions(functions: list[ParsedFunction]) -> None:
    for func in functions:
        prepare_parsed_function(func)


def prepare_module_for_target(
    ir_text: str,
    *,
    aggregate_returned_indirect,
    aggregate_returned_indirect_indexed=None,
    materialize_legacy_slots: bool = True,
) -> PreparedSelfBackendModule:
    module = parse_self_backend_module(ir_text)
    return prepare_parsed_module_for_target(
        module,
        aggregate_returned_indirect=aggregate_returned_indirect,
        aggregate_returned_indirect_indexed=aggregate_returned_indirect_indexed,
        materialize_legacy_slots=materialize_legacy_slots,
    )


def prepare_parsed_module_for_target(
    module: ParsedModule,
    *,
    aggregate_returned_indirect,
    aggregate_returned_indirect_indexed=None,
    materialize_legacy_slots: bool = True,
) -> PreparedSelfBackendModule:
    """Prepare an already-final parsed/indexed module without reparsing text."""
    # The verifier constructs each function's indexed kernel at its definition
    # boundary.  Every later migrated pass then observes the same stable IDs.
    verify_parsed_module(module)
    globals_ = list(module.globals_)
    functions = list(module.functions)
    prepare_parsed_functions(functions)
    for func in functions:
        assign_stack_slots(
            func,
            aggregate_returned_indirect=aggregate_returned_indirect,
            aggregate_returned_indirect_indexed=(
                aggregate_returned_indirect_indexed
            ),
            materialize_legacy_slots=materialize_legacy_slots,
        )
    module_symbols = prepare_module_symbols("", globals_, functions)
    return PreparedSelfBackendModule(
        triple=module.triple,
        globals_=globals_,
        functions=functions,
        module_symbols=module_symbols,
    )
