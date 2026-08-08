"""Namespaced theme utilities and selective component style dependencies.

The numeric values remain owned by :mod:`pcc_gui_theme_anim`.  This module
owns only the bounded utility registry, immutable 40-byte operations and the
component dependency/dirty table.  A token edit dirties exact token users; a
namespace schema edit dirties users of that namespace.  Applying an operation
never parses text or allocates.

The bounded class grammar is intentionally smaller than CSS/Tailwind::

    class      := SP* candidate (SP+ candidate)* SP*
    candidate  := "-"? prefix "-" value ("/" modifier)?
    prefix     := "bg" | "text" | "font" | "w" | "h" |
                  "pad" | "gap" | "x" | "y"
    value      := decimal-token-id | namespace-owned stable name
    modifier   := 0..100 | "half" | "full" | "dense" | "opaque" |
                  "[" modifier "]"

Only ASCII space separates candidates, names are case-sensitive, and an exact
duplicate candidate is rejected.  A leading negative prefix is governed by
the registered utility; it is never conflated with the optional slash
modifier.  The bracket form records arbitrary-modifier provenance while still
accepting only the same bounded values.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, extern
from pcc.unsafe import (
    calloc,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    load_i8,
    memcpy,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
    store_i8,
)


GENERATOR_SIZE = 32
DEPENDENCY_SIZE = 56
STYLE_OPERATION_SIZE = 40

NAMESPACE_COLOUR = 0
NAMESPACE_FONT = 1
NAMESPACE_SIZE = 2
NAMESPACE_SPACING = 3
NAMESPACE_COUNT = 4
TOKEN_COUNT = 16

STYLE_BG = 1
STYLE_TEXT_COLOUR = 2
STYLE_FONT = 3
STYLE_WIDTH = 4
STYLE_HEIGHT = 5
STYLE_PADDING = 6
STYLE_GAP = 7
STYLE_X = 8
STYLE_Y = 9

OP_NEGATIVE = 1
OP_MODIFIER_NAMED = 2
OP_MODIFIER_ARBITRARY = 4
OP_PERCENT_SHIFT = 8
OP_PERCENT_MASK = 0x7F00

PREFIX_BG = 1
PREFIX_TEXT = 2
PREFIX_FONT = 3
PREFIX_WIDTH = 4
PREFIX_HEIGHT = 5
PREFIX_PADDING = 6
PREFIX_GAP = 7
PREFIX_X = 8
PREFIX_Y = 9
PREFIX_COUNT = 9

CACHE_ENTRY_SIZE = 80
CACHE_CLASS_BYTES = 257
CACHE_OPERATION_CAPACITY = 64
CACHE_NAMESPACE_DEP_SIZE = 16
CACHE_TOKEN_DEP_SIZE = 16
CACHE_VALID = 1
CACHE_COMPILING = 2

OK = 0
ERR_CAPACITY = -101
ERR_DUPLICATE_KEY = -102
ERR_INVALID_TRANSITION = -103
ERR_OWNERSHIP = -105
ERR_STALE = -106
ERR_INVALID_CANDIDATE = -117
ERR_UNKNOWN_CANDIDATE = -118
ERR_AMBIGUOUS_CANDIDATE = -119


_theme_active_get = extern(
    "pcc_gui_theme_active_get", (c_int32, c_int32), c_int64
)
_theme_token_generation = extern(
    "pcc_gui_theme_token_generation", (c_int32, c_int32), c_int64
)
_theme_namespace_generation = extern(
    "pcc_gui_theme_namespace_generation", (c_int32,), c_int64
)
_component_owner = extern(
    "pcc_gui_component_owner_for_node", (c_int64,), c_int64
)
_component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
_kit_style_set = extern(
    "pcc_kit_style_set", (c_int64, c_int32, c_int64), c_int32
)


define_global_i64("pcc_gui_style_generators", 0)
define_global_i64("pcc_gui_style_generator_capacity", 0)
define_global_i64("pcc_gui_style_dependencies", 0)
define_global_i64("pcc_gui_style_dependency_capacity", 0)
define_global_i64("pcc_gui_style_schema_generation_storage", 1)
define_global_i64("pcc_gui_style_cache_records", 0)
define_global_i64("pcc_gui_style_cache_classes", 0)
define_global_i64("pcc_gui_style_cache_operations", 0)
define_global_i64("pcc_gui_style_cache_namespace_dependencies", 0)
define_global_i64("pcc_gui_style_cache_token_dependencies", 0)
define_global_i64("pcc_gui_style_cache_capacity", 0)
define_global_i64("pcc_gui_style_cache_epoch", 1)
define_global_i64("pcc_gui_style_parser_invocations_storage", 0)
define_global_i64("pcc_gui_style_cache_allocations", 0)
define_global_i64("pcc_gui_style_cache_hits", 0)
define_global_i64("pcc_gui_style_cache_misses", 0)


def _base(name: str) -> int:
    if name == "pcc_gui_style_generators":
        return load_i64(global_addr("pcc_gui_style_generators"), 0)
    if name == "pcc_gui_style_generator_capacity":
        return load_i64(global_addr("pcc_gui_style_generator_capacity"), 0)
    if name == "pcc_gui_style_dependencies":
        return load_i64(global_addr("pcc_gui_style_dependencies"), 0)
    if name == "pcc_gui_style_dependency_capacity":
        return load_i64(global_addr("pcc_gui_style_dependency_capacity"), 0)
    if name == "pcc_gui_style_schema_generation_storage":
        return load_i64(global_addr("pcc_gui_style_schema_generation_storage"), 0)
    if name == "pcc_gui_style_cache_records":
        return load_i64(global_addr("pcc_gui_style_cache_records"), 0)
    if name == "pcc_gui_style_cache_classes":
        return load_i64(global_addr("pcc_gui_style_cache_classes"), 0)
    if name == "pcc_gui_style_cache_operations":
        return load_i64(global_addr("pcc_gui_style_cache_operations"), 0)
    if name == "pcc_gui_style_cache_namespace_dependencies":
        return load_i64(
            global_addr("pcc_gui_style_cache_namespace_dependencies"), 0
        )
    if name == "pcc_gui_style_cache_token_dependencies":
        return load_i64(global_addr("pcc_gui_style_cache_token_dependencies"), 0)
    if name == "pcc_gui_style_cache_capacity":
        return load_i64(global_addr("pcc_gui_style_cache_capacity"), 0)
    if name == "pcc_gui_style_cache_epoch":
        return load_i64(global_addr("pcc_gui_style_cache_epoch"), 0)
    if name == "pcc_gui_style_parser_invocations_storage":
        return load_i64(
            global_addr("pcc_gui_style_parser_invocations_storage"), 0
        )
    if name == "pcc_gui_style_cache_allocations":
        return load_i64(global_addr("pcc_gui_style_cache_allocations"), 0)
    if name == "pcc_gui_style_cache_hits":
        return load_i64(global_addr("pcc_gui_style_cache_hits"), 0)
    if name == "pcc_gui_style_cache_misses":
        return load_i64(global_addr("pcc_gui_style_cache_misses"), 0)
    return 0


def _generator_at(index: int):
    return int_to_ptr(_base("pcc_gui_style_generators") + index * GENERATOR_SIZE)


def _dependency_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_dependencies") + index * DEPENDENCY_SIZE
    )


def _generator_index(utility_id: int) -> int:
    cap = _base("pcc_gui_style_generator_capacity")
    i = 0
    while i < cap:
        record = _generator_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) == utility_id:
            return i
        i = i + 1
    return -1


def _dependency_index(
    component_id: int, node_id: int, namespace: int, token: int
) -> int:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i64(record, 8) == component_id
            and load_i64(record, 40) == node_id
            and load_i32(record, 16) == namespace
            and load_i32(record, 20) == token
        ):
            return i
        i = i + 1
    return -1


def _clear_dependency(index: int) -> None:
    record = _dependency_at(index)
    store_i32(record, 0, 0)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i32(record, 16, 0)
    store_i32(record, 20, 0)
    store_i64(record, 24, 0)
    store_i64(record, 32, 0)
    store_i64(record, 40, 0)
    store_i64(record, 48, 0)


def _field_matches_namespace(namespace: int, field: int) -> int:
    if namespace == NAMESPACE_COLOUR:
        return 1 if field == STYLE_BG or field == STYLE_TEXT_COLOUR else 0
    if namespace == NAMESPACE_FONT:
        return 1 if field == STYLE_FONT else 0
    if namespace == NAMESPACE_SIZE:
        return 1 if field == STYLE_WIDTH or field == STYLE_HEIGHT else 0
    if namespace == NAMESPACE_SPACING:
        return 1 if field >= STYLE_PADDING and field <= STYLE_Y else 0
    return 0


def _resolved_component(component_id: int, node_id: int) -> int:
    owner = _component_owner(node_id)
    if component_id < 0:
        component_id = owner
    if (
        component_id < 0
        or owner != component_id
        or _component_valid(component_id) == 0
    ):
        return -1
    return component_id


def _dependency_slot(
    component_id: int,
    node_id: int,
    namespace: int,
    token: int,
    excluded_slot: int,
) -> int:
    index = _dependency_index(component_id, node_id, namespace, token)
    if index >= 0:
        return index
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        if i != excluded_slot and load_i32(_dependency_at(i), 0) == 0:
            return i
        i = i + 1
    return -1


def _commit_dependency(
    index: int,
    component_id: int,
    node_id: int,
    namespace: int,
    token: int,
) -> None:
    record = _dependency_at(index)
    prior_class_epoch = load_i64(record, 48)
    store_i64(record, 8, component_id)
    store_i32(record, 16, namespace)
    store_i32(record, 20, token)
    store_i64(record, 24, _theme_namespace_generation(namespace))
    store_i64(record, 32, _theme_token_generation(namespace, token))
    store_i64(record, 40, node_id)
    # A direct helper is a durable dependency until component retirement.  A
    # positive tag is class-only; negate it to remember that the same token is
    # also referenced directly.  Zero already denotes direct-only.
    if prior_class_epoch > 0:
        store_i64(record, 48, -prior_class_epoch)
    store_i32(record, 0, 1)


def _mark_class_dependency(
    component_id: int,
    node_id: int,
    namespace: int,
    token: int,
    class_epoch: int,
    prior_index: int,
    prior_tag: int,
) -> int:
    index = _dependency_index(component_id, node_id, namespace, token)
    if index < 0:
        return ERR_STALE
    record = _dependency_at(index)
    # A missing record was created by this class application and is therefore
    # class-only.  Existing direct-only/combined records retain their durable
    # direct ownership by storing the class epoch as a negative tag.
    if prior_index < 0 or prior_tag > 0:
        store_i64(record, 48, class_epoch)
    else:
        store_i64(record, 48, -class_epoch)
    return OK


def _retire_old_class_dependencies(
    component_id: int, node_id: int, class_epoch: int
) -> None:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i64(record, 8) == component_id
            and load_i64(record, 40) == node_id
        ):
            tag = load_i64(record, 48)
            if tag > 0 and tag != class_epoch:
                _clear_dependency(i)
            elif tag < 0 and -tag != class_epoch:
                # The old class stopped referencing this token, but a direct
                # helper still does.  Retain the record as direct-only.
                store_i64(record, 48, 0)
        i = i + 1


@c_abi_typed_export("pcc_gui_style_init", "i32", ("i64", "i64"))
def pcc_gui_style_init(generator_capacity: int, dependency_capacity: int) -> int:
    if (
        generator_capacity <= 0
        or generator_capacity > 256
        or dependency_capacity <= 0
        or dependency_capacity > 8192
    ):
        return ERR_CAPACITY
    # Utility ids are append-only for the process lifetime.  The frozen
    # operation record deliberately has no registry-generation field, so
    # replacing the table could make an old operation name a different field.
    if (
        _base("pcc_gui_style_generators") != 0
        or _base("pcc_gui_style_dependencies") != 0
    ):
        return ERR_INVALID_TRANSITION
    generation = _base("pcc_gui_style_schema_generation_storage") + 1
    if generation <= 0 or generation >= 0x7FFFFFFFFFFFFFFF:
        return ERR_CAPACITY
    generators = calloc(generator_capacity, GENERATOR_SIZE)
    dependencies = calloc(dependency_capacity, DEPENDENCY_SIZE)
    if ptr_is_null(generators) or ptr_is_null(dependencies):
        if not ptr_is_null(generators):
            free(generators)
        if not ptr_is_null(dependencies):
            free(dependencies)
        return ERR_CAPACITY
    store_i64(global_addr("pcc_gui_style_generators"), 0, ptr_to_int(generators))
    store_i64(
        global_addr("pcc_gui_style_dependencies"), 0, ptr_to_int(dependencies)
    )
    store_i64(
        global_addr("pcc_gui_style_generator_capacity"), 0, generator_capacity
    )
    store_i64(
        global_addr("pcc_gui_style_dependency_capacity"), 0, dependency_capacity
    )
    store_i64(
        global_addr("pcc_gui_style_schema_generation_storage"), 0, generation
    )
    return OK


def _register_utility(
    utility_id: int,
    prefix_id: int,
    namespace: int,
    style_field: int,
    allow_negative: int,
) -> int:
    if (
        utility_id <= 0
        or prefix_id < 0
        or prefix_id > PREFIX_COUNT
        or namespace < 0
        or namespace >= NAMESPACE_COUNT
        or style_field < STYLE_BG
        or style_field > STYLE_Y
        or (allow_negative != 0 and allow_negative != 1)
        or _field_matches_namespace(namespace, style_field) == 0
    ):
        return ERR_OWNERSHIP
    if _generator_index(utility_id) >= 0:
        return ERR_DUPLICATE_KEY
    cap = _base("pcc_gui_style_generator_capacity")
    i = 0
    while i < cap:
        record = _generator_at(i)
        if load_i32(record, 0) == 0:
            store_i32(record, 4, utility_id)
            store_i32(record, 8, namespace)
            store_i32(record, 12, style_field)
            store_i32(record, 16, allow_negative)
            store_i32(record, 20, prefix_id)
            store_i64(record, 24, 0)
            store_i32(record, 0, 1)
            generation = _base("pcc_gui_style_schema_generation_storage") + 1
            if generation <= 0 or generation >= 0x7FFFFFFFFFFFFFFF:
                store_i32(record, 0, 0)
                return ERR_CAPACITY
            store_i64(
                global_addr("pcc_gui_style_schema_generation_storage"),
                0,
                generation,
            )
            return OK
        i = i + 1
    return ERR_CAPACITY


@c_abi_typed_export(
    "pcc_gui_style_register_utility",
    "i32",
    ("i32", "i32", "i32", "i32"),
)
def pcc_gui_style_register_utility(
    utility_id: int, namespace: int, style_field: int, allow_negative: int
) -> int:
    # Numeric/direct helpers do not need a class-string prefix.
    return _register_utility(
        utility_id, 0, namespace, style_field, allow_negative
    )


@c_abi_typed_export(
    "pcc_gui_style_register_named_utility",
    "i32",
    ("i32", "i32", "i32", "i32", "i32"),
)
def pcc_gui_style_register_named_utility(
    utility_id: int,
    prefix_id: int,
    namespace: int,
    style_field: int,
    allow_negative: int,
) -> int:
    if prefix_id <= 0:
        return ERR_OWNERSHIP
    return _register_utility(
        utility_id, prefix_id, namespace, style_field, allow_negative
    )


@c_abi_typed_export("pcc_gui_style_schema_generation", "i64", ())
def pcc_gui_style_schema_generation() -> int:
    return _base("pcc_gui_style_schema_generation_storage")


@c_abi_typed_export(
    "pcc_gui_style_generate", "i32", ("i32", "i32", "i32", "ptr")
)
def pcc_gui_style_generate(
    utility_id: int, token: int, negative: int, operation_out
) -> int:
    if ptr_is_null(operation_out) or token < 0 or token >= TOKEN_COUNT:
        return ERR_OWNERSHIP
    index = _generator_index(utility_id)
    if index < 0:
        return ERR_STALE
    generator = _generator_at(index)
    if negative != 0 and negative != 1:
        return ERR_OWNERSHIP
    if negative != 0 and load_i32(generator, 16) == 0:
        return ERR_INVALID_TRANSITION
    namespace = load_i32(generator, 8)
    token_generation = _theme_token_generation(namespace, token)
    namespace_generation = _theme_namespace_generation(namespace)
    if token_generation <= 0 or namespace_generation <= 0:
        return ERR_STALE
    value = _theme_active_get(namespace, token)
    if negative != 0:
        if value == -0x8000000000000000:
            return ERR_INVALID_TRANSITION
        value = -value
    store_i32(operation_out, 0, utility_id)
    store_i32(operation_out, 4, namespace)
    store_i32(operation_out, 8, token)
    store_i32(operation_out, 12, OP_NEGATIVE if negative != 0 else 0)
    store_i64(operation_out, 16, value)
    store_i64(operation_out, 24, namespace_generation)
    store_i64(operation_out, 32, token_generation)
    return OK


def _operation_flags_valid(generator, namespace: int, flags: int) -> int:
    allowed = (
        OP_NEGATIVE
        | OP_MODIFIER_NAMED
        | OP_MODIFIER_ARBITRARY
        | OP_PERCENT_MASK
    )
    if flags < 0 or (flags & allowed) != flags:
        return 0
    modifier = flags & (OP_MODIFIER_NAMED | OP_MODIFIER_ARBITRARY)
    percent = (flags & OP_PERCENT_MASK) >> OP_PERCENT_SHIFT
    if modifier == 0:
        if percent != 0:
            return 0
    else:
        if (
            modifier == (OP_MODIFIER_NAMED | OP_MODIFIER_ARBITRARY)
            or namespace == NAMESPACE_FONT
            or percent < 0
            or percent > 100
            or (namespace == NAMESPACE_COLOUR and (flags & OP_NEGATIVE) != 0)
        ):
            return 0
    if (flags & OP_NEGATIVE) != 0 and load_i32(generator, 16) == 0:
        return 0
    return 1


def _operation_expected_value(namespace: int, token: int, flags: int) -> int:
    value = _theme_active_get(namespace, token)
    if (flags & OP_NEGATIVE) != 0:
        value = -value
    if (flags & (OP_MODIFIER_NAMED | OP_MODIFIER_ARBITRARY)) != 0:
        percent = (flags & OP_PERCENT_MASK) >> OP_PERCENT_SHIFT
        if namespace == NAMESPACE_COLOUR:
            alpha = (percent * 255) // 100
            value = (value & 0xFFFFFF) | (alpha << 24)
        else:
            whole = value // 100
            remainder = value - whole * 100
            value = whole * percent + (remainder * percent) // 100
    return value


@c_abi_typed_export(
    "pcc_gui_style_apply", "i32", ("i64", "i64", "ptr")
)
def pcc_gui_style_apply(component_id: int, node_id: int, operation) -> int:
    if ptr_is_null(operation):
        return ERR_OWNERSHIP
    utility_id = load_i32(operation, 0)
    index = _generator_index(utility_id)
    if index < 0:
        return ERR_STALE
    generator = _generator_at(index)
    namespace = load_i32(operation, 4)
    token = load_i32(operation, 8)
    flags = load_i32(operation, 12)
    if (
        namespace != load_i32(generator, 8)
        or namespace < 0
        or namespace >= NAMESPACE_COUNT
        or token < 0
        or token >= TOKEN_COUNT
        or _operation_flags_valid(generator, namespace, flags) == 0
    ):
        return ERR_STALE
    active_value = _theme_active_get(namespace, token)
    if (flags & OP_NEGATIVE) != 0 and active_value == -0x8000000000000000:
        return ERR_STALE
    expected_value = _operation_expected_value(namespace, token, flags)
    if (
        load_i64(operation, 16) != expected_value
        or load_i64(operation, 24) != _theme_namespace_generation(namespace)
        or load_i64(operation, 32) != _theme_token_generation(namespace, token)
    ):
        return ERR_STALE
    component_id = _resolved_component(component_id, node_id)
    if component_id < 0:
        return ERR_OWNERSHIP
    dependency_slot = _dependency_slot(
        component_id, node_id, namespace, token, -1
    )
    if dependency_slot < 0:
        return ERR_CAPACITY
    status = _kit_style_set(
        node_id, load_i32(generator, 12), load_i64(operation, 16)
    )
    if status != OK:
        return ERR_STALE
    _commit_dependency(
        dependency_slot, component_id, node_id, namespace, token
    )
    return OK


def _apply_direct(
    component_id: int, node_id: int, namespace: int, token: int, field: int
) -> int:
    if (
        token < 0
        or token >= TOKEN_COUNT
        or _field_matches_namespace(namespace, field) == 0
    ):
        return ERR_OWNERSHIP
    component_id = _resolved_component(component_id, node_id)
    if component_id < 0:
        return ERR_OWNERSHIP
    if _theme_token_generation(namespace, token) <= 0:
        return ERR_STALE
    dependency_slot = _dependency_slot(
        component_id, node_id, namespace, token, -1
    )
    if dependency_slot < 0:
        return ERR_CAPACITY
    status = _kit_style_set(node_id, field, _theme_active_get(namespace, token))
    if status != OK:
        return ERR_STALE
    _commit_dependency(
        dependency_slot, component_id, node_id, namespace, token
    )
    return OK


def _apply_two_direct(
    node_id: int,
    namespace_a: int,
    token_a: int,
    field_a: int,
    namespace_b: int,
    token_b: int,
    field_b: int,
) -> int:
    if (
        token_a < 0
        or token_a >= TOKEN_COUNT
        or token_b < 0
        or token_b >= TOKEN_COUNT
        or _field_matches_namespace(namespace_a, field_a) == 0
        or _field_matches_namespace(namespace_b, field_b) == 0
    ):
        return ERR_OWNERSHIP
    component_id = _resolved_component(-1, node_id)
    if component_id < 0:
        return ERR_OWNERSHIP
    if (
        _theme_token_generation(namespace_a, token_a) <= 0
        or _theme_token_generation(namespace_b, token_b) <= 0
    ):
        return ERR_STALE
    slot_a = _dependency_slot(
        component_id, node_id, namespace_a, token_a, -1
    )
    if slot_a < 0:
        return ERR_CAPACITY
    if namespace_a == namespace_b and token_a == token_b:
        slot_b = slot_a
    else:
        slot_b = _dependency_slot(
            component_id, node_id, namespace_b, token_b, slot_a
        )
    if slot_b < 0:
        return ERR_CAPACITY
    # The owner and both fixed field ids were validated before either write;
    # capacity failure therefore cannot leave a partially styled node.
    if _kit_style_set(
        node_id, field_a, _theme_active_get(namespace_a, token_a)
    ) != OK:
        return ERR_STALE
    if _kit_style_set(
        node_id, field_b, _theme_active_get(namespace_b, token_b)
    ) != OK:
        return ERR_STALE
    _commit_dependency(
        slot_a, component_id, node_id, namespace_a, token_a
    )
    if slot_b != slot_a:
        _commit_dependency(
            slot_b, component_id, node_id, namespace_b, token_b
        )
    return OK


@c_abi_typed_export("pcc_gui_style_bg", "i32", ("i64", "i32"))
def pcc_gui_style_bg(node_id: int, token: int) -> int:
    return _apply_direct(-1, node_id, NAMESPACE_COLOUR, token, STYLE_BG)


@c_abi_typed_export("pcc_gui_style_tx", "i32", ("i64", "i32", "i32"))
def pcc_gui_style_tx(node_id: int, colour_token: int, font_token: int) -> int:
    return _apply_two_direct(
        node_id,
        NAMESPACE_COLOUR,
        colour_token,
        STYLE_TEXT_COLOUR,
        NAMESPACE_FONT,
        font_token,
        STYLE_FONT,
    )


@c_abi_typed_export("pcc_gui_style_size", "i32", ("i64", "i32", "i32"))
def pcc_gui_style_size(node_id: int, token: int, axis: int) -> int:
    if axis == 0:
        return _apply_direct(-1, node_id, NAMESPACE_SIZE, token, STYLE_WIDTH)
    if axis == 1:
        return _apply_direct(-1, node_id, NAMESPACE_SIZE, token, STYLE_HEIGHT)
    if axis == 2:
        return _apply_two_direct(
            node_id,
            NAMESPACE_SIZE,
            token,
            STYLE_WIDTH,
            NAMESPACE_SIZE,
            token,
            STYLE_HEIGHT,
        )
    return ERR_OWNERSHIP


@c_abi_typed_export("pcc_gui_style_pad", "i32", ("i64", "i32"))
def pcc_gui_style_pad(node_id: int, token: int) -> int:
    return _apply_direct(-1, node_id, NAMESPACE_SPACING, token, STYLE_PADDING)


@c_abi_typed_export("pcc_gui_style_gap", "i32", ("i64", "i32"))
def pcc_gui_style_gap(node_id: int, token: int) -> int:
    return _apply_direct(-1, node_id, NAMESPACE_SPACING, token, STYLE_GAP)


@c_abi_typed_export(
    "pcc_gui_style_theme_token_changed", "void", ("i32", "i32")
)
def pcc_gui_style_theme_token_changed(namespace: int, token: int) -> None:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i32(record, 16) == namespace
            and load_i32(record, 20) == token
        ):
            store_i32(record, 4, 1)
        i = i + 1


@c_abi_typed_export(
    "pcc_gui_style_theme_namespace_changed", "void", ("i32",)
)
def pcc_gui_style_theme_namespace_changed(namespace: int) -> None:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 16) == namespace:
            store_i32(record, 4, 1)
        i = i + 1


@c_abi_typed_export("pcc_gui_style_component_dirty", "i32", ("i64",))
def pcc_gui_style_component_dirty(component_id: int) -> int:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i64(record, 8) == component_id
            and load_i32(record, 4) != 0
        ):
            return 1
        i = i + 1
    return 0


@c_abi_typed_export("pcc_gui_style_next_dirty", "i64", ())
def pcc_gui_style_next_dirty() -> int:
    cap = _base("pcc_gui_style_dependency_capacity")
    best = -1
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) != 0:
            component_id = load_i64(record, 8)
            if best < 0 or component_id < best:
                best = component_id
        i = i + 1
    return best


@c_abi_typed_export("pcc_gui_style_component_did_commit", "i32", ("i64",))
def pcc_gui_style_component_did_commit(component_id: int) -> int:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if load_i32(record, 0) != 0 and load_i64(record, 8) == component_id:
            namespace = load_i32(record, 16)
            token = load_i32(record, 20)
            if (
                load_i64(record, 24) != _theme_namespace_generation(namespace)
                or load_i64(record, 32) != _theme_token_generation(namespace, token)
            ):
                return ERR_STALE
        i = i + 1
    i = 0
    while i < cap:
        record = _dependency_at(i)
        if load_i32(record, 0) != 0 and load_i64(record, 8) == component_id:
            store_i32(record, 4, 0)
        i = i + 1
    return OK


@c_abi_typed_export("pcc_gui_style_component_unmounted", "void", ("i64",))
def pcc_gui_style_component_unmounted(component_id: int) -> None:
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        if (
            load_i32(_dependency_at(i), 0) != 0
            and load_i64(_dependency_at(i), 8) == component_id
        ):
            _clear_dependency(i)
        i = i + 1


# ---------------------------------------------------------------------------
# Bounded class-string compiler/cache.  All cache storage is allocated once by
# compiler_init.  Cold compilation parses into those fixed arenas; warm apply
# performs no parsing and no allocation.


def _cache_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_cache_records") + index * CACHE_ENTRY_SIZE
    )


def _cache_class_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_cache_classes") + index * CACHE_CLASS_BYTES
    )


def _cache_operations_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_cache_operations")
        + index * CACHE_OPERATION_CAPACITY * STYLE_OPERATION_SIZE
    )


def _cache_namespace_dependencies_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_cache_namespace_dependencies")
        + index * NAMESPACE_COUNT * CACHE_NAMESPACE_DEP_SIZE
    )


def _cache_token_dependencies_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_style_cache_token_dependencies")
        + index * CACHE_OPERATION_CAPACITY * CACHE_TOKEN_DEP_SIZE
    )


@c_abi_typed_export("pcc_gui_style_compiler_init", "i32", ("i64",))
def pcc_gui_style_compiler_init(cache_capacity: int) -> int:
    if cache_capacity <= 0 or cache_capacity > 128:
        return ERR_CAPACITY
    if _base("pcc_gui_style_generators") == 0:
        return ERR_INVALID_TRANSITION
    if _base("pcc_gui_style_cache_records") != 0:
        return ERR_INVALID_TRANSITION
    records = calloc(cache_capacity, CACHE_ENTRY_SIZE)
    classes = calloc(cache_capacity, CACHE_CLASS_BYTES)
    operations = calloc(
        cache_capacity * CACHE_OPERATION_CAPACITY, STYLE_OPERATION_SIZE
    )
    namespace_dependencies = calloc(
        cache_capacity * NAMESPACE_COUNT, CACHE_NAMESPACE_DEP_SIZE
    )
    token_dependencies = calloc(
        cache_capacity * CACHE_OPERATION_CAPACITY, CACHE_TOKEN_DEP_SIZE
    )
    if (
        ptr_is_null(records)
        or ptr_is_null(classes)
        or ptr_is_null(operations)
        or ptr_is_null(namespace_dependencies)
        or ptr_is_null(token_dependencies)
    ):
        if not ptr_is_null(records):
            free(records)
        if not ptr_is_null(classes):
            free(classes)
        if not ptr_is_null(operations):
            free(operations)
        if not ptr_is_null(namespace_dependencies):
            free(namespace_dependencies)
        if not ptr_is_null(token_dependencies):
            free(token_dependencies)
        return ERR_CAPACITY
    store_i64(global_addr("pcc_gui_style_cache_records"), 0, ptr_to_int(records))
    store_i64(global_addr("pcc_gui_style_cache_classes"), 0, ptr_to_int(classes))
    store_i64(
        global_addr("pcc_gui_style_cache_operations"), 0, ptr_to_int(operations)
    )
    store_i64(
        global_addr("pcc_gui_style_cache_namespace_dependencies"),
        0,
        ptr_to_int(namespace_dependencies),
    )
    store_i64(
        global_addr("pcc_gui_style_cache_token_dependencies"),
        0,
        ptr_to_int(token_dependencies),
    )
    store_i64(global_addr("pcc_gui_style_cache_capacity"), 0, cache_capacity)
    store_i64(global_addr("pcc_gui_style_cache_allocations"), 0, 5)
    i = 0
    while i < cache_capacity:
        entry = _cache_at(i)
        store_i64(entry, 8, ptr_to_int(_cache_class_at(i)))
        store_i64(entry, 32, ptr_to_int(_cache_operations_at(i)))
        store_i64(
            entry, 48, ptr_to_int(_cache_namespace_dependencies_at(i))
        )
        store_i64(entry, 56, ptr_to_int(_cache_token_dependencies_at(i)))
        i = i + 1
    return OK


def _candidate_hash(data, length: int) -> int:
    value = 1469598103934665603
    i = 0
    while i < length:
        value = value ^ (load_i8(data, i) & 255)
        value = (value * 1099511628211) & 0x7FFFFFFFFFFFFFFF
        i = i + 1
    if value == 0:
        return 1
    return value


def _bytes_equal(left, right, length: int) -> int:
    i = 0
    while i < length:
        if load_i8(left, i) != load_i8(right, i):
            return 0
        i = i + 1
    return 1


def _slice_uint(data, start: int, end: int, maximum: int) -> int:
    if start >= end:
        return -1
    value = 0
    i = start
    while i < end:
        ch = load_i8(data, i) & 255
        if ch < 48 or ch > 57:
            return -1
        value = value * 10 + ch - 48
        if value > maximum:
            return -1
        i = i + 1
    return value


def _prefix_id(data, start: int, end: int) -> int:
    length = end - start
    if length == 1:
        ch = load_i8(data, start) & 255
        if ch == 119:
            return PREFIX_WIDTH
        if ch == 104:
            return PREFIX_HEIGHT
        if ch == 120:
            return PREFIX_X
        if ch == 121:
            return PREFIX_Y
    if length == 2:
        a = load_i8(data, start) & 255
        b = load_i8(data, start + 1) & 255
        if a == 98 and b == 103:
            return PREFIX_BG
    if length == 3:
        a = load_i8(data, start) & 255
        b = load_i8(data, start + 1) & 255
        c = load_i8(data, start + 2) & 255
        if a == 112 and b == 97 and c == 100:
            return PREFIX_PADDING
        if a == 103 and b == 97 and c == 112:
            return PREFIX_GAP
    if length == 4:
        if (
            (load_i8(data, start) & 255) == 116
            and (load_i8(data, start + 1) & 255) == 101
            and (load_i8(data, start + 2) & 255) == 120
            and (load_i8(data, start + 3) & 255) == 116
        ):
            return PREFIX_TEXT
        if (
            (load_i8(data, start) & 255) == 102
            and (load_i8(data, start + 1) & 255) == 111
            and (load_i8(data, start + 2) & 255) == 110
            and (load_i8(data, start + 3) & 255) == 116
        ):
            return PREFIX_FONT
    return 0


def _slice_is_2(data, start: int, end: int, a: int, b: int) -> int:
    if end - start != 2:
        return 0
    if (
        (load_i8(data, start) & 255) == a
        and (load_i8(data, start + 1) & 255) == b
    ):
        return 1
    return 0


def _slice_is_4(
    data, start: int, end: int, a: int, b: int, c: int, d: int
) -> int:
    if end - start != 4:
        return 0
    if (
        (load_i8(data, start) & 255) == a
        and (load_i8(data, start + 1) & 255) == b
        and (load_i8(data, start + 2) & 255) == c
        and (load_i8(data, start + 3) & 255) == d
    ):
        return 1
    return 0


def _slice_is_5(
    data, start: int, end: int, a: int, b: int, c: int, d: int, e: int
) -> int:
    if end - start != 5:
        return 0
    if (
        (load_i8(data, start) & 255) == a
        and (load_i8(data, start + 1) & 255) == b
        and (load_i8(data, start + 2) & 255) == c
        and (load_i8(data, start + 3) & 255) == d
        and (load_i8(data, start + 4) & 255) == e
    ):
        return 1
    return 0


def _slice_is_6(
    data,
    start: int,
    end: int,
    a: int,
    b: int,
    c: int,
    d: int,
    e: int,
    f: int,
) -> int:
    if end - start != 6:
        return 0
    if (
        (load_i8(data, start) & 255) == a
        and (load_i8(data, start + 1) & 255) == b
        and (load_i8(data, start + 2) & 255) == c
        and (load_i8(data, start + 3) & 255) == d
        and (load_i8(data, start + 4) & 255) == e
        and (load_i8(data, start + 5) & 255) == f
    ):
        return 1
    return 0


def _token_id_from_slice(namespace: int, data, start: int, end: int) -> int:
    numeric = _slice_uint(data, start, end, TOKEN_COUNT - 1)
    if numeric >= 0:
        return numeric
    if namespace == NAMESPACE_COLOUR:
        if _slice_is_6(data, start, end, 97, 99, 99, 101, 110, 116) != 0:
            return 0  # accent
        if _slice_is_5(data, start, end, 109, 117, 116, 101, 100) != 0:
            return 1  # muted
        if end - start == 7:
            if (
                (load_i8(data, start) & 255) == 115
                and (load_i8(data, start + 1) & 255) == 117
                and (load_i8(data, start + 2) & 255) == 114
                and (load_i8(data, start + 3) & 255) == 102
                and (load_i8(data, start + 4) & 255) == 97
                and (load_i8(data, start + 5) & 255) == 99
                and (load_i8(data, start + 6) & 255) == 101
            ):
                return 2
        if _slice_is_6(data, start, end, 100, 97, 110, 103, 101, 114) != 0:
            return 3  # danger
    if namespace == NAMESPACE_FONT:
        if _slice_is_4(data, start, end, 98, 111, 100, 121) != 0:
            return 0  # body
        if _slice_is_4(data, start, end, 109, 111, 110, 111) != 0:
            return 1  # mono
        if _slice_is_5(data, start, end, 116, 105, 116, 108, 101) != 0:
            return 2  # title
    if namespace == NAMESPACE_SIZE:
        if _slice_is_2(data, start, end, 120, 115) != 0:
            return 0  # xs
        if _slice_is_2(data, start, end, 115, 109) != 0:
            return 1  # sm
        if _slice_is_2(data, start, end, 109, 100) != 0:
            return 2  # md
        if _slice_is_2(data, start, end, 108, 103) != 0:
            return 3  # lg
        if _slice_is_2(data, start, end, 120, 108) != 0:
            return 4  # xl
    if namespace == NAMESPACE_SPACING:
        if _slice_is_4(data, start, end, 110, 111, 110, 101) != 0:
            return 0  # none
        if _slice_is_2(data, start, end, 112, 120) != 0:
            return 1  # px
    return -1


def _named_generator_index(prefix_id: int) -> int:
    cap = _base("pcc_gui_style_generator_capacity")
    found = -1
    i = 0
    while i < cap:
        record = _generator_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 20) == prefix_id:
            if found >= 0:
                return -2
            found = i
        i = i + 1
    return found


def _modifier_flags(data, start: int, end: int) -> int:
    if start >= end:
        return -1
    arbitrary = 0
    if (load_i8(data, start) & 255) == 91:  # '['
        if end - start < 3 or (load_i8(data, end - 1) & 255) != 93:
            return -1
        arbitrary = 1
        start = start + 1
        end = end - 1
    percent = _slice_uint(data, start, end, 100)
    if percent < 0:
        if _slice_is_4(data, start, end, 104, 97, 108, 102) != 0:
            percent = 50
        elif _slice_is_4(data, start, end, 102, 117, 108, 108) != 0:
            percent = 100
        elif _slice_is_5(data, start, end, 100, 101, 110, 115, 101) != 0:
            percent = 50
        elif _slice_is_6(data, start, end, 111, 112, 97, 113, 117, 101) != 0:
            percent = 100
        else:
            return -1
    kind = OP_MODIFIER_ARBITRARY if arbitrary != 0 else OP_MODIFIER_NAMED
    return kind | (percent << OP_PERCENT_SHIFT)


def _cache_entry_bytes_match(index: int, data, length: int, key_hash: int) -> int:
    entry = _cache_at(index)
    if load_i64(entry, 0) != key_hash or load_i32(entry, 16) != length:
        return 0
    return _bytes_equal(_cache_class_at(index), data, length)


def _cache_entry_current(index: int) -> int:
    entry = _cache_at(index)
    if load_i32(entry, 20) != CACHE_VALID:
        return 0
    if load_i64(entry, 24) != _base("pcc_gui_style_schema_generation_storage"):
        return 0
    namespace_dependencies = _cache_namespace_dependencies_at(index)
    count = load_i32(entry, 44)
    i = 0
    while i < count:
        dep = int_to_ptr(ptr_to_int(namespace_dependencies) + i * CACHE_NAMESPACE_DEP_SIZE)
        if load_i64(dep, 8) != _theme_namespace_generation(load_i32(dep, 0)):
            return 0
        i = i + 1
    token_dependencies = _cache_token_dependencies_at(index)
    count = load_i32(entry, 64)
    i = 0
    while i < count:
        dep = int_to_ptr(ptr_to_int(token_dependencies) + i * CACHE_TOKEN_DEP_SIZE)
        if load_i64(dep, 8) != _theme_token_generation(
            load_i32(dep, 0), load_i32(dep, 4)
        ):
            return 0
        i = i + 1
    return 1


def _cache_find(data, length: int, key_hash: int) -> int:
    cap = _base("pcc_gui_style_cache_capacity")
    i = 0
    while i < cap:
        if _cache_entry_bytes_match(i, data, length, key_hash) != 0:
            return i
        i = i + 1
    return -1


def _cache_replacement() -> int:
    cap = _base("pcc_gui_style_cache_capacity")
    oldest = -1
    oldest_epoch = 0
    i = 0
    while i < cap:
        entry = _cache_at(i)
        if load_i32(entry, 20) != CACHE_VALID:
            return i
        epoch = load_i64(entry, 72)
        if oldest < 0 or epoch < oldest_epoch:
            oldest = i
            oldest_epoch = epoch
        i = i + 1
    return oldest


def _next_cache_epoch() -> int:
    epoch = _base("pcc_gui_style_cache_epoch") + 1
    if epoch <= 0 or epoch >= 0x7FFFFFFFFFFFFFFF:
        return -1
    store_i64(global_addr("pcc_gui_style_cache_epoch"), 0, epoch)
    return epoch


def _record_cache_dependencies(index: int, operation_count: int) -> int:
    operations = _cache_operations_at(index)
    namespace_dependencies = _cache_namespace_dependencies_at(index)
    token_dependencies = _cache_token_dependencies_at(index)
    namespace_count = 0
    token_count = 0
    i = 0
    while i < operation_count:
        operation = int_to_ptr(ptr_to_int(operations) + i * STYLE_OPERATION_SIZE)
        namespace = load_i32(operation, 4)
        token = load_i32(operation, 8)
        seen = 0
        j = 0
        while j < namespace_count:
            dep = int_to_ptr(ptr_to_int(namespace_dependencies) + j * CACHE_NAMESPACE_DEP_SIZE)
            if load_i32(dep, 0) == namespace:
                seen = 1
            j = j + 1
        if seen == 0:
            dep = int_to_ptr(ptr_to_int(namespace_dependencies) + namespace_count * CACHE_NAMESPACE_DEP_SIZE)
            store_i32(dep, 0, namespace)
            store_i32(dep, 4, 0)
            store_i64(dep, 8, _theme_namespace_generation(namespace))
            namespace_count = namespace_count + 1
        seen = 0
        j = 0
        while j < token_count:
            dep = int_to_ptr(ptr_to_int(token_dependencies) + j * CACHE_TOKEN_DEP_SIZE)
            if load_i32(dep, 0) == namespace and load_i32(dep, 4) == token:
                seen = 1
            j = j + 1
        if seen == 0:
            dep = int_to_ptr(ptr_to_int(token_dependencies) + token_count * CACHE_TOKEN_DEP_SIZE)
            store_i32(dep, 0, namespace)
            store_i32(dep, 4, token)
            store_i64(dep, 8, _theme_token_generation(namespace, token))
            token_count = token_count + 1
        i = i + 1
    entry = _cache_at(index)
    store_i32(entry, 44, namespace_count)
    store_i32(entry, 64, token_count)
    return OK


def _compile_candidates(index: int, data, length: int) -> int:
    operations = _cache_operations_at(index)
    operation_count = 0
    pos = 0
    while pos < length:
        while pos < length and (load_i8(data, pos) & 255) == 32:
            pos = pos + 1
        if pos >= length:
            break
        start = pos
        while pos < length and (load_i8(data, pos) & 255) != 32:
            ch = load_i8(data, pos) & 255
            if ch < 33 or ch > 126:
                return ERR_INVALID_CANDIDATE
            pos = pos + 1
        end = pos
        negative = 0
        core_start = start
        if (load_i8(data, core_start) & 255) == 45:
            negative = 1
            core_start = core_start + 1
            if core_start >= end:
                return ERR_INVALID_CANDIDATE
        slash = -1
        dash = -1
        i = core_start
        while i < end:
            ch = load_i8(data, i) & 255
            if ch == 47:
                if slash >= 0:
                    return ERR_INVALID_CANDIDATE
                slash = i
            elif ch == 45 and dash < 0:
                dash = i
            i = i + 1
        core_end = slash if slash >= 0 else end
        if dash <= core_start or dash >= core_end - 1:
            return ERR_INVALID_CANDIDATE
        prefix_id = _prefix_id(data, core_start, dash)
        if prefix_id == 0:
            return ERR_UNKNOWN_CANDIDATE
        generator_index = _named_generator_index(prefix_id)
        if generator_index == -2:
            return ERR_AMBIGUOUS_CANDIDATE
        if generator_index < 0:
            return ERR_UNKNOWN_CANDIDATE
        generator = _generator_at(generator_index)
        namespace = load_i32(generator, 8)
        token = _token_id_from_slice(namespace, data, dash + 1, core_end)
        if token < 0:
            return ERR_UNKNOWN_CANDIDATE
        flags = OP_NEGATIVE if negative != 0 else 0
        if slash >= 0:
            modifier_flags = _modifier_flags(data, slash + 1, end)
            if modifier_flags < 0:
                return ERR_INVALID_CANDIDATE
            flags = flags | modifier_flags
        if _operation_flags_valid(generator, namespace, flags) == 0:
            return ERR_INVALID_CANDIDATE
        if operation_count >= CACHE_OPERATION_CAPACITY:
            return ERR_CAPACITY
        operation = int_to_ptr(
            ptr_to_int(operations) + operation_count * STYLE_OPERATION_SIZE
        )
        status = pcc_gui_style_generate(
            load_i32(generator, 4), token, negative, operation
        )
        if status != OK:
            return status
        store_i32(operation, 12, flags)
        store_i64(
            operation, 16, _operation_expected_value(namespace, token, flags)
        )
        j = 0
        while j < operation_count:
            prior = int_to_ptr(ptr_to_int(operations) + j * STYLE_OPERATION_SIZE)
            if (
                load_i32(prior, 0) == load_i32(operation, 0)
                and load_i32(prior, 8) == token
                and load_i32(prior, 12) == flags
            ):
                return ERR_DUPLICATE_KEY
            j = j + 1
        operation_count = operation_count + 1
    if operation_count == 0:
        return ERR_INVALID_CANDIDATE
    return operation_count


@c_abi_typed_export("pcc_gui_style_compile", "i32", ("ptr", "i64"))
def pcc_gui_style_compile(class_bytes, length: int) -> int:
    if (
        ptr_is_null(class_bytes)
        or length <= 0
        or length >= CACHE_CLASS_BYTES
        or _base("pcc_gui_style_cache_records") == 0
    ):
        return ERR_INVALID_CANDIDATE
    key_hash = _candidate_hash(class_bytes, length)
    index = _cache_find(class_bytes, length, key_hash)
    if index >= 0 and _cache_entry_current(index) != 0:
        entry = _cache_at(index)
        epoch = _next_cache_epoch()
        if epoch < 0:
            return ERR_CAPACITY
        store_i64(entry, 72, epoch)
        store_i64(
            global_addr("pcc_gui_style_cache_hits"),
            0,
            _base("pcc_gui_style_cache_hits") + 1,
        )
        return load_i32(entry, 40)
    if index < 0:
        index = _cache_replacement()
    if index < 0:
        return ERR_CAPACITY
    entry = _cache_at(index)
    store_i32(entry, 20, CACHE_COMPILING)
    store_i64(
        global_addr("pcc_gui_style_parser_invocations_storage"),
        0,
        _base("pcc_gui_style_parser_invocations_storage") + 1,
    )
    store_i64(
        global_addr("pcc_gui_style_cache_misses"),
        0,
        _base("pcc_gui_style_cache_misses") + 1,
    )
    operation_count = _compile_candidates(index, class_bytes, length)
    if operation_count < 0:
        store_i32(entry, 20, 0)
        return operation_count
    _record_cache_dependencies(index, operation_count)
    memcpy(_cache_class_at(index), class_bytes, length)
    store_i8(_cache_class_at(index), length, 0)
    store_i64(entry, 0, key_hash)
    store_i32(entry, 16, length)
    store_i32(entry, 40, operation_count)
    store_i32(entry, 68, 0)
    store_i64(
        entry, 24, _base("pcc_gui_style_schema_generation_storage")
    )
    epoch = _next_cache_epoch()
    if epoch < 0:
        store_i32(entry, 20, 0)
        return ERR_CAPACITY
    store_i64(entry, 72, epoch)
    store_i32(entry, 20, CACHE_VALID)
    return operation_count


def _compiled_cache_index(class_bytes, length: int) -> int:
    return _cache_find(class_bytes, length, _candidate_hash(class_bytes, length))


@c_abi_typed_export(
    "pcc_gui_style_cached_operations", "i32", ("ptr", "i64", "ptr", "i32")
)
def pcc_gui_style_cached_operations(
    class_bytes, length: int, operations_out, capacity: int
) -> int:
    count = pcc_gui_style_compile(class_bytes, length)
    if count < 0:
        return count
    if capacity < count or (count > 0 and ptr_is_null(operations_out)):
        return ERR_CAPACITY
    index = _compiled_cache_index(class_bytes, length)
    if index < 0 or _cache_entry_current(index) == 0:
        return ERR_STALE
    memcpy(
        operations_out,
        _cache_operations_at(index),
        count * STYLE_OPERATION_SIZE,
    )
    return count


def _cached_apply_capacity_ok(
    component_id: int, node_id: int, index: int, count: int
) -> int:
    operations = _cache_operations_at(index)
    missing = 0
    i = 0
    while i < count:
        operation = int_to_ptr(ptr_to_int(operations) + i * STYLE_OPERATION_SIZE)
        namespace = load_i32(operation, 4)
        token = load_i32(operation, 8)
        if _dependency_index(component_id, node_id, namespace, token) < 0:
            seen = 0
            j = 0
            while j < i:
                prior = int_to_ptr(ptr_to_int(operations) + j * STYLE_OPERATION_SIZE)
                if load_i32(prior, 4) == namespace and load_i32(prior, 8) == token:
                    seen = 1
                j = j + 1
            if seen == 0:
                missing = missing + 1
        i = i + 1
    free_slots = 0
    cap = _base("pcc_gui_style_dependency_capacity")
    i = 0
    while i < cap:
        if load_i32(_dependency_at(i), 0) == 0:
            free_slots = free_slots + 1
        i = i + 1
    return 1 if free_slots >= missing else 0


@c_abi_typed_export(
    "pcc_gui_style_apply_class", "i32", ("i64", "i64", "ptr", "i64")
)
def pcc_gui_style_apply_class(
    component_id: int, node_id: int, class_bytes, length: int
) -> int:
    count = pcc_gui_style_compile(class_bytes, length)
    if count < 0:
        return count
    index = _compiled_cache_index(class_bytes, length)
    if index < 0 or _cache_entry_current(index) == 0:
        return ERR_STALE
    component_id = _resolved_component(component_id, node_id)
    if component_id < 0:
        return ERR_OWNERSHIP
    if _cached_apply_capacity_ok(component_id, node_id, index, count) == 0:
        return ERR_CAPACITY
    class_epoch = load_i64(_cache_at(index), 72)
    if class_epoch <= 0:
        return ERR_STALE
    operations = _cache_operations_at(index)
    i = 0
    while i < count:
        operation = int_to_ptr(ptr_to_int(operations) + i * STYLE_OPERATION_SIZE)
        namespace = load_i32(operation, 4)
        token = load_i32(operation, 8)
        prior_index = _dependency_index(
            component_id, node_id, namespace, token
        )
        prior_tag = 1
        if prior_index >= 0:
            prior_tag = load_i64(_dependency_at(prior_index), 48)
        status = pcc_gui_style_apply(component_id, node_id, operation)
        if status != OK:
            return status
        status = _mark_class_dependency(
            component_id,
            node_id,
            namespace,
            token,
            class_epoch,
            prior_index,
            prior_tag,
        )
        if status != OK:
            return status
        i = i + 1
    _retire_old_class_dependencies(component_id, node_id, class_epoch)
    return OK


@c_abi_typed_export("pcc_gui_style_parser_invocations", "i64", ())
def pcc_gui_style_parser_invocations() -> int:
    return _base("pcc_gui_style_parser_invocations_storage")


@c_abi_typed_export("pcc_gui_style_cache_allocation_count", "i64", ())
def pcc_gui_style_cache_allocation_count() -> int:
    return _base("pcc_gui_style_cache_allocations")


@c_abi_typed_export("pcc_gui_style_cache_hit_count", "i64", ())
def pcc_gui_style_cache_hit_count() -> int:
    return _base("pcc_gui_style_cache_hits")


@c_abi_typed_export("pcc_gui_style_cache_miss_count", "i64", ())
def pcc_gui_style_cache_miss_count() -> int:
    return _base("pcc_gui_style_cache_misses")
