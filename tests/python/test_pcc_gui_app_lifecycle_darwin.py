"""Darwin bridge reachability for native app lifecycle payloads."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.kernel_ir.metal_render_surface import write_metal_render_bridge


pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit lifecycle adapter")
def test_native_event_adapter_reachability(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    bridge_source = write_metal_render_bridge(tmp_path)
    bridge = tmp_path / "libpcc_gui_metal_lifecycle.dylib"
    built_bridge = subprocess.run(
        [
            "clang",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
            "-framework",
            "AppKit",
            "-framework",
            "QuartzCore",
            "-dynamiclib",
            str(bridge_source),
            "-o",
            str(bridge),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert built_bridge.returncode == 0, built_bridge.stdout + built_bridge.stderr

    source = tmp_path / "native_lifecycle.py"
    source.write_text(
        r'''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import call_i64_ptr1, call_i64_ptr2, call_i64_ptr_i64_ptr, call_ptr_i64_i64, cstr, define_global_i64, dynamic_library_open, dynamic_library_symbol, function_addr, global_addr, int_to_ptr, load_i8, load_i32, load_i64, load_ptr, null, ptr_is_null, ptr_to_int, store_i64

app_init = extern("pcc_gui_app_lifecycle_init", (c_int64,c_ptr,c_int64,c_int64,c_ptr,c_ptr), c_int32)
app_startup = extern("pcc_gui_app_lifecycle_post_startup", (), c_int32)
app_post = extern("pcc_gui_app_lifecycle_post", (c_int32,c_int64,c_ptr,c_int64,c_int32,c_int32), c_int32)
app_drain = extern("pcc_gui_app_lifecycle_drain", (c_int32,c_ptr), c_int32)
terminal_count = extern("pcc_gui_app_lifecycle_terminal_count", (), c_int32)
native_event = extern("pcc_gui_app_lifecycle_native_event", (c_int32,c_int64,c_ptr,c_int64,c_int32,c_int32), c_int32)

define_global_i64("native_close_fn", 0)
define_global_i64("native_trace", 0)
define_global_i64("native_opened_ok", 0)

@c_abi_typed_export("native_release", "i64", ("i64",))
def native_release(handle: int) -> int:
    return call_i64_ptr1(int_to_ptr(load_i64(global_addr("native_close_fn"), 0)), int_to_ptr(handle))

@c_abi_typed_export("native_lifecycle_event", "i32", ("ptr",))
def native_lifecycle_event(event) -> int:
    kind = load_i32(event, 8)
    store_i64(global_addr("native_trace"), 0, load_i64(global_addr("native_trace"), 0) | (1 << kind))
    if kind == 4:
        payload = load_ptr(event, 24)
        if ptr_is_null(payload) or load_i64(event, 32) != 32:
            return -1
    if kind == 5:
        payload = load_ptr(event, 24)
        if not ptr_is_null(payload) and load_i8(payload, 0) == 47:
            store_i64(global_addr("native_opened_ok"), 0, 1)
        else:
            return -1
    return 0

@c_abi_typed_export("native_lifecycle_sink", "i32", ("i32", "i64", "ptr", "i64", "i32", "i32"))
def native_lifecycle_sink(kind: int, window_id: int, payload, length: int, flags: int, exit_code: int) -> int:
    return native_event(kind, window_id, payload, length, flags, exit_code)

def main() -> int:
    bridge = dynamic_library_open(cstr("__BRIDGE__"))
    if ptr_is_null(bridge):
        return 1
    create = dynamic_library_symbol(bridge, cstr("pcc_gui_metal_window_create"))
    close = dynamic_library_symbol(bridge, cstr("pcc_gui_metal_window_close"))
    install = dynamic_library_symbol(bridge, cstr("pcc_gui_metal_lifecycle_install"))
    probe = dynamic_library_symbol(bridge, cstr("pcc_gui_metal_lifecycle_probe"))
    if ptr_is_null(create) or ptr_is_null(close) or ptr_is_null(install) or ptr_is_null(probe):
        return 2
    window = call_ptr_i64_i64(create, cstr("pcc lifecycle"), 320, 200)
    if ptr_is_null(window):
        return 3
    store_i64(global_addr("native_close_fn"), 0, ptr_to_int(close))
    if app_init(16, function_addr("native_lifecycle_event"), 77, ptr_to_int(window), function_addr("native_release"), null()) != 0:
        return 4
    if app_startup() != 0 or app_drain(2, null()) != 2:
        return 5
    if app_post(3, 77, null(), 0, 0, 0) != 0 or app_drain(1, null()) != 1:
        return 6
    sink = function_addr("native_lifecycle_sink")
    if call_i64_ptr_i64_ptr(install, window, 77, sink) != 0:
        return 7
    if call_i64_ptr2(probe, window, cstr("/tmp/pcc-opened.txt")) != 0:
        return 8
    trace = load_i64(global_addr("native_trace"), 0)
    if (trace & (1 << 4)) == 0 or (trace & (1 << 5)) == 0 or (trace & (1 << 6)) == 0:
        return 9
    if load_i64(global_addr("native_opened_ok"), 0) != 1:
        return 10
    if app_post(7, 77, null(), 0, 1, 0) != 0 or app_drain(1, null()) != 1:
        return 11
    if terminal_count() != 1:
        return 12
    print("PCC_GUI_NATIVE_LIFECYCLE_OK")
    return 0

main()
'''.replace("__BRIDGE__", str(bridge)),
        encoding="utf-8",
    )
    exe = tmp_path / "native_lifecycle"
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
            str(source),
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
        [str(exe)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "PCC_GUI_NATIVE_LIFECYCLE_OK" in ran.stdout
