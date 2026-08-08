"""Declarative PCC native GUI shell for the Python Harness port."""

from pcc.extern import (
    c_abi_typed_export,
    c_int32,
    c_int64,
    c_ptr,
    c_void,
    extern,
)
from pcc.unsafe import (
    calloc,
    cstr,
    free,
    function_addr,
    load_i32,
    load_i64,
    null,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)

import gui_bridge as native
from gui_model import HarnessGuiState


getenv = extern("getenv", (c_ptr,), c_ptr)
fflush = extern("fflush", (c_ptr,), c_int32)

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_destroy = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)
kit_rect = extern(
    "pcc_kit_rect",
    (c_int64, c_int64, c_int64, c_int64, c_int64, c_int32),
    c_void,
)
kit_text = extern(
    "pcc_kit_text",
    (c_int64, c_int64, c_int64, c_ptr, c_int64, c_int64, c_int32),
    c_void,
)
kit_render = extern(
    "pcc_kit_render", (c_int64, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_void
)

commands_init = extern(
    "pcc_gui_commands_init", (c_int64, c_int64, c_int64, c_int64), c_int32
)
command_register = extern(
    "pcc_gui_commands_register",
    (c_int32, c_ptr, c_int32, c_int32, c_int64, c_int64),
    c_int32,
)
command_invoke = extern("pcc_gui_commands_invoke", (c_ptr,), c_int32)
resolve_result = extern(
    "pcc_gui_commands_resolve_result", (c_int64, c_ptr, c_int64), c_int32
)
command_completion = extern(
    "pcc_gui_commands_completion", (c_int64, c_ptr), c_int32
)
release_completion = extern(
    "pcc_gui_commands_release_completion", (c_int64,), c_int32
)
commands_shutdown = extern("pcc_gui_commands_shutdown", (), c_int64)


WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
SIDEBAR_WIDTH = 280

BG = 0xFFFFFFFF
SIDEBAR = 0xFFF9FAFB
INK = 0xFF0F1115
SECONDARY = 0xFF61666B
CAPTION = 0xFFADB2B8
BORDER = 0xFFE6E8EB
BLUE = 0xFF4176E6
BLUE_PALE = 0xFFE4EDFD
INPUT = 0xFFFFFFFF
USER_BUBBLE = 0xFFEDF3FE


def _rect(parent: int, x: int, y: int, width: int, height: int, color: int) -> int:
    node = kit_create(parent)
    kit_rect(node, x, y, width, height, color)
    return node


def _text(parent: int, x: int, y: int, value, length: int, size: int, color: int) -> int:
    node = kit_create(parent)
    kit_text(node, x, y, value, length, size, color)
    return node


def _build_sidebar(root: int) -> None:
    sidebar = _rect(root, 0, 0, SIDEBAR_WIDTH, WINDOW_HEIGHT, SIDEBAR)
    _rect(root, SIDEBAR_WIDTH - 1, 0, 1, WINDOW_HEIGHT, BORDER)
    _text(sidebar, 20, 24, cstr("deepseek"), 8, 20, INK)
    _text(sidebar, 238, 27, cstr("<<"), 2, 14, SECONDARY)
    _rect(sidebar, 14, 74, 252, 38, BG)
    _text(sidebar, 30, 84, cstr("+  New Session"), 14, 14, INK)
    _text(sidebar, 18, 142, cstr("Workspaces"), 10, 12, CAPTION)
    _rect(sidebar, 14, 168, 252, 34, 0xFFF1F3F5)
    _text(sidebar, 28, 177, cstr("pcc"), 3, 14, INK)
    _text(sidebar, 18, 222, cstr("Sessions"), 8, 12, CAPTION)
    _rect(sidebar, 14, 248, 252, 42, BLUE_PALE)
    _text(sidebar, 28, 259, cstr("PCC Harness"), 11, 14, INK)
    _text(sidebar, 28, 740, cstr("Settings"), 8, 14, SECONDARY)
    _text(sidebar, 196, 740, cstr("Ready"), 5, 12, CAPTION)


def _build_composer(root: int, active: int) -> None:
    x = 390
    y = 666 if active != 0 else 388
    width = 780
    _rect(root, x, y, width, 126, 0xFFE6E8EB)
    _rect(root, x + 1, y + 1, width - 2, 124, INPUT)
    if active != 0:
        _text(root, x + 18, y + 18, cstr("Ask a follow-up"), 15, 16, CAPTION)
    else:
        _text(
            root,
            x + 18,
            y + 18,
            cstr("What can I help you build?"),
            26,
            16,
            CAPTION,
        )
    _text(root, x + 18, y + 91, cstr("+"), 1, 18, SECONDARY)
    _text(root, x + 52, y + 92, cstr("Read-only"), 9, 13, SECONDARY)
    _text(root, x + 604, y + 92, cstr("DeepSeek"), 8, 13, SECONDARY)
    _rect(root, x + 728, y + 78, 36, 36, BLUE)
    _text(root, x + 740, y + 86, cstr("^"), 1, 18, BG)


def _build_hero(root: int) -> None:
    _text(root, 620, 299, cstr("<>"), 2, 26, INK)
    _text(root, 660, 295, cstr("DeepSeek Harness"), 16, 26, INK)
    _rect(root, 870, 296, 68, 22, BLUE_PALE)
    _text(root, 880, 299, cstr("Preview"), 7, 12, 0xFF0E3074)
    _text(root, 398, 352, cstr("Choose workspace  v"), 19, 13, INK)
    _build_composer(root, 0)


def _build_active(root: int) -> None:
    _text(root, 300, 22, cstr("PCC Harness"), 11, 14, INK)
    _text(root, 308, 62, cstr("Chat"), 4, 13, BLUE)
    _text(root, 378, 62, cstr("Trajectory"), 10, 13, SECONDARY)
    _rect(root, 300, 84, 952, 1, BORDER)
    _rect(root, 720, 146, 420, 42, USER_BUBBLE)
    _text(root, 742, 158, cstr("hello from pcc gui"), 18, 15, INK)
    _text(root, 410, 232, cstr("DeepSeek"), 8, 13, SECONDARY)
    _text(
        root,
        410,
        260,
        cstr("PCC harness is running. You said: hello from pcc gui"),
        52,
        15,
        INK,
    )
    _text(root, 410, 292, cstr("8 logged events  |  completed"), 29, 12, CAPTION)
    _build_composer(root, 1)


def build_scene(state: HarnessGuiState) -> int:
    if kit_init(128) != 0:
        return -1
    root = kit_create(-1)
    if root < 0:
        return -1
    kit_rect(root, 0, 0, WINDOW_WIDTH, WINDOW_HEIGHT, BG)
    _build_sidebar(root)
    if state.phase == "active":
        _build_active(root)
    else:
        _build_hero(root)
    return root


@c_abi_typed_export("harness_gui_submit_command", "i32", ("ptr", "i64"))
def harness_gui_submit_command(invoke, resolver: int) -> int:
    return resolve_result(resolver, null(), 0)


def _init_commands() -> int:
    if commands_init(8, 8, 8, 8) != 0:
        return -1
    return command_register(
        1, function_addr("harness_gui_submit_command"), 0, 0, 0, 0
    )


def _invoke_submit(request_id: int) -> int:
    packet = calloc(1, 64)
    error = calloc(1, 24)
    completion = calloc(1, 48)
    store_i64(packet, 0, request_id)
    store_i32(packet, 8, 1)
    store_i32(packet, 12, 0)
    store_i64(packet, 16, 1)
    store_ptr(packet, 24, null())
    store_i64(packet, 32, 0)
    store_i64(packet, 40, 0)
    store_i64(packet, 48, request_id)
    store_ptr(packet, 56, error)
    invoked = command_invoke(packet)
    if invoked != 0:
        return _release_submit_buffers(packet, error, completion, invoked)
    completed = command_completion(request_id, completion)
    if completed != 0:
        return _release_submit_buffers(packet, error, completion, completed)
    if load_i32(completion, 8) != 1:
        return _release_submit_buffers(
            packet, error, completion, load_i32(completion, 8) - 1000
        )
    released = release_completion(request_id)
    return _release_submit_buffers(packet, error, completion, released)


def _release_submit_buffers(packet, error, completion, status: int) -> int:
    free(completion)
    free(error)
    free(packet)
    return status


def _render(root: int, rects, colors, texts, rect_count, text_count) -> None:
    store_i64(rect_count, 0, 0)
    store_i64(text_count, 0, 0)
    kit_render(root, rects, colors, rect_count, texts, text_count)
    native.render_scene(
        rects,
        colors,
        load_i64(rect_count, 0),
        texts,
        load_i64(text_count, 0),
    )


def gui_self_check() -> int:
    state = HarnessGuiState()
    if state.visible_regions() != [
        "sidebar",
        "session-navigation",
        "trajectory",
        "composer",
        "status",
        "settings",
    ]:
        return 1
    command_status = _init_commands()
    if command_status != 0:
        print("HARNESS_GUI_COMMAND_INIT_FAILED", command_status)
        return 2
    command_status = _invoke_submit(1)
    if command_status != 0:
        print("HARNESS_GUI_COMMAND_INVOKE_FAILED", command_status)
        return 2
    response = state.submit_sample()
    if response != "PCC harness is running. You said: hello from pcc gui":
        return 3
    if state.agent.session.count() != 8:
        return 4
    root = build_scene(state)
    if root < 0:
        return 5
    rects = calloc(256, 32)
    colors = calloc(256, 4)
    texts = calloc(256, 48)
    rect_count = stack_alloc(8)
    text_count = stack_alloc(8)
    store_i64(rect_count, 0, 0)
    store_i64(text_count, 0, 0)
    kit_render(root, rects, colors, rect_count, texts, text_count)
    if load_i64(rect_count, 0) < 9 or load_i64(text_count, 0) < 15:
        print(
            "HARNESS_GUI_RENDER_COUNTS",
            load_i64(rect_count, 0),
            load_i64(text_count, 0),
        )
        fflush(null())
        return 6
    if kit_destroy(root) <= 0:
        return 7
    if commands_shutdown() != 0:
        return 8
    print("HARNESS_GUI_SELF_CHECK_OK")
    return 0


def run_gui() -> int:
    bridge = getenv(cstr("PCC_HARNESS_GUI_BRIDGE"))
    if ptr_is_null(bridge):
        print("PCC_HARNESS_GUI_BRIDGE is not set")
        return 20
    if native.init(cstr("DeepSeek Harness"), WINDOW_WIDTH, WINDOW_HEIGHT, bridge) != 0:
        print("failed to initialize PCC native GUI")
        return 21
    if _init_commands() != 0:
        native.close()
        return 22

    state = HarnessGuiState()
    root = build_scene(state)
    rects = calloc(256, 32)
    colors = calloc(256, 4)
    texts = calloc(256, 48)
    rect_count = stack_alloc(8)
    text_count = stack_alloc(8)
    click_x = stack_alloc(8)
    click_y = stack_alloc(8)
    request_id = 1

    autosubmit = getenv(cstr("PCC_HARNESS_GUI_AUTOSUBMIT"))
    if not ptr_is_null(autosubmit):
        if _invoke_submit(request_id) == 0:
            state.submit_sample()
            request_id += 1
            kit_destroy(root)
            root = build_scene(state)

    _render(root, rects, colors, texts, rect_count, text_count)
    attempts = 0
    while native.render_ack() != 0 and attempts < 60:
        native.running()
        native.sleep(16)
        _render(root, rects, colors, texts, rect_count, text_count)
        attempts += 1

    capture_path = getenv(cstr("PCC_HARNESS_GUI_CAPTURE"))
    if not ptr_is_null(capture_path):
        result = native.capture(capture_path)
        print("HARNESS_GUI_CAPTURE", result)
        native.close()
        kit_destroy(root)
        commands_shutdown()
        return result

    while native.running() != 0:
        if native.poll_click(click_x, click_y) != 0:
            x = load_i64(click_x, 0)
            y = load_i64(click_y, 0)
            if x >= 14 and x < 266 and y >= 74 and y < 112:
                state.new_session()
                kit_destroy(root)
                root = build_scene(state)
            elif x >= 1118 and x < 1154 and (
                (y >= 466 and y < 502) or (y >= 744 and y < 780)
            ):
                if _invoke_submit(request_id) == 0:
                    state.submit_sample()
                    request_id += 1
                    kit_destroy(root)
                    root = build_scene(state)
            _render(root, rects, colors, texts, rect_count, text_count)
        native.sleep(16)

    kit_destroy(root)
    commands_shutdown()
    native.close()
    return 0
