"""Re-run backend-specific GC test files under explicit GC/backend env.

The underlying test files spawn subprocesses; those subprocesses inherit
``PCC_GC_BACKEND`` from the pytest process. Running plain ``pytest <file>``
without setting that env exercises only the default backend (#0). This
wrapper parametrizes over the GC backends each gate originally covered and
over the Python native backend:

* ``llvm`` is the baseline path.
* ``self`` is the pcc-owned backend path.

Each target runs as a separate subprocess with its own timeout so a slow
backend does not hide the first actionable failure behind one long aggregate
run. Most targets are files; known slow aggregate files are split to node ids.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _node(path: str, name: str) -> str:
    return path + "::" + name


_GENERATIONAL_FILE = "tests/python/test_gc_backend_generational.py"
_GENERATIONAL_TARGETS = (
    _node(_GENERATIONAL_FILE, "test_generational_backend_small_alloc_uses_minor_fast_path"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_heap_pressure_triggers_collection"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_c_runtime_uses_minor_bump_arena"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_uses_minor_bump_arena"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_threaded_minor_blocks"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_class_instances_deallocate_from_minor_arena"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_promotes_remembered_young_child"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_oldifies_copy_for_remembered_child"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_forwarded_minor_source_is_inactive_after_oldify"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy"),
    _node(_GENERATIONAL_FILE, "test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify"),
)

_RELOCATING_FILE = "tests/python/test_gc_backend_relocating.py"
_RELOCATING_TARGETS = (
    _node(_RELOCATING_FILE, "test_colored_relocating_task_and_scheduler_queue_follow_forwarding"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding"),
    _node(_RELOCATING_FILE, "test_colored_relocating_list_copy_owns_item_array"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_list_copy_owns_item_array"),
    _node(_RELOCATING_FILE, "test_colored_relocating_tuple_copy_retains_owned_items"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_tuple_copy_retains_owned_items"),
    _node(_RELOCATING_FILE, "test_colored_relocating_task_copy_retains_state_slots"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_task_copy_retains_state_slots"),
    _node(_RELOCATING_FILE, "test_colored_relocating_set_copy_retains_owned_entries"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_set_copy_retains_owned_entries"),
    _node(_RELOCATING_FILE, "test_colored_relocating_dict_copy_retains_owned_tables"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_dict_copy_retains_owned_tables"),
    _node(_RELOCATING_FILE, "test_colored_relocating_instance_copy_retains_owned_fields"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_instance_copy_retains_owned_fields"),
    _node(_RELOCATING_FILE, "test_colored_relocating_targets_wait_for_phase_reset"),
    _node(_RELOCATING_FILE, "test_pcc_python_colored_relocating_targets_wait_for_phase_reset"),
    _node(_RELOCATING_FILE, "test_colored_relocating_load_barrier_follows_forwarding_entry"),
    _node(_RELOCATING_FILE, "test_colored_relocating_rejects_forwarding_for_pinned_objects"),
    _node(_RELOCATING_FILE, "test_colored_relocating_stable_id_survives_forwarding"),
    _node(_RELOCATING_FILE, "test_colored_relocating_selects_unpinned_relocation_set"),
    _node(_RELOCATING_FILE, "test_colored_relocating_copy_moves_selected_simple_object"),
    _node(_RELOCATING_FILE, "test_colored_relocating_copy_consumes_relocation_entry"),
    _node(_RELOCATING_FILE, "test_colored_relocating_step_copies_selected_simple_object"),
)


_BACKEND_TEST_GROUPS = {
    "2": (
        "tests/python/test_gc_backend23_production.py",
        "tests/python/test_gc_backend_concurrent.py",
        "tests/python/test_gc_concurrent_collection.py",
        "tests/python/test_gc_threading_substrate.py",
    ),
    "3": (
        "tests/python/test_gc_backend23_production.py",
        *_GENERATIONAL_TARGETS,
        "tests/python/test_gc_concurrent_collection.py",
        "tests/python/test_gc_coroutine_roots.py",
    ),
    "4": (
        *_RELOCATING_TARGETS,
        "tests/python/test_gc_backend4_production.py",
        "tests/python/test_gc_abstraction_surface.py",
        "tests/python/test_gc_coroutine_roots.py",
    ),
}


_FRONTEND_BACKENDS = ("llvm", "self")
_SUBPROCESS_TIMEOUT_SECONDS = 240
_SLOW_SUBPROCESS_TIMEOUT_SECONDS = 600


def _timeout_for_target(test_target: str) -> int:
    if test_target == "tests/python/test_gc_backend4_production.py":
        return _SLOW_SUBPROCESS_TIMEOUT_SECONDS
    return _SUBPROCESS_TIMEOUT_SECONDS


def _timeout_output_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _iter_cases():
    for frontend_backend in _FRONTEND_BACKENDS:
        for gc_backend, targets in _BACKEND_TEST_GROUPS.items():
            for test_target in targets:
                yield pytest.param(
                    frontend_backend,
                    gc_backend,
                    test_target,
                    id=(
                        "frontend="
                        + frontend_backend
                        + "-gc="
                        + gc_backend
                        + "-"
                        + test_target.rsplit("/", 1)[-1].replace("::", "-")
                    ),
                )


def _run_file_under_backends(
    frontend_backend: str,
    gc_backend: str,
    test_target: str,
) -> None:
    env = {
        **os.environ,
        "PCC_BACKEND": frontend_backend,
        "PCC_GC_BACKEND": gc_backend,
    }
    cmd = [sys.executable, "-m", "pytest", "-q", "-n0", test_target]
    timeout_seconds = _timeout_for_target(test_target)
    try:
        proc = subprocess.run(
            env=env,
            args=cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_output_text(exc.stdout)
        stderr = _timeout_output_text(exc.stderr)
        raise AssertionError(
            "GC subset timed out after "
            + str(timeout_seconds)
            + "s:\n"
            + f"frontend backend: {frontend_backend}\n"
            + f"PCC_GC_BACKEND={gc_backend}\n"
            + f"cmd: {' '.join(cmd)}\n"
            + f"stdout:\n{stdout}\n"
            + f"stderr:\n{stderr}"
        ) from exc
    assert proc.returncode == 0, (
        f"frontend backend {frontend_backend!r} with "
        f"PCC_GC_BACKEND={gc_backend} failed (exit {proc.returncode}):\n"
        f"cmd: {' '.join(cmd)}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


@pytest.mark.parametrize(
    ("frontend_backend", "gc_backend", "test_target"),
    tuple(_iter_cases()),
)
def test_gc_backend_subset_under_frontend_backend(
    frontend_backend: str,
    gc_backend: str,
    test_target: str,
) -> None:
    _run_file_under_backends(frontend_backend, gc_backend, test_target)
