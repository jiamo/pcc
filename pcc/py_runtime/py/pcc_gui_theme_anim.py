"""pcc-Python owner: GUI styling (theme) + animation.

Theme: one canonical table of 64 value slots split into four explicit
16-token namespaces (colour, font, size, spacing).  Generation metadata is
kept alongside the active table owner so token edits can invalidate exact
dependants without invalidating unrelated tokens.  Animation: a linear tween over a
duration with an eased progress curve; the animation advances by elapsed
milliseconds and yields the interpolated value.

Owned surface:

  pcc_gui_theme_init, pcc_gui_theme_set_color, pcc_gui_theme_get_color,
  pcc_gui_theme_set_i64, pcc_gui_theme_get_i64, pcc_gui_theme_set_token,
  pcc_gui_theme_get_token, pcc_gui_theme_activate,
  pcc_gui_theme_token_generation, pcc_gui_theme_namespace_generation,
  pcc_gui_anim_start, pcc_gui_anim_step, pcc_gui_anim_done,
  pcc_gui_anim_value
"""

from pcc.extern import c_abi_typed_export, c_int32, c_void, extern
from pcc.unsafe import (
    define_global_i64,
    define_global_i64_array,
    load_i32,
    store_i32,
    global_addr,
    int_to_ptr,
    load_i64,
    ptr_is_null,
    store_i64,
    ptr_to_int,
)


THEME_NAMESPACE_COLOUR = 0
THEME_NAMESPACE_FONT = 1
THEME_NAMESPACE_SIZE = 2
THEME_NAMESPACE_SPACING = 3
THEME_NAMESPACE_COUNT = 4
THEME_TOKENS_PER_NAMESPACE = 16
THEME_TOKEN_COUNT = 64

define_global_i64("pcc_gui_theme_active", 0)
define_global_i64_array(
    "pcc_gui_theme_token_generations",
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
)
define_global_i64_array("pcc_gui_theme_namespace_generations", 1, 1, 1, 1)

_style_token_changed = extern(
    "pcc_gui_style_theme_token_changed", (c_int32, c_int32), c_void
)
_style_namespace_changed = extern(
    "pcc_gui_style_theme_namespace_changed", (c_int32,), c_void
)


def _token_id(namespace: int, token: int) -> int:
    if (
        namespace < 0
        or namespace >= THEME_NAMESPACE_COUNT
        or token < 0
        or token >= THEME_TOKENS_PER_NAMESPACE
    ):
        return -1
    return namespace * THEME_TOKENS_PER_NAMESPACE + token


def _bump_token(namespace: int, token: int) -> int:
    key = _token_id(namespace, token)
    if key < 0:
        return -1
    address = global_addr("pcc_gui_theme_token_generations")
    generation = load_i64(address, key * 8) + 1
    if generation <= 0 or generation >= 0x7FFFFFFFFFFFFFFF:
        return -1
    store_i64(address, key * 8, generation)
    _style_token_changed(namespace, token)
    return 0


@c_abi_typed_export("pcc_gui_theme_token_id", "i32", ("i32", "i32"))
def pcc_gui_theme_token_id(namespace: int, token: int) -> int:
    return _token_id(namespace, token)


@c_abi_typed_export("pcc_gui_theme_resolve_prefix", "i32", ("i32",))
def pcc_gui_theme_resolve_prefix(prefix: int) -> int:
    if prefix >= 1 and prefix <= THEME_NAMESPACE_COUNT:
        return prefix - 1
    return -1


@c_abi_typed_export("pcc_gui_theme_token_generation", "i64", ("i32", "i32"))
def pcc_gui_theme_token_generation(namespace: int, token: int) -> int:
    key = _token_id(namespace, token)
    if key < 0:
        return -1
    return load_i64(global_addr("pcc_gui_theme_token_generations"), key * 8)


@c_abi_typed_export("pcc_gui_theme_namespace_generation", "i64", ("i32",))
def pcc_gui_theme_namespace_generation(namespace: int) -> int:
    if namespace < 0 or namespace >= THEME_NAMESPACE_COUNT:
        return -1
    return load_i64(
        global_addr("pcc_gui_theme_namespace_generations"), namespace * 8
    )


@c_abi_typed_export("pcc_gui_theme_bump_namespace", "i32", ("i32",))
def pcc_gui_theme_bump_namespace(namespace: int) -> int:
    if namespace < 0 or namespace >= THEME_NAMESPACE_COUNT:
        return -1
    address = global_addr("pcc_gui_theme_namespace_generations")
    generation = load_i64(address, namespace * 8) + 1
    if generation <= 0 or generation >= 0x7FFFFFFFFFFFFFFF:
        return -1
    store_i64(address, namespace * 8, generation)
    _style_namespace_changed(namespace)
    return 0


@c_abi_typed_export("pcc_gui_theme_init", "i32", ("ptr",))
def pcc_gui_theme_init(theme) -> int:
    if ptr_is_null(theme):
        return -1
    active = 1 if ptr_to_int(theme) == load_i64(
        global_addr("pcc_gui_theme_active"), 0
    ) else 0
    if active != 0:
        generations = global_addr("pcc_gui_theme_token_generations")
        i = 0
        while i < THEME_TOKEN_COUNT:
            if (
                load_i64(theme, i * 8) != 0
                and load_i64(generations, i * 8) >= 0x7FFFFFFFFFFFFFFE
            ):
                return -1
            i = i + 1
    i: int = 0
    while i < THEME_TOKEN_COUNT:
        old = load_i64(theme, i * 8)
        store_i64(theme, i * 8, 0)
        if active != 0 and old != 0:
            _bump_token(i // 16, i % 16)
        i += 1
    return 0


@c_abi_typed_export("pcc_gui_theme_set_color", "i32", ("ptr", "i32", "i64"))
def pcc_gui_theme_set_color(theme, key: int, color: int) -> int:
    if ptr_is_null(theme) or key < 0 or key >= 64:
        return -1
    old = load_i64(theme, key * 8)
    if old == color:
        return 0
    store_i64(theme, key * 8, color)
    if ptr_to_int(theme) == load_i64(global_addr("pcc_gui_theme_active"), 0):
        if _bump_token(key // 16, key % 16) != 0:
            store_i64(theme, key * 8, old)
            return -1
    return 0


@c_abi_typed_export("pcc_gui_theme_get_color", "i64", ("ptr", "i32"))
def pcc_gui_theme_get_color(theme, key: int) -> int:
    if ptr_is_null(theme) or key < 0 or key >= 64:
        return 0
    return load_i64(theme, key * 8)


@c_abi_typed_export("pcc_gui_theme_set_i64", "i32", ("ptr", "i32", "i64"))
def pcc_gui_theme_set_i64(theme, key: int, value: int) -> int:
    return pcc_gui_theme_set_color(theme, key, value)


@c_abi_typed_export("pcc_gui_theme_get_i64", "i64", ("ptr", "i32"))
def pcc_gui_theme_get_i64(theme, key: int) -> int:
    return pcc_gui_theme_get_color(theme, key)


@c_abi_typed_export(
    "pcc_gui_theme_set_token", "i32", ("ptr", "i32", "i32", "i64")
)
def pcc_gui_theme_set_token(
    theme, namespace: int, token: int, value: int
) -> int:
    key = _token_id(namespace, token)
    if key < 0:
        return -1
    return pcc_gui_theme_set_i64(theme, key, value)


@c_abi_typed_export(
    "pcc_gui_theme_get_token", "i64", ("ptr", "i32", "i32")
)
def pcc_gui_theme_get_token(theme, namespace: int, token: int) -> int:
    key = _token_id(namespace, token)
    if key < 0:
        return 0
    return pcc_gui_theme_get_i64(theme, key)


@c_abi_typed_export("pcc_gui_theme_activate", "i32", ("ptr",))
def pcc_gui_theme_activate(theme) -> int:
    if ptr_is_null(theme):
        return -1
    new_address = ptr_to_int(theme)
    old_address = load_i64(global_addr("pcc_gui_theme_active"), 0)
    if old_address == new_address:
        return 0
    old_theme = int_to_ptr(old_address)
    generations = global_addr("pcc_gui_theme_token_generations")
    # Preflight the complete swap so generation exhaustion cannot publish a
    # partially invalidated theme.
    i = 0
    while i < THEME_TOKEN_COUNT:
        changed = 1 if old_address == 0 else 0
        if old_address != 0 and load_i64(theme, i * 8) != load_i64(old_theme, i * 8):
            changed = 1
        if changed != 0 and load_i64(generations, i * 8) >= 0x7FFFFFFFFFFFFFFE:
            return -1
        i = i + 1
    i = 0
    while i < THEME_TOKEN_COUNT:
        changed = 1 if old_address == 0 else 0
        if old_address != 0 and load_i64(theme, i * 8) != load_i64(old_theme, i * 8):
            changed = 1
        if changed != 0:
            _bump_token(i // 16, i % 16)
        i = i + 1
    store_i64(global_addr("pcc_gui_theme_active"), 0, new_address)
    return 0


@c_abi_typed_export("pcc_gui_theme_active_get", "i64", ("i32", "i32"))
def pcc_gui_theme_active_get(namespace: int, token: int) -> int:
    key = _token_id(namespace, token)
    active = load_i64(global_addr("pcc_gui_theme_active"), 0)
    if key < 0 or active == 0:
        return 0
    return load_i64(int_to_ptr(active), key * 8)


# animation descriptor: from@0, to@8, duration_ms@16, elapsed@24,
# running@32 (i32)
@c_abi_typed_export("pcc_gui_anim_start", "i32", ("ptr", "i64", "i64", "i64"))
def pcc_gui_anim_start(anim, from_v: int, to_v: int, duration_ms: int) -> int:
    if ptr_is_null(anim) or duration_ms <= 0:
        return -1
    store_i64(anim, 0, from_v)
    store_i64(anim, 8, to_v)
    store_i64(anim, 16, duration_ms)
    store_i64(anim, 24, 0)
    store_i32(anim, 32, 1)
    return 0


@c_abi_typed_export("pcc_gui_anim_step", "i32", ("ptr", "i64"))
def pcc_gui_anim_step(anim, elapsed_ms: int) -> int:
    if ptr_is_null(anim):
        return -1
    if load_i32(anim, 32) == 0:
        return 0
    cur: int = load_i64(anim, 24) + elapsed_ms
    duration: int = load_i64(anim, 16)
    if cur >= duration:
        store_i64(anim, 24, duration)
        store_i32(anim, 32, 0)
    else:
        store_i64(anim, 24, cur)
    return 0


@c_abi_typed_export("pcc_gui_anim_done", "i32", ("ptr",))
def pcc_gui_anim_done(anim) -> int:
    if ptr_is_null(anim):
        return 1
    return 1 - load_i32(anim, 32)


@c_abi_typed_export("pcc_gui_anim_value", "i64", ("ptr",))
def pcc_gui_anim_value(anim) -> int:
    if ptr_is_null(anim):
        return 0
    from_v: int = load_i64(anim, 0)
    to_v: int = load_i64(anim, 8)
    duration: int = load_i64(anim, 16)
    elapsed: int = load_i64(anim, 24)
    if duration <= 0:
        return to_v
    if elapsed >= duration:
        return to_v
    # linear tween: from + (to-from)*elapsed/duration (truncated)
    return from_v + (to_v - from_v) * elapsed // duration
