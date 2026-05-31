"""Safe pip-compatible front door for pcc package planning/install.

The shim accepts the common `pip install ... --dry-run` form and reports the
plan without invoking pip's installer.  Non-dry-run local installs are routed
through pcc's package installer rather than upstream pip.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .inspect import inspect_package
from .install import install_package, local_resolver_diagnostics, resolve_local_install_order


def _parse_install_args(args: list[str]) -> dict[str, object]:
    command = args[0] if args else "install"
    target_dir = None
    cache_dir = None
    abi = "pcc-native"
    find_links: list[str] = []
    index_urls: list[str] = []
    no_index = False
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
                return {"ok": False, "command": command, "error": arg + " requires a value"}
            target_dir = args[i + 1]
            i += 1
        elif arg == "--cache-dir":
            if i + 1 >= len(args):
                return {"ok": False, "command": command, "error": "--cache-dir requires a value"}
            cache_dir = args[i + 1]
            i += 1
        elif arg in ("--find-links", "-f"):
            if i + 1 >= len(args):
                return {"ok": False, "command": command, "error": arg + " requires a value"}
            find_links.append(args[i + 1])
            i += 1
        elif arg.startswith("--find-links="):
            find_links.append(arg.split("=", 1)[1])
        elif arg in ("--index-url", "-i"):
            if i + 1 >= len(args):
                return {"ok": False, "command": command, "error": arg + " requires a value"}
            index_urls.append(args[i + 1])
            i += 1
        elif arg.startswith("--index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--extra-index-url":
            if i + 1 >= len(args):
                return {"ok": False, "command": command, "error": "--extra-index-url requires a value"}
            index_urls.append(args[i + 1])
            i += 1
        elif arg.startswith("--extra-index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(args):
                return {"ok": False, "command": command, "error": "--abi requires a value"}
            abi = args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            pass
        else:
            packages.append(arg)
        i += 1
    return {
        "ok": True,
        "command": command,
        "dry_run": dry_run,
        "target_dir": target_dir,
        "cache_dir": cache_dir,
        "find_links": find_links,
        "index_urls": [] if no_index else index_urls,
        "no_index": no_index,
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
        return {"ok": False, "command": command, "error": "only install dry-run is supported"}
    if not dry_run:
        return {"ok": False, "command": command, "error": "pcc pip shim requires --dry-run"}
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
    if find_links or index_urls:
        packages = resolve_local_install_order(
            packages,
            cache_dir=cache_dir,  # type: ignore[arg-type]
            find_links=find_links,
            index_urls=index_urls,
            abi=abi,
        )
    resolver_diagnostics = local_resolver_diagnostics(
        packages,
        cache_dir=cache_dir,  # type: ignore[arg-type]
        find_links=find_links,
        index_urls=index_urls,
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
            index_urls=index_urls,
            abi=abi,
            build_source=True,
        )
        for pkg in packages
    ]
    return {
        "ok": all(item.get("ok") for item in installs),
        "command": command,
        "dry_run": False,
        "packages": packages,
        "find_links": find_links,
        "index_urls": index_urls,
        "no_index": bool(parsed["no_index"]),
        "abi": abi,
        "report_path": parsed["report_path"],
        "resolver_diagnostics": resolver_diagnostics,
        "installs": installs,
    }


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
        parser.print_help()
        return 0
    wants_plan = "--dry-run" in raw or "--report" in raw or any(
        item.startswith("--report=") for item in raw
    )
    plan = pip_dry_run_plan(raw) if wants_plan else pip_install_plan(raw)
    plan = _write_report_if_requested(plan)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
