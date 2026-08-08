"""Self-backend native-object cache planning and publication."""

from __future__ import annotations

import os
import subprocess


OBJECT_CACHE_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE"
OBJECT_CACHE_DIR_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_DIR"
OBJECT_CACHE_IDENTITY_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY"
OBJECT_CACHE_VERSION = "pcc.self-backend-object-cache.v2"


def enabled() -> bool:
    value = str(os.environ.get(OBJECT_CACHE_ENV, "") or "")
    if value.strip().lower() in (
        "0",
        "false",
        "no",
        "off",
        "disable",
        "disabled",
    ):
        return False
    identity = str(os.environ.get(OBJECT_CACHE_IDENTITY_ENV, "") or "").strip()
    return bool(identity)


def cache_dir() -> str:
    configured = str(os.environ.get(OBJECT_CACHE_DIR_ENV, "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(
        os.path.expanduser("~"),
        ".cache",
        "pcc",
        "self-backend-object-cache",
    )


def path_allowed(cache_path: str) -> bool:
    if not cache_path or not enabled():
        return False
    cache_root = os.path.abspath(cache_dir())
    candidate = os.path.abspath(cache_path)
    return candidate.startswith(cache_root + os.sep)


def maintain(
    protected_paths: list[str],
    *,
    host_python_command,
    pcc_source_root,
    retention_host_code: str,
) -> None:
    if not enabled():
        return
    try:
        subprocess.run(
            [
                host_python_command(),
                "-c",
                retention_host_code,
                pcc_source_root(),
                cache_dir(),
            ]
            + protected_paths,
            check=True,
        )
    except Exception:
        pass


def plan(
    worker_items: list[tuple[str, str, str]],
    target_id: str,
    cc: str,
    tmp_dir: str,
    *,
    host_python_command,
    plan_host_code: str,
    small_int_decimal,
) -> list[tuple[str, str]]:
    disabled = [("", "off") for _item in worker_items]
    if not worker_items or not enabled():
        return disabled
    identity = str(os.environ.get(OBJECT_CACHE_IDENTITY_ENV, "") or "").strip()
    root = cache_dir()
    manifest_path = str(os.path.join(tmp_dir, "self_backend_cache_inputs.tsv"))
    plan_path = str(os.path.join(tmp_dir, "self_backend_cache_plan.tsv"))
    try:
        with open(manifest_path, "w", encoding="utf-8") as stream:
            for index, item in enumerate(worker_items):
                result_path, obj_path, ir_path = item
                stream.write(
                    small_int_decimal(index)
                    + "\t"
                    + ir_path
                    + "\t"
                    + result_path
                    + "\t"
                    + obj_path
                    + "\n"
                )
        subprocess.run(
            [
                host_python_command(),
                "-c",
                plan_host_code,
                OBJECT_CACHE_VERSION,
                identity,
                target_id,
                cc,
                root,
                manifest_path,
                plan_path,
            ],
            check=True,
        )
        with open(plan_path, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except Exception:
        return disabled
    if len(lines) != len(worker_items):
        return disabled
    planned: list[tuple[str, str]] = []
    for expected_index, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] != small_int_decimal(expected_index):
            return disabled
        cache_path, status = parts[1], parts[2]
        if status not in ("hit", "miss") or not path_allowed(cache_path):
            return disabled
        planned.append((cache_path, status))
    return planned


def publish(
    worker_items: list[tuple[str, str, str]],
    cache_plan: list[tuple[str, str]],
    tmp_dir: str,
    *,
    host_python_command,
    publish_host_code: str,
) -> bool:
    publish_rows: list[tuple[str, str]] = []
    for index, item in enumerate(worker_items):
        _result_path, obj_path, _ir_path = item
        cache_path, status = cache_plan[index]
        if status == "miss" and cache_path and os.path.isfile(obj_path):
            publish_rows.append((cache_path, obj_path))
    if not publish_rows:
        return True
    manifest_path = str(os.path.join(tmp_dir, "self_backend_cache_publish.tsv"))
    try:
        with open(manifest_path, "w", encoding="utf-8") as stream:
            for cache_path, obj_path in publish_rows:
                stream.write(cache_path + "\t" + obj_path + "\n")
        subprocess.run(
            [
                host_python_command(),
                "-c",
                publish_host_code,
                manifest_path,
            ],
            check=True,
        )
        return True
    except Exception:
        return False
