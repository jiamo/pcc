"""Typed managed state and exactly-once in-process GUI command resolution."""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
COMMANDS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_commands.py"
BINDING = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_binding.py"
COMPONENTS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_components.py"
CONTRACT = REPO / "pcc" / "py_runtime" / "gui_declarative_contract_v1.json"


def test_gui_runtime_unsafe_static_intrinsics_use_literal_operands() -> None:
    runtime_dir = REPO / "pcc" / "py_runtime" / "py"
    offenders = []
    for path in sorted(runtime_dir.glob("pcc_gui*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "global_addr":
                valid = (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                )
            elif node.func.id == "stack_alloc":
                valid = (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, int)
                )
            else:
                continue
            if not valid:
                offenders.append(f"{path.name}:{node.lineno}:{node.func.id}")
    assert offenders == [], (
        "pcc.unsafe global_addr/stack_alloc operands are compile-time values; "
        "use literals at each call site: " + ", ".join(offenders)
    )


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


def test_command_owner_freezes_one_table_and_callback_abi() -> None:
    commands = COMMANDS.read_text(encoding="utf-8")
    binding = BINDING.read_text(encoding="utf-8")
    components = COMPONENTS.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    unsafe = (REPO / "pcc" / "unsafe" / "__init__.py").read_text(
        encoding="utf-8"
    )
    infer = (REPO / "pcc" / "py_frontend" / "type_infer.py").read_text(
        encoding="utf-8"
    )
    lowering = (
        REPO / "pcc" / "py_frontend" / "codegen" / "unsafe_lowering.py"
    ).read_text(encoding="utf-8")
    modules = makefile.split("FREESTANDING_PY_MODULES =", 1)[1].splitlines()[0]
    assert modules.split().count("pcc_gui_commands") == 1
    assert modules.split().count("pcc_gui_binding") == 1
    assert "define_global" not in binding
    assert "pcc_gui_managed_state_set" in binding
    assert "pcc_gui_commands_register_legacy" in binding
    assert "INVOKE_SIZE = 64" in commands
    assert "COMPLETION_SIZE = 48" in commands
    assert "MAX_PAYLOAD = 256" in commands
    assert '"pcc_gui_commands_resolve_result"' in commands
    assert '"pcc_gui_commands_resolve_error"' in commands
    assert '"pcc_gui_commands_target_teardown"' in commands
    assert "call_i32_ptr_i64" in unsafe
    assert '"call_i32_ptr_i64": TYPE_INT' in infer
    assert 'if intrinsic == "call_i32_ptr_i64"' in lowering
    assert "ir.FunctionType(_I32, [_CSTR, _I64])" in lowering
    assert '"pcc_gui_commands_target_teardown"' in components
    assert "_commands_target_teardown(component_id)" in components


def test_frozen_contract_matches_command_record_layouts() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    records = {record["name"]: record for record in contract["records"]}
    invoke = records["PccGuiInvokeV1"]
    completion = records["PccGuiCompletionV1"]
    assert invoke["size"] == 64
    assert [(field["name"], field["offset"]) for field in invoke["fields"]] == [
        ("request_id", 0),
        ("command_id", 8),
        ("flags", 12),
        ("target_id", 16),
        ("payload", 24),
        ("payload_length", 32),
        ("policy_context", 40),
        ("resolver_id", 48),
        ("error_out", 56),
    ]
    assert completion["size"] == 48
    assert [(field["name"], field["offset"]) for field in completion["fields"]] == [
        ("request_id", 0),
        ("kind", 8),
        ("status", 12),
        ("payload", 16),
        ("payload_length", 24),
        ("message", 32),
        ("flags", 40),
    ]


_COMMAND_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import define_global_i64, function_addr, global_addr, int_to_ptr, load_i32, load_i64, load_ptr, null, ptr_is_null, stack_alloc, store_i32, store_i64, store_ptr

commands_init = extern("pcc_gui_commands_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
set_handle_ownership = extern("pcc_gui_managed_state_set_handle_ownership", (c_ptr,c_ptr), c_int32)
state_set = extern("pcc_gui_managed_state_set", (c_int64,c_int64,c_int32,c_int64,c_int64), c_int32)
state_get = extern("pcc_gui_managed_state_get", (c_int64,c_int64,c_ptr), c_int32)
binding_add = extern("pcc_gui_managed_binding_add", (c_int64,c_int64,c_int64,c_int64), c_int32)
command_register = extern("pcc_gui_commands_register", (c_int32,c_ptr,c_int32,c_int32,c_int64,c_int64), c_int32)
command_invoke = extern("pcc_gui_commands_invoke", (c_ptr,), c_int32)
resolve_result = extern("pcc_gui_commands_resolve_result", (c_int64,c_ptr,c_int64), c_int32)
resolve_error = extern("pcc_gui_commands_resolve_error", (c_int64,c_int32,c_int64), c_int32)
request_payload = extern("pcc_gui_commands_request_payload", (c_int64,c_ptr), c_int32)
completion = extern("pcc_gui_commands_completion", (c_int64,c_ptr), c_int32)
release_completion = extern("pcc_gui_commands_release_completion", (c_int64,), c_int32)
pending_count = extern("pcc_gui_commands_pending_count", (c_int64,), c_int64)
target_teardown = extern("pcc_gui_commands_target_teardown", (c_int64,), c_int32)
cancel_all = extern("pcc_gui_commands_cancel_all", (), c_int64)

legacy_set_property = extern("pcc_gui_binding_set_property", (c_ptr,c_int32,c_int64), c_int32)
legacy_get_property = extern("pcc_gui_binding_get_property", (c_ptr,c_int32), c_int64)
legacy_set_command = extern("pcc_gui_binding_set_command", (c_ptr,c_int32,c_ptr), c_int32)
legacy_has_command = extern("pcc_gui_binding_has_command", (c_ptr,c_int32), c_int32)
legacy_invoke_command = extern("pcc_gui_binding_invoke_command", (c_ptr,c_int32,c_int64), c_int32)

define_global_i64("command_retain_count", 0)
define_global_i64("command_release_count", 0)

@c_abi_typed_export("command_handle_retain", "i64", ("i64",))
def command_handle_retain(value: int) -> int:
    store_i64(global_addr("command_retain_count"), 0, load_i64(global_addr("command_retain_count"), 0) + 1)
    return value + 1000

@c_abi_typed_export("command_handle_release", "i64", ("i64",))
def command_handle_release(value: int) -> int:
    store_i64(global_addr("command_release_count"), 0, load_i64(global_addr("command_release_count"), 0) + 1)
    return 0

@c_abi_typed_export("command_sync", "i32", ("ptr", "i64"))
def command_sync(invoke, resolver: int) -> int:
    if load_i64(invoke, 48) != resolver or load_i32(invoke, 8) != 1:
        return -1
    payload = load_ptr(invoke, 24)
    if ptr_is_null(payload) or load_i64(invoke, 32) != 8:
        return -1
    result = stack_alloc(8)
    store_i64(result, 0, load_i64(payload, 0) + 1)
    return resolve_result(resolver, result, 8)

@c_abi_typed_export("command_error", "i32", ("ptr", "i64"))
def command_error(invoke, resolver: int) -> int:
    return resolve_error(resolver, -116, load_i32(invoke, 8))

@c_abi_typed_export("command_async", "i32", ("ptr", "i64"))
def command_async(invoke, resolver: int) -> int:
    copied = stack_alloc(16)
    if request_payload(resolver, copied) != 0:
        return -1
    if load_i64(copied, 8) != load_i64(invoke, 32):
        return -1
    if load_i64(invoke, 32) > 0 and load_i64(load_ptr(copied, 0), 0) != load_i64(load_ptr(invoke, 24), 0):
        return -1
    return 1

@c_abi_typed_export("command_legacy", "i64", ("i64",))
def command_legacy(value: int) -> int:
    return value + 5

def fill_invoke(packet, request: int, command: int, flags: int, target: int, payload, length: int, context: int, resolver: int, error) -> None:
    store_i64(packet, 0, request)
    store_i32(packet, 8, command)
    store_i32(packet, 12, flags)
    store_i64(packet, 16, target)
    store_ptr(packet, 24, payload)
    store_i64(packet, 32, length)
    store_i64(packet, 40, context)
    store_i64(packet, 48, resolver)
    store_ptr(packet, 56, error)

def main() -> int:
    if target_teardown(77) != 0:
        return 1
    if commands_init(16, 8, 16, 4) != 0 or commands_init(1, 1, 1, 1) != -103:
        return 2
    if set_handle_ownership(function_addr("command_handle_retain"), function_addr("command_handle_release")) != 0:
        return 3
    state = stack_alloc(48)
    if state_set(10, 1, 1, 42, 7) != 0 or state_get(10, 1, state) != 0:
        return 4
    if load_i32(state, 0) != 1 or load_i64(state, 24) != 42 or load_i64(state, 32) != 7:
        return 5
    if binding_add(10, 1, 20, 2) != 0 or state_set(10, 1, 1, 77, 8) != 0:
        return 6
    if state_get(20, 2, state) != 0 or load_i64(state, 24) != 77 or load_i64(state, 32) != 8:
        return 7
    if state_set(10, 1, 2, 90, 0) != -105:
        return 8
    if state_set(30, 3, 2, 900, 0) != 0 or state_get(30, 3, state) != 0:
        return 9
    if load_i64(state, 24) != 1900 or load_i64(global_addr("command_retain_count"), 0) != 1:
        return 10
    if state_set(30, 3, 2, 901, 0) != 0:
        return 11
    if load_i64(global_addr("command_retain_count"), 0) != 2 or load_i64(global_addr("command_release_count"), 0) != 1:
        return 12
    if target_teardown(30) != 0 or load_i64(global_addr("command_release_count"), 0) != 2:
        return 13

    if command_register(1, function_addr("command_sync"), 2, 1, 10, 7) != 0:
        return 14
    if command_register(2, function_addr("command_error"), 0, 0, 0, 0) != 0:
        return 15
    if command_register(3, function_addr("command_async"), 1, 1, 20, 0) != 0:
        return 16
    if command_register(4, function_addr("command_async"), 1, 1, 40, 0) != 0:
        return 17
    if command_register(1, function_addr("command_sync"), 2, 1, 10, 7) != -102:
        return 18

    invoke = stack_alloc(64)
    error = stack_alloc(24)
    value = stack_alloc(8)
    out = stack_alloc(48)
    store_i64(value, 0, 41)
    fill_invoke(invoke, 1, 99, 0, 10, null(), 0, 0, 99, error)
    if command_invoke(invoke) != -110 or load_i32(error, 0) != -110:
        return 19
    fill_invoke(invoke, 2, 1, 0, 11, value, 8, 7, 100, error)
    if command_invoke(invoke) != -111:
        return 20
    fill_invoke(invoke, 3, 1, 0, 10, value, 8, 8, 101, error)
    if command_invoke(invoke) != -111:
        return 21
    fill_invoke(invoke, 4, 1, 0, 10, null(), 8, 7, 102, error)
    if command_invoke(invoke) != -112:
        return 22

    fill_invoke(invoke, 10, 1, 0, 10, value, 8, 7, 110, error)
    if command_invoke(invoke) != 0 or completion(110, out) != 0:
        return 23
    if load_i64(out, 0) != 10 or load_i32(out, 8) != 1 or load_i32(out, 12) != 0:
        return 24
    if load_i64(out, 24) != 8 or load_i64(load_ptr(out, 16), 0) != 42:
        return 25
    if resolve_result(110, value, 8) != -107 or release_completion(110) != 0:
        return 26

    fill_invoke(invoke, 11, 2, 0, 99, null(), 0, 0, 111, error)
    if command_invoke(invoke) != 0 or completion(111, out) != 0:
        return 27
    if load_i32(out, 8) != 2 or load_i32(out, 12) != -116 or ptr_is_null(load_ptr(out, 32)):
        return 28
    if release_completion(111) != 0:
        return 29

    store_i64(value, 0, 0x11223344)
    fill_invoke(invoke, 12, 3, 1, 20, value, 8, 0, 112, error)
    if command_invoke(invoke) != 1 or pending_count(20) != 1:
        return 30
    store_i64(value, 0, 0x55667788)
    copied = stack_alloc(16)
    if request_payload(112, copied) != 0 or load_i64(load_ptr(copied, 0), 0) != 0x11223344:
        return 46
    fill_invoke(invoke, 12, 3, 1, 20, value, 8, 0, 113, error)
    if command_invoke(invoke) != -102:
        return 31
    if resolve_result(112, value, 8) != 0 or resolve_result(112, value, 8) != -107:
        return 32
    if completion(112, out) != 0 or load_i64(load_ptr(out, 16), 0) != 0x55667788:
        return 33
    if release_completion(112) != 0:
        return 34

    store_i64(value, 0, 40)
    fill_invoke(invoke, 13, 4, 1, 40, value, 8, 0, 114, error)
    if command_invoke(invoke) != 1 or target_teardown(40) != 1:
        return 35
    if pending_count(40) != 0 or completion(114, out) != 0:
        return 36
    if load_i32(out, 8) != 3 or load_i32(out, 12) != -109:
        return 37
    if resolve_result(114, value, 8) != -108 or release_completion(114) != 0:
        return 38
    fill_invoke(invoke, 14, 4, 1, 40, value, 8, 0, 115, error)
    if command_invoke(invoke) != -110:
        return 39

    i = 0
    while i < 4:
        fill_invoke(invoke, 500 + i, 3, 1, 20, value, 8, 0, 600 + i, error)
        if command_invoke(invoke) != 1:
            return 40
        i = i + 1
    fill_invoke(invoke, 700, 3, 1, 20, value, 8, 0, 800, error)
    if command_invoke(invoke) != -101 or cancel_all() != 4:
        return 41
    i = 0
    while i < 4:
        if completion(600 + i, out) != 0 or release_completion(600 + i) != 0:
            return 42
        i = i + 1

    owner = int_to_ptr(0x1234)
    if legacy_set_property(owner, 5, 33) != 0 or legacy_get_property(owner, 5) != 33:
        return 43
    if legacy_set_command(owner, 6, function_addr("command_legacy")) != 0:
        return 44
    if legacy_has_command(owner, 6) != 1 or legacy_invoke_command(owner, 6, 12) != 17:
        return 45
    print("gui-command-state-ok")
    return 0

main()
'''


def test_typed_state_sync_async_error_capacity_and_teardown(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "gui-command-state-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "gui_command_state", _COMMAND_PROGRAM
    )
