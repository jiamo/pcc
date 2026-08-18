"""Installed-package and native-extension path discovery for the pipeline."""

from __future__ import annotations

import os
from typing import Optional

from ..package_environment import (
    package_environment_fingerprint as environment_fingerprint,
)
from ..package_environment import package_site_roots as environment_site_roots
from .pipeline_paths import resolve_module_src

NATIVE_EXTENSION_SUFFIXES = (".so", ".dylib", ".pyd", ".dll")


# Environment RESOLUTION is cached per fingerprint (the coordinator calls
# resolve_pcc_native_extension_path once per import edge, and every call was
# re-running the 13-env-var resolution plus an environment.json open/read/
# parse: 34% of a profiled stage2 coordinator window).  Filesystem PROBES
# (isdir here, isfile/listdir below) deliberately stay per-call so packages
# installed mid-process are still found.
_SITE_ROOTS_CACHE: dict = {}


def package_site_roots() -> list[str]:
    fingerprint = environment_fingerprint()
    roots = _SITE_ROOTS_CACHE.get(fingerprint)
    if roots is None:
        roots = tuple(environment_site_roots())
        _SITE_ROOTS_CACHE[fingerprint] = roots
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if root in seen or not os.path.isdir(root):
            continue
        seen.add(root)
        out.append(root)
    return out


def native_extension_name_uses_cpython_abi(path: str) -> bool:
    lower = os.path.basename(str(path or "")).lower()
    if ".cpython-" in lower or "-cpython-" in lower or "_cpython-" in lower:
        return True
    if ".abi3" in lower or "-abi3" in lower or "_abi3" in lower:
        return True
    first_cp = lower.find("-cp")
    return first_cp >= 0 and lower.find("-cp", first_cp + 3) >= 0


def resolve_pcc_native_extension_path(module_name: str) -> Optional[str]:
    """Return a pcc-native extension artifact for ``module_name`` if present."""

    relative = str(module_name or "").replace(".", os.sep)
    if not relative:
        return None
    for site_root in package_site_roots():
        base = str(os.path.join(site_root, relative))
        for suffix in NATIVE_EXTENSION_SUFFIXES:
            candidate = base + suffix
            if os.path.isfile(candidate) and not native_extension_name_uses_cpython_abi(
                candidate
            ):
                return str(os.path.abspath(candidate))
        parent = str(os.path.dirname(base))
        leaf = str(os.path.basename(base))
        if not parent or not os.path.isdir(parent):
            continue
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            names = []
        for name in names:
            full = str(os.path.join(parent, name))
            if not os.path.isfile(full):
                continue
            if not name.startswith(leaf + "."):
                continue
            if not name.lower().endswith(NATIVE_EXTENSION_SUFFIXES):
                continue
            if native_extension_name_uses_cpython_abi(name):
                continue
            return str(os.path.abspath(full))
    return None


def resolve_module_src_for_import(
    root_dir: str, dotted_name: str
) -> Optional[str]:
    target = resolve_module_src(root_dir, dotted_name)
    if target is not None:
        return target
    for site_root in package_site_roots():
        target = resolve_module_src(site_root, dotted_name)
        if target is not None:
            return target
    return None


def package_site_package_root_for_src(src_path: str) -> Optional[str]:
    absolute_source = str(os.path.abspath(src_path))
    for site_root in package_site_roots():
        absolute_site = str(os.path.abspath(site_root))
        prefix = absolute_site if absolute_site.endswith(os.sep) else absolute_site + os.sep
        if not absolute_source.startswith(prefix):
            continue
        relative = absolute_source[len(prefix) :]
        first = relative.split(os.sep, 1)[0]
        if not first:
            continue
        package_root = str(os.path.join(absolute_site, first))
        if os.path.isfile(os.path.join(package_root, "pcc-package.json")):
            return package_root
    return None


def package_site_package_root_for_module_name(
    module_name: str,
) -> Optional[str]:
    top = str(module_name or "").split(".", 1)[0]
    if not top:
        return None
    for site_root in package_site_roots():
        package_root = str(os.path.join(site_root, top))
        if os.path.isfile(os.path.join(package_root, "pcc-package.json")):
            return package_root
    return None


def package_root_no_libpython_diagnostic(
    root: str,
) -> Optional[tuple[str, str]]:
    queue = [str(root)]
    queue_index = 0
    while queue_index < len(queue):
        current = queue[queue_index]
        queue_index += 1
        try:
            names = os.listdir(current)
        except OSError:
            names = []
        for name in names:
            path = str(os.path.join(current, name))
            if os.path.isdir(path):
                queue.append(path)
                continue
            lower = name.lower()
            if not lower.endswith(NATIVE_EXTENSION_SUFFIXES):
                continue
            if native_extension_name_uses_cpython_abi(name):
                return ("PCC-PKG-004", path)
    return None
