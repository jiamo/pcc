"""Pip-compatible front door with explicit acquire/build/install boundaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquire import (
    ACQUIRE_MODES,
    acquire_requirement,
    looks_like_local_spec,
    target_python_version,
)
from .inspect import inspect_package
from .install import (
    _default_cache_dir,
    _resolve_spec_with_origin,
    install_package,
    local_resolver_diagnostics,
    resolve_local_install_order,
)


def _parse_install_args(args: list[str]) -> dict[str, object]:
    command = args[0] if args else "install"
    target_dir = None
    cache_dir = None
    abi = "pcc-native"
    find_links: list[str] = []
    index_urls: list[str] = []
    no_index = False
    acquire_mode = "auto"
    target_python = None
    packages: list[str] = []
    report_path = None
    dry_run = "--dry-run" in args
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--dry-run":
            pass
        elif arg == "--no-index":
            no_index = True
        elif arg == "--acquire":
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": "--acquire requires a value",
                }
            acquire_mode = args[i + 1]
            i += 1
        elif arg.startswith("--acquire="):
            acquire_mode = arg.split("=", 1)[1]
        elif arg in ("--python-version", "--target-python"):
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": arg + " requires a value",
                }
            target_python = args[i + 1]
            i += 1
        elif arg.startswith("--python-version=") or arg.startswith(
            "--target-python="
        ):
            target_python = arg.split("=", 1)[1]
        elif arg == "--report":
            dry_run = True
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                report_path = args[i + 1]
                i += 1
        elif arg.startswith("--report="):
            dry_run = True
            report_path = arg.split("=", 1)[1]
        elif arg in ("--target", "--target-dir"):
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": arg + " requires a value",
                }
            target_dir = args[i + 1]
            i += 1
        elif arg == "--cache-dir":
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": "--cache-dir requires a value",
                }
            cache_dir = args[i + 1]
            i += 1
        elif arg in ("--find-links", "-f"):
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": arg + " requires a value",
                }
            find_links.append(args[i + 1])
            i += 1
        elif arg.startswith("--find-links="):
            find_links.append(arg.split("=", 1)[1])
        elif arg in ("--index-url", "-i"):
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": arg + " requires a value",
                }
            index_urls.append(args[i + 1])
            i += 1
        elif arg.startswith("--index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--extra-index-url":
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": "--extra-index-url requires a value",
                }
            index_urls.append(args[i + 1])
            i += 1
        elif arg.startswith("--extra-index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(args):
                return {
                    "ok": False,
                    "command": command,
                    "error": "--abi requires a value",
                }
            abi = args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            pass
        else:
            packages.append(arg)
        i += 1
    if acquire_mode not in ACQUIRE_MODES:
        return {
            "ok": False,
            "command": command,
            "error": "PCC-PKG-ACQUIRE-MODE-INVALID",
        }
    if no_index:
        acquire_mode = "offline"
    try:
        target_python = target_python_version(target_python)
    except ValueError as exc:
        return {
            "ok": False,
            "command": command,
            "error": str(exc),
            "diagnostic": "PCC-PKG-ACQUIRE-TARGET-PYTHON-INVALID",
        }
    return {
        "ok": True,
        "command": command,
        "dry_run": dry_run,
        "target_dir": target_dir,
        "cache_dir": cache_dir,
        "find_links": find_links,
        "index_urls": [] if no_index else index_urls,
        "no_index": no_index,
        "acquire_mode": acquire_mode,
        "target_python": target_python,
        "report_path": report_path,
        "abi": abi,
        "packages": packages,
    }


def pip_dry_run_plan(args: list[str]) -> dict[str, object]:
    parsed = _parse_install_args(args)
    if not parsed.get("ok"):
        return parsed
    command = str(parsed["command"])
    dry_run = bool(parsed["dry_run"])
    find_links = list(parsed["find_links"])  # type: ignore[arg-type]
    index_urls = list(parsed["index_urls"])  # type: ignore[arg-type]
    abi = str(parsed["abi"])
    packages = list(parsed["packages"])  # type: ignore[arg-type]
    if find_links or index_urls:
        packages = resolve_local_install_order(
            packages,
            cache_dir=parsed["cache_dir"],  # type: ignore[arg-type]
            find_links=find_links,
            index_urls=index_urls,
            abi=abi,
        )
    resolver_diagnostics = local_resolver_diagnostics(
        packages,
        cache_dir=parsed["cache_dir"],  # type: ignore[arg-type]
        find_links=find_links,
        index_urls=index_urls,
        abi=abi,
    )
    if command != "install":
        return {
            "ok": False,
            "command": command,
            "error": "only install dry-run is supported",
        }
    if not dry_run:
        return {
            "ok": False,
            "command": command,
            "error": "pcc pip shim requires --dry-run",
        }
    if not packages:
        return {"ok": False, "command": command, "error": "no packages requested"}
    inspected = [inspect_package(pkg).as_dict() for pkg in packages]
    return {
        "ok": True,
        "command": command,
        "dry_run": True,
        "packages": packages,
        "find_links": find_links,
        "index_urls": index_urls,
        "no_index": bool(parsed["no_index"]),
        "acquire_mode_requested": parsed["acquire_mode"],
        "target_python": parsed["target_python"],
        "abi": abi,
        "report_path": parsed["report_path"],
        "resolver_diagnostics": resolver_diagnostics,
        "inspections": inspected,
    }


def pip_install_plan(args: list[str]) -> dict[str, object]:
    parsed = _parse_install_args(args)
    if not parsed.get("ok"):
        return parsed
    command = str(parsed["command"])
    if command != "install":
        return {"ok": False, "command": command, "error": "only install is supported"}
    target_dir = parsed["target_dir"]
    cache_dir = parsed["cache_dir"]
    find_links = list(parsed["find_links"])  # type: ignore[arg-type]
    index_urls = list(parsed["index_urls"])  # type: ignore[arg-type]
    abi = str(parsed["abi"])
    packages = list(parsed["packages"])  # type: ignore[arg-type]
    acquire_mode = str(parsed["acquire_mode"])
    cache = (
        Path(str(cache_dir)).expanduser().resolve()
        if cache_dir is not None
        else _default_cache_dir()
    )
    install_specs: list[str] = []
    acquisitions: list[dict[str, object]] = []
    for package in packages:
        local_source, origin = _resolve_spec_with_origin(
            package,
            cache,
            find_links,
            index_urls=(),
            abi=abi,
        )
        project_shadowed_by_online_acquire = (
            origin == "projects" and acquire_mode != "offline"
        )
        if (
            local_source is not None and not project_shadowed_by_online_acquire
        ) or looks_like_local_spec(package):
            install_specs.append(package)
            continue
        acquisition = acquire_requirement(
            package,
            mode=acquire_mode,
            cache_dir=cache,
            index_urls=index_urls,
            abi=abi,
            target_python=str(parsed["target_python"]),
        )
        acquisitions.append(acquisition)
        artifact_path = acquisition.get("artifact_path")
        if acquisition.get("ok") and artifact_path:
            install_specs.append(str(artifact_path))

    failed_acquisitions = [item for item in acquisitions if not item.get("ok")]
    if failed_acquisitions:
        return {
            "ok": False,
            "command": command,
            "dry_run": False,
            "packages": packages,
            "find_links": find_links,
            "index_urls": index_urls,
            "no_index": bool(parsed["no_index"]),
            "abi": abi,
            "acquire_mode_requested": acquire_mode,
            "target_python": parsed["target_python"],
            "acquisitions": acquisitions,
            "installs": [],
            "resolver_diagnostics": [],
            "report_path": parsed["report_path"],
            "error": failed_acquisitions[0].get("error"),
        }
    if find_links or install_specs != packages:
        install_specs = resolve_local_install_order(
            install_specs,
            cache_dir=cache,
            find_links=find_links,
            index_urls=(),
            abi=abi,
        )
    resolver_diagnostics = local_resolver_diagnostics(
        install_specs,
        cache_dir=cache,
        find_links=find_links,
        index_urls=(),
        abi=abi,
    )
    if not packages:
        return {"ok": False, "command": command, "error": "no packages requested"}
    installs = [
        install_package(
            pkg,
            target_dir=target_dir,  # type: ignore[arg-type]
            cache_dir=cache_dir,  # type: ignore[arg-type]
            find_links=find_links,
            index_urls=(),
            abi=abi,
            build_source=True,
        )
        for pkg in install_specs
    ]
    plan: dict[str, object] = {
        "ok": all(item.get("ok") for item in installs),
        "command": command,
        "dry_run": False,
        "packages": packages,
        "install_specs": install_specs,
        "find_links": find_links,
        "index_urls": index_urls,
        "no_index": bool(parsed["no_index"]),
        "abi": abi,
        "acquire_mode_requested": acquire_mode,
        "target_python": parsed["target_python"],
        "acquisitions": acquisitions,
        "report_path": parsed["report_path"],
        "resolver_diagnostics": resolver_diagnostics,
        "installs": installs,
    }
    return plan


def _write_report_if_requested(plan: dict[str, object]) -> dict[str, object]:
    report_path = plan.get("report_path")
    if not report_path:
        return plan
    path = Path(str(report_path)).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        failed = dict(plan)
        failed["ok"] = False
        failed["error"] = f"failed to write report: {exc}"
        return failed
    return plan


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "pip":
        raw = raw[1:]
    if not raw:
        raw = ["install", "--dry-run"]
    if "--help" in raw or "-h" in raw:
        parser = argparse.ArgumentParser(prog="pcc -m pip")
        parser.add_argument("install", nargs="?")
        parser.add_argument("packages", nargs="*")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--python-version")
        parser.print_help()
        return 0
    wants_plan = (
        "--dry-run" in raw
        or "--report" in raw
        or any(item.startswith("--report=") for item in raw)
    )
    plan = pip_dry_run_plan(raw) if wants_plan else pip_install_plan(raw)
    plan = _write_report_if_requested(plan)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
