"""5-GC common production contract: valueclass pointer-payload updates.

This extends the ValueBox root contract with the next value-model brick named
in the common contract README: a boxed valueclass with pointer-bearing payload
fields must remain usable after backend #4 installs a relocation forwarding
entry, and later payload updates must still be observed through the moved box.

The program compiles once in strict no-libpython self-backend mode, then runs
under ``PCC_GC_BACKEND=0..4``. Backends 0..3 exercise the same mutation/readback
semantics without forcing relocation; backend 4 additionally selects the
ValueBox for relocation, copies it, resolves it through the GC load barrier,
and mutates the pointer fields through the resolved object.
"""
from __future__ import annotations

import os
import subprocess

import pytest


_PROGRAM = (
    "import gc\n"
    "import pcc\n"
    "from typing import Any\n"
    "from pcc.extern import extern, c_int64, c_ptr, c_void\n"
    "from pcc.unsafe import free, malloc, null, ptr_eq, ptr_is_null, store_ptr\n"
    "\n"
    "pcc_gc_backend = extern('pcc_gc_backend', (), c_int64)\n"
    "pcc_gc_load_ptr = extern('pcc_gc_load_ptr', (c_ptr, c_ptr), c_ptr)\n"
    "pcc_gc_store_ptr = extern('pcc_gc_store_ptr', (c_ptr, c_ptr, c_ptr), c_void)\n"
    "pcc_gc_scheduler_root_register = extern('pcc_gc_scheduler_root_register', (c_ptr,), c_void)\n"
    "pcc_gc_scheduler_root_unregister = extern('pcc_gc_scheduler_root_unregister', (c_ptr,), c_void)\n"
    "pcc_gc_object_id = extern('pcc_gc_object_id', (c_ptr,), c_int64)\n"
    "pcc_gc_reset_relocation_set = extern('pcc_gc_reset_relocation_set', (), c_void)\n"
    "pcc_gc_select_relocation_set = extern('pcc_gc_select_relocation_set', (c_int64,), c_int64)\n"
    "pcc_gc_relocation_set_contains = extern('pcc_gc_relocation_set_contains', (c_ptr,), c_int64)\n"
    "pcc_gc_relocate_copy = extern('pcc_gc_relocate_copy', (c_ptr, c_int64), c_ptr)\n"
    "\n"
    "VALUEBOX_BAG_SIZE = 56\n"
    "\n"
    "@pcc.valueclass\n"
    "class Bag:\n"
    "    items: list\n"
    "    label: str\n"
    "    extra: list\n"
    "\n"
    "def ident(x: Any) -> Any:\n"
    "    return x\n"
    "\n"
    "def check_payload(box: Any) -> None:\n"
    "    box.items.append(8)\n"
    "    gc.collect()\n"
    "    box.extra.append('tail')\n"
    "    gc.collect()\n"
    "    print(len(box.items))\n"
    "    print(box.items[3])\n"
    "    print(box.label)\n"
    "    print(len(box.extra))\n"
    "    print(box.extra[1])\n"
    "\n"
    "def force_relocating_backend_readback(box: Any) -> None:\n"
    "    if pcc_gc_backend() != 4:\n"
    "        print('plain')\n"
    "        check_payload(box)\n"
    "        return\n"
    "\n"
    "    slot = malloc(8)\n"
    "    store_ptr(slot, 0, null())\n"
    "    pcc_gc_scheduler_root_register(slot)\n"
    "    pcc_gc_store_ptr(null(), slot, box)\n"
    "\n"
    "    stable_id = pcc_gc_object_id(box)\n"
    "    pcc_gc_reset_relocation_set()\n"
    "    selected = pcc_gc_select_relocation_set(4096)\n"
    "    print('relocated')\n"
    "    print(selected > 0)\n"
    "    print(pcc_gc_relocation_set_contains(box) == 1)\n"
    "\n"
    "    moved = pcc_gc_relocate_copy(box, VALUEBOX_BAG_SIZE)\n"
    "    print(ptr_is_null(moved) == False)\n"
    "    loaded = pcc_gc_load_ptr(null(), slot)\n"
    "    print(ptr_eq(loaded, moved))\n"
    "    print(pcc_gc_object_id(loaded) == stable_id)\n"
    "\n"
    "    pcc_gc_reset_relocation_set()\n"
    "    check_payload(box)\n"
    "    pcc_gc_store_ptr(null(), slot, null())\n"
    "    pcc_gc_scheduler_root_unregister(slot)\n"
    "    free(slot)\n"
    "\n"
    "def main() -> None:\n"
    "    bag = Bag([1, 2, 3], 'bag', ['head'])\n"
    "    boxed = ident(bag)\n"
    "    gc.collect()\n"
    "    force_relocating_backend_readback(boxed)\n"
    "\n"
    "main()\n"
)


@pytest.fixture(scope="module")
def _valueclass_pointer_payload_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_valueclass_pointer_payload")
    src = tmp / "valueclass_pointer_payload.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "valueclass_pointer_payload_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
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
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_valueclass_pointer_payload_updates_after_optional_relocation(
    _valueclass_pointer_payload_exe,
    backend,
):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_valueclass_pointer_payload_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"backend #{backend} rc={run.returncode}: {run.stderr.strip()[:200]}"
    )
    expected = ["plain", "4", "8", "bag", "2", "tail"]
    if backend == "4":
        expected = [
            "relocated",
            "True",
            "True",
            "True",
            "True",
            "True",
            "4",
            "8",
            "bag",
            "2",
            "tail",
        ]
    assert run.stdout.splitlines() == expected, run.stdout
