"""Webview-free GUI app run events and exactly-once shutdown."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
LIFECYCLE = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_app_lifecycle.py"
BRIDGE = REPO / "pcc" / "kernel_ir" / "metal_render_surface.py"
CONTRACT = REPO / "pcc" / "py_runtime" / "gui_declarative_contract_v1.json"


def _compile_run(
    tmp_path: Path, pcc_py_runtime_archive: Path, name: str, source: str
) -> str:
    src = tmp_path / f"{name}.py"
    exe = tmp_path / name
    src.write_text(source, encoding="utf-8")
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(exe)], env=env, text=True, capture_output=True, timeout=30
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout


def test_app_owner_freezes_selected_events_and_real_native_adapter() -> None:
    lifecycle = LIFECYCLE.read_text(encoding="utf-8")
    bridge = BRIDGE.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    modules = " ".join(
        line for line in makefile.splitlines() if "FREESTANDING_PY_MODULES" in line
    )
    assert modules.split().count("pcc_gui_app_lifecycle") == 1
    assert "APP_EVENT_SIZE = 48" in lifecycle
    assert "MAX_EVENT_PAYLOAD = 256" in lifecycle
    for name in (
        "EVENT_READY",
        "EVENT_RESUMED",
        "EVENT_MAIN_EVENTS_CLEARED",
        "EVENT_WINDOW",
        "EVENT_OPENED",
        "EVENT_REOPEN",
        "EVENT_EXIT_REQUESTED",
        "EVENT_EXIT",
    ):
        assert name in lifecycle
    assert "Webview" in lifecycle
    assert "_scheduler_shutdown()" in lifecycle
    assert "_commands_shutdown()" in lifecycle
    assert "_components_shutdown()" in lifecycle
    assert "_events_shutdown(null())" in lifecycle
    assert "_release_native_window()" in lifecycle
    assert lifecycle.index("_scheduler_shutdown()") < lifecycle.index(
        "_commands_shutdown()"
    ) < lifecycle.index("_components_shutdown()") < lifecycle.index(
        "_events_shutdown(null())"
    ) < lifecycle.index("_release_native_window()")
    assert "PccGuiLifecycleDelegate" in bridge
    assert "pcc_gui_metal_lifecycle_install" in bridge
    assert "pcc_gui_metal_lifecycle_probe" in bridge
    assert "applicationShouldHandleReopen" in bridge
    assert "openFiles:" in bridge
    assert "windowDidResize:" in bridge
    assert "applicationShouldTerminate:" in bridge


def test_frozen_app_event_layout_and_transition_set() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    records = {record["name"]: record for record in contract["records"]}
    event = records["PccGuiAppEventV1"]
    assert event["size"] == 48
    assert [(field["name"], field["offset"]) for field in event["fields"]] == [
        ("sequence", 0),
        ("kind", 8),
        ("flags", 12),
        ("window_id", 16),
        ("payload", 24),
        ("payload_length", 32),
        ("exit_code", 40),
        ("status_out", 44),
    ]
    app = contract["state_machines"]["app"]
    assert app["events"] == [
        "Ready",
        "Resumed",
        "MainEventsCleared",
        "WindowEvent",
        "Opened",
        "Reopen",
        "ExitRequested",
        "Exit",
    ]
    assert "WebviewEvent" in " ".join(app["rules"])


_APP_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import cstr, define_global_i64, function_addr, global_addr, int_to_ptr, load_i8, load_i32, load_i64, load_ptr, null, ptr_is_null, stack_alloc, store_i32, store_i64, store_ptr

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
register_handles = extern("pcc_gui_component_register_handle_ops", (c_ptr,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)

scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
enqueue_set = extern("pcc_gui_scheduler_enqueue_set", (c_int64,c_int32,c_int32,c_int64,c_ptr), c_int32)

events_init = extern("pcc_gui_events_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
register_listener_callback = extern("pcc_gui_events_register_listener_callback", (c_int32,c_ptr), c_int32)
register_effect_callback = extern("pcc_gui_events_register_effect_callback", (c_int32,c_ptr), c_int32)
listen = extern("pcc_gui_events_listen", (c_int64,c_int64,c_int32,c_int32,c_int32,c_int64), c_int32)
register_effect = extern("pcc_gui_events_register_effect", (c_int64,c_int32,c_int64,c_int32,c_int64), c_int32)
listener_count = extern("pcc_gui_events_listener_count", (c_int64,), c_int32)

commands_init = extern("pcc_gui_commands_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
command_register = extern("pcc_gui_commands_register", (c_int32,c_ptr,c_int32,c_int32,c_int64,c_int64), c_int32)
command_invoke = extern("pcc_gui_commands_invoke", (c_ptr,), c_int32)
command_completion = extern("pcc_gui_commands_completion", (c_int64,c_ptr), c_int32)
command_pending = extern("pcc_gui_commands_pending_count", (c_int64,), c_int64)

app_init = extern("pcc_gui_app_lifecycle_init", (c_int64,c_ptr,c_int64,c_int64,c_ptr,c_ptr), c_int32)
app_post = extern("pcc_gui_app_lifecycle_post", (c_int32,c_int64,c_ptr,c_int64,c_int32,c_int32), c_int32)
app_startup = extern("pcc_gui_app_lifecycle_post_startup", (), c_int32)
app_drain = extern("pcc_gui_app_lifecycle_drain", (c_int32,c_ptr), c_int32)
app_state = extern("pcc_gui_app_lifecycle_state", (), c_int32)
app_pending = extern("pcc_gui_app_lifecycle_pending", (), c_int64)
terminal_count = extern("pcc_gui_app_lifecycle_terminal_count", (), c_int32)

define_global_i64("app_component", -1)
define_global_i64("app_event_mask", 0)
define_global_i64("app_last_sequence", 0)
define_global_i64("app_exit_requests", 0)
define_global_i64("app_work_drained", 0)
define_global_i64("app_window_release_count", 0)
define_global_i64("app_handle_retain_count", 0)
define_global_i64("app_handle_release_count", 0)
define_global_i64("app_effect_calls", 0)
define_global_i64("app_cleanup_ok", 0)

@c_abi_typed_export("app_render", "i32", ("ptr",))
def app_render(context) -> int:
    store_i32(load_ptr(context, 64), 0, 0)
    return 0

@c_abi_typed_export("app_handle_retain", "i64", ("i64",))
def app_handle_retain(value: int) -> int:
    store_i64(global_addr("app_handle_retain_count"), 0, load_i64(global_addr("app_handle_retain_count"), 0) + 1)
    return value

@c_abi_typed_export("app_handle_release", "i64", ("i64",))
def app_handle_release(value: int) -> int:
    store_i64(global_addr("app_handle_release_count"), 0, load_i64(global_addr("app_handle_release_count"), 0) + 1)
    return 0

@c_abi_typed_export("app_window_release", "i64", ("i64",))
def app_window_release(value: int) -> int:
    if value != 555:
        return -1
    store_i64(global_addr("app_window_release_count"), 0, load_i64(global_addr("app_window_release_count"), 0) + 1)
    return 0

@c_abi_typed_export("app_work_drain", "i32", ("ptr",))
def app_work_drain(unused) -> int:
    store_i64(global_addr("app_work_drained"), 0, 1)
    return 0

@c_abi_typed_export("app_listener", "i32", ("i64", "i64", "ptr"))
def app_listener(listener_id: int, component: int, event) -> int:
    return 0

@c_abi_typed_export("app_effect", "i32", ("i64", "i32", "i64"))
def app_effect(component: int, phase: int, payload: int) -> int:
    store_i64(global_addr("app_effect_calls"), 0, load_i64(global_addr("app_effect_calls"), 0) + 1)
    return 0

@c_abi_typed_export("app_async_command", "i32", ("ptr", "i64"))
def app_async_command(invoke, resolver: int) -> int:
    return 1

@c_abi_typed_export("app_event", "i32", ("ptr",))
def app_event(event) -> int:
    sequence = load_i64(event, 0)
    kind = load_i32(event, 8)
    if sequence <= load_i64(global_addr("app_last_sequence"), 0):
        return -1
    store_i64(global_addr("app_last_sequence"), 0, sequence)
    store_i64(global_addr("app_event_mask"), 0, load_i64(global_addr("app_event_mask"), 0) | (1 << kind))
    if kind == 3 and load_i64(global_addr("app_work_drained"), 0) != 1:
        return -1
    if kind == 4:
        payload = load_ptr(event, 24)
        if ptr_is_null(payload) or load_i64(event, 32) != 32 or load_i64(payload, 8) != 640:
            return -1
    if kind == 5:
        payload = load_ptr(event, 24)
        if ptr_is_null(payload) or load_i8(payload, 0) != 111:
            return -1
    if kind == 7:
        count = load_i64(global_addr("app_exit_requests"), 0)
        store_i64(global_addr("app_exit_requests"), 0, count + 1)
        return 1 if count == 0 else 0
    if kind == 8:
        component = load_i64(global_addr("app_component"), 0)
        completion = stack_alloc(48)
        ok = 1
        if component_valid(component) != 0 or listener_count(component) != 0:
            ok = 0
        if command_pending(-1) != 0 or command_completion(800, completion) != 0:
            ok = 0
        elif load_i32(completion, 8) != 3 or load_i32(completion, 12) != -109:
            ok = 0
        if load_i64(global_addr("app_handle_retain_count"), 0) != 2:
            ok = 0
        if load_i64(global_addr("app_handle_release_count"), 0) != 2:
            ok = 0
        if load_i64(global_addr("app_effect_calls"), 0) < 4:
            ok = 0
        if load_i64(global_addr("app_window_release_count"), 0) != 1:
            ok = 0
        store_i64(global_addr("app_cleanup_ok"), 0, ok)
    return 0

def raw_len(text) -> int:
    n = 0
    while load_i8(text, n) != 0:
        n = n + 1
    return n

def main() -> int:
    if kit_init(4) != 0 or components_init(2, 2, 2) != 0:
        return 1
    if register_handles(function_addr("app_handle_retain"), function_addr("app_handle_release")) != 0:
        return 2
    if register_render(1, function_addr("app_render")) != 0:
        return 3
    root = kit_create(-1)
    state = stack_alloc(24)
    store_i32(state, 0, 2)
    store_i64(state, 8, 700)
    store_i64(state, 16, 0)
    component = mount(-1, root, 1, null(), 0, state, 1)
    if component < 0:
        return 4
    store_i64(global_addr("app_component"), 0, component)
    if scheduler_init(2, 4, 1) != 0 or enqueue_set(component, 0, 0, 701, null()) != 0:
        return 5
    if events_init(4, 4, 4, 8) != 0:
        return 6
    if register_listener_callback(1, function_addr("app_listener")) != 0:
        return 7
    if register_effect_callback(2, function_addr("app_effect")) != 0:
        return 8
    if listen(10, component, 1, 1, 0, 0) != 0:
        return 9
    if register_effect(component, 20, 0, 2, 77) != 0:
        return 10
    if commands_init(4, 2, 2, 2) != 0:
        return 11
    if command_register(10, function_addr("app_async_command"), 0, 0, 0, 0) != 0:
        return 12
    packet = stack_alloc(64)
    error = stack_alloc(24)
    store_i64(packet, 0, 700)
    store_i32(packet, 8, 10)
    store_i32(packet, 12, 1)
    store_i64(packet, 16, component)
    store_ptr(packet, 24, null())
    store_i64(packet, 32, 0)
    store_i64(packet, 40, 0)
    store_i64(packet, 48, 800)
    store_ptr(packet, 56, error)
    if command_invoke(packet) != 1:
        return 13
    if app_init(16, function_addr("app_event"), 99, 555, function_addr("app_window_release"), function_addr("app_work_drain")) != 0:
        return 14
    if app_startup() != 0:
        return 15
    if app_drain(1, error) != 1 or app_state() != 2:
        return 16
    if app_drain(1, error) != 1 or app_state() != 3:
        return 17
    if app_post(1, 0, null(), 0, 0, 0) != 0 or app_drain(1, error) != -103:
        return 18
    if app_post(3, 99, null(), 0, 0, 0) != 0:
        return 19
    native = stack_alloc(32)
    store_i32(native, 0, 1)
    store_i64(native, 8, 640)
    store_i64(native, 16, 480)
    store_i64(native, 24, 0)
    if app_post(4, 99, native, 32, 0, 0) != 0:
        return 20
    store_i64(native, 8, 1)
    opened = cstr("opened.txt")
    if app_post(5, 99, opened, raw_len(opened), 0, 0) != 0:
        return 21
    if app_post(6, 99, null(), 0, 0, 0) != 0 or app_drain(8, error) != 4:
        return 22
    if app_state() != 4 or app_pending() != 0:
        return 23
    if app_post(7, 99, null(), 0, 0, 0) != 0 or app_drain(1, error) != 1:
        return 24
    if app_state() != 4 or load_i64(global_addr("app_exit_requests"), 0) != 1:
        return 25
    if app_post(7, 99, null(), 0, 1, 7) != 0 or app_drain(1, error) != 1:
        return 26
    if app_state() != 7 or terminal_count() != 1 or load_i64(global_addr("app_cleanup_ok"), 0) != 1:
        return 27
    if app_post(6, 99, null(), 0, 0, 0) != -108 or terminal_count() != 1:
        return 28
    mask = load_i64(global_addr("app_event_mask"), 0)
    if (mask & 0x1FE) != 0x1FE:
        return 29
    print("gui-app-lifecycle-ok")
    return 0

main()
'''


def test_ordered_events_cancellation_and_complete_shutdown(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "gui-app-lifecycle-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "gui_app_lifecycle", _APP_PROGRAM
    )
