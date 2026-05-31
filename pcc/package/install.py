"""Generic pcc package install manifest flow.

This is a real local/cache install skeleton, not a NumPy-specific shortcut.
It copies local source trees or extracts wheel/sdist artifacts into a pcc site
directory and writes a manifest that future import/build steps can consume.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

from .inspect import inspect_package
from .linkage import linkage_report
from .metadata import inspect_artifact, pcc_native_wheel_tag


_REQ_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")
_VERSION_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")
_REPOSITORY_MANIFEST = "pcc-wheel-repository.json"
_ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_site_dir() -> Path:
    env = os.environ.get("PCC_PACKAGE_SITE")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".cache" / "pcc" / "site-packages"


def _default_cache_dir() -> Path:
    env = os.environ.get("PCC_PACKAGE_CACHE")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".cache" / "pcc" / "package-cache"


def _normalized_package_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _artifact_project_name(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.split("-")[0] if "-" in name else name


def _artifact_version_text(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = name.split("-")
    if path.name.endswith(".whl") and len(parts) >= 5:
        return parts[1]
    if len(parts) >= 2:
        return parts[1]
    return "0"


def _wheel_tags_from_name(name: str) -> tuple[str | None, str | None, str | None]:
    stem = name[:-4] if name.endswith(".whl") else name
    parts = stem.split("-")
    if len(parts) < 5:
        return None, None, None
    return parts[-3], parts[-2], parts[-1]


def _version_key(version: str) -> tuple[tuple[int, object], ...]:
    key: list[tuple[int, object]] = []
    for token in _VERSION_TOKEN_RE.findall(version):
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token.lower()))
    return tuple(key) or ((0, 0),)


def _artifact_selection_key(
    path: Path,
    compatibility_reason: str | None = None,
) -> tuple[tuple[tuple[int, object], ...], int, str]:
    ranks = {
        "source_artifact": 1,
        "pure_python_wheel": 2,
        "pcc_native_wheel": 3,
    }
    return (
        _version_key(_artifact_version_text(path)),
        ranks.get(compatibility_reason or "", 0),
        path.name,
    )


def _artifact_compatibility_reason(path: Path, *, abi: str) -> tuple[bool, str]:
    return _artifact_compatibility_reason_from_name(path.name, abi=abi)


def _artifact_compatibility_reason_from_name(name: str, *, abi: str) -> tuple[bool, str]:
    if abi != "pcc-native":
        return True, "abi_mode_allows_artifact"
    lower = name.lower()
    if lower.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip")):
        return True, "source_artifact"
    if not lower.endswith(".whl"):
        return False, "unsupported_artifact_kind"
    python_tag, abi_tag, platform_tag = _wheel_tags_from_name(name)
    if python_tag == "py3" and abi_tag == "none" and platform_tag == "any":
        return True, "pure_python_wheel"
    if f"{python_tag}-{abi_tag}-{platform_tag}" == pcc_native_wheel_tag():
        return True, "pcc_native_wheel"
    return False, "wheel_tag_not_pcc_native_compatible"


def _repository_manifest_candidates(
    root: Path,
    expected: str,
    *,
    abi: str,
) -> list[tuple[Path, str]]:
    manifest_path = root / _REPOSITORY_MANIFEST
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        return []
    candidates: list[tuple[Path, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if _normalized_package_name(name) != expected:
            continue
        path_text = row.get("path")
        if not isinstance(path_text, str):
            continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            continue
        reason = str(row.get("compatibility_reason") or "")
        if abi == "pcc-native" and (
            not bool(row.get("pcc_native_compatible"))
            or bool(row.get("links_libpython"))
        ):
            continue
        candidates.append((path.resolve(), reason))
    return candidates


def _parse_requires_dist_name(line: str) -> str | None:
    if not line.startswith("Requires-Dist:"):
        return None
    rest = line[len("Requires-Dist:") :].strip()
    match = _REQ_NAME_RE.match(rest)
    if match is None:
        return None
    return match.group(0)


def _requires_dist_diagnostics_text(text: str) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        rest = line[len("Requires-Dist:") :].strip()
        dep = _parse_requires_dist_name(line) or "dependency"
        name_end = len(dep)
        if "[" in rest[: rest.find(";") if ";" in rest else len(rest)]:
            diagnostics.append(f"unsupported_extras:{dep}")
        if any(op in rest[name_end:] for op in ("<", ">", "=", "!", "~")):
            diagnostics.append(f"unresolved_version_constraint:{dep}")
        if ";" in rest:
            diagnostics.append(f"unsupported_environment_marker:{dep}")
    return tuple(dict.fromkeys(diagnostics))


def _metadata_requires_dist_text(text: str) -> tuple[str, ...]:
    deps: list[str] = []
    for line in text.splitlines():
        dep = _parse_requires_dist_name(line)
        if dep:
            deps.append(dep)
    return tuple(deps)


def artifact_requires_dist_diagnostics(path: str | Path) -> tuple[str, ...]:
    source = Path(path).expanduser().resolve()
    diagnostics: list[str] = []
    if source.is_dir():
        metadata_files = list(source.rglob("METADATA")) + list(source.rglob("PKG-INFO"))
        for metadata in metadata_files:
            try:
                diagnostics.extend(
                    _requires_dist_diagnostics_text(metadata.read_text(encoding="utf-8"))
                )
            except OSError:
                continue
        return tuple(dict.fromkeys(diagnostics))
    lower = source.name.lower()
    try:
        if lower.endswith(".whl") or lower.endswith(".zip"):
            with zipfile.ZipFile(source) as zf:
                for name in zf.namelist():
                    if name.endswith(".dist-info/METADATA") or name.endswith("PKG-INFO"):
                        diagnostics.extend(
                            _requires_dist_diagnostics_text(
                                zf.read(name).decode("utf-8", errors="replace")
                            )
                        )
        elif lower.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")):
            with tarfile.open(source) as tf:
                for member in tf.getmembers():
                    if member.isfile() and (
                        member.name.endswith(".dist-info/METADATA")
                        or member.name.endswith("PKG-INFO")
                    ):
                        extracted = tf.extractfile(member)
                        if extracted is not None:
                            diagnostics.extend(
                                _requires_dist_diagnostics_text(
                                    extracted.read().decode("utf-8", errors="replace")
                                )
                            )
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return ()
    return tuple(dict.fromkeys(diagnostics))


def artifact_requires_dist(path: str | Path) -> tuple[str, ...]:
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        metadata_files = list(source.rglob("METADATA")) + list(source.rglob("PKG-INFO"))
        deps: list[str] = []
        for metadata in metadata_files:
            try:
                deps.extend(_metadata_requires_dist_text(metadata.read_text(encoding="utf-8")))
            except OSError:
                continue
        return tuple(dict.fromkeys(deps))
    lower = source.name.lower()
    deps: list[str] = []
    try:
        if lower.endswith(".whl") or lower.endswith(".zip"):
            with zipfile.ZipFile(source) as zf:
                for name in zf.namelist():
                    if name.endswith(".dist-info/METADATA") or name.endswith("PKG-INFO"):
                        deps.extend(
                            _metadata_requires_dist_text(
                                zf.read(name).decode("utf-8", errors="replace")
                            )
                        )
        elif lower.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")):
            with tarfile.open(source) as tf:
                for member in tf.getmembers():
                    if member.isfile() and (
                        member.name.endswith(".dist-info/METADATA")
                        or member.name.endswith("PKG-INFO")
                    ):
                        extracted = tf.extractfile(member)
                        if extracted is not None:
                            deps.extend(
                                _metadata_requires_dist_text(
                                    extracted.read().decode("utf-8", errors="replace")
                                )
                            )
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return ()
    return tuple(dict.fromkeys(deps))


def _find_links_artifact_with_origin(
    spec: str,
    find_links: list[str] | tuple[str, ...],
    *,
    abi: str = "pcc-native",
) -> tuple[Path | None, str | None]:
    expected = _normalized_package_name(spec)
    manifest_matches: list[tuple[Path, str]] = []
    matches: list[tuple[Path, str]] = []
    for link in find_links:
        root = Path(link).expanduser()
        if root.is_dir():
            manifest_matches.extend(_repository_manifest_candidates(root, expected, abi=abi))
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = [child for child in root.iterdir() if child.is_file()]
        else:
            candidates = []
        for candidate in candidates:
            if not candidate.name.endswith(_ARTIFACT_SUFFIXES):
                continue
            if _normalized_package_name(_artifact_project_name(candidate)) == expected:
                allowed, reason = _artifact_compatibility_reason(candidate, abi=abi)
                if allowed:
                    matches.append((candidate.resolve(), reason))
    if manifest_matches:
        best = sorted(
            manifest_matches,
            key=lambda item: _artifact_selection_key(item[0], item[1]),
        )[-1]
        return best[0], "wheel-repository"
    if matches:
        best = sorted(
            matches,
            key=lambda item: _artifact_selection_key(item[0], item[1]),
        )[-1]
        return best[0], "find-links"
    return None, None


def _find_links_artifact(
    spec: str,
    find_links: list[str] | tuple[str, ...],
    *,
    abi: str = "pcc-native",
) -> Path | None:
    path, _origin = _find_links_artifact_with_origin(spec, find_links, abi=abi)
    return path


def _simple_index_package_url(index_url: str, package: str) -> str:
    base = index_url.rstrip("/") + "/"
    return urllib.parse.urljoin(base, _normalized_package_name(package) + "/")


def _simple_index_links(page_text: str, page_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in re.finditer(r"""href\s*=\s*['"]([^'"]+)['"]""", page_text, re.IGNORECASE):
        href = match.group(1)
        url = urllib.parse.urljoin(page_url, href)
        name = Path(urllib.parse.urlparse(url).path).name
        if name:
            links.append((url, name))
    return links


def _download_index_artifact(
    spec: str,
    cache_dir: Path,
    index_urls: list[str] | tuple[str, ...],
    *,
    abi: str,
) -> Path | None:
    expected = _normalized_package_name(spec)
    candidates: list[tuple[str, str, str]] = []
    for index_url in index_urls:
        page_url = _simple_index_package_url(index_url, spec)
        try:
            with urllib.request.urlopen(page_url, timeout=20) as response:
                page_text = response.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        for artifact_url, filename in _simple_index_links(page_text, page_url):
            if not filename.endswith(_ARTIFACT_SUFFIXES):
                continue
            if _normalized_package_name(_artifact_project_name(Path(filename))) != expected:
                continue
            allowed, reason = _artifact_compatibility_reason_from_name(filename, abi=abi)
            if allowed:
                candidates.append((artifact_url, filename, reason))
    if not candidates:
        return None
    best_url, best_name, _best_reason = sorted(
        candidates,
        key=lambda item: _artifact_selection_key(Path(item[1]), item[2]),
    )[-1]
    download_dir = cache_dir / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = download_dir / best_name
    try:
        with urllib.request.urlopen(best_url, timeout=60) as response:
            dest.write_bytes(response.read())
    except (OSError, ValueError):
        return None
    return dest.resolve()


def local_resolver_diagnostics(
    packages: list[str] | tuple[str, ...],
    *,
    cache_dir: str | Path | None = None,
    find_links: list[str] | tuple[str, ...] = (),
    index_urls: list[str] | tuple[str, ...] = (),
    abi: str = "pcc-native",
) -> list[str]:
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()
    diagnostics: list[str] = []
    for package in resolve_local_install_order(
        packages,
        cache_dir=cache,
        find_links=find_links,
        index_urls=index_urls,
        abi=abi,
    ):
        source = _resolve_spec(str(package), cache, find_links, index_urls=index_urls, abi=abi)
        if source is None:
            continue
        for diagnostic in artifact_requires_dist_diagnostics(source):
            diagnostics.append(f"{package}:{diagnostic}")
    return list(dict.fromkeys(diagnostics))


def resolve_local_install_order(
    packages: list[str] | tuple[str, ...],
    *,
    cache_dir: str | Path | None = None,
    find_links: list[str] | tuple[str, ...] = (),
    index_urls: list[str] | tuple[str, ...] = (),
    abi: str = "pcc-native",
) -> list[str]:
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()
    ordered: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(pkg: str) -> None:
        key = _normalized_package_name(pkg)
        if key in done or key in visiting:
            return
        visiting.add(key)
        source = _resolve_spec(pkg, cache, find_links, index_urls=index_urls, abi=abi)
        if source is not None:
            for dep in artifact_requires_dist(source):
                if _resolve_spec(dep, cache, find_links, index_urls=index_urls, abi=abi) is not None:
                    visit(dep)
        visiting.remove(key)
        done.add(key)
        ordered.append(pkg)

    for package in packages:
        visit(str(package))
    return ordered


def _resolve_spec(
    spec: str,
    cache_dir: Path | None = None,
    find_links: list[str] | tuple[str, ...] = (),
    index_urls: list[str] | tuple[str, ...] = (),
    *,
    abi: str = "pcc-native",
) -> Path | None:
    path, _origin = _resolve_spec_with_origin(
        spec,
        cache_dir,
        find_links,
        index_urls=index_urls,
        abi=abi,
    )
    return path


def _resolve_spec_with_origin(
    spec: str,
    cache_dir: Path | None = None,
    find_links: list[str] | tuple[str, ...] = (),
    index_urls: list[str] | tuple[str, ...] = (),
    *,
    abi: str = "pcc-native",
) -> tuple[Path | None, str | None]:
    candidate = Path(spec).expanduser()
    if candidate.exists():
        return candidate.resolve(), "direct"
    projects = _repo_root() / "projects"
    matches = sorted(projects.glob(spec + "-*")) if projects.exists() else []
    if matches:
        return matches[-1].resolve(), "projects"
    linked, origin = _find_links_artifact_with_origin(spec, find_links, abi=abi)
    if linked is not None:
        return linked, origin
    if cache_dir is not None and cache_dir.exists():
        direct = cache_dir / spec
        if direct.exists():
            return direct.resolve(), "cache"
        for suffix in (".whl", ".tar.gz", ".tgz", ".zip"):
            matches = sorted(cache_dir.glob(spec + "-*" + suffix))
            if matches:
                return matches[-1].resolve(), "cache"
    if cache_dir is not None and index_urls:
        downloaded = _download_index_artifact(spec, cache_dir, index_urls, abi=abi)
        if downloaded is not None:
            return downloaded, "index-url"
    return None, None


def _iter_importable_roots(source: Path) -> Iterable[Path]:
    """Yield top-level importable package/module payloads from a source tree."""
    bases = [source, source / "src"]
    visible_dirs = [
        child
        for child in sorted(source.iterdir()) if source.is_dir()
        if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__"
    ] if source.is_dir() else []
    if len(visible_dirs) == 1:
        bases.append(visible_dirs[0])
        bases.append(visible_dirs[0] / "src")

    seen: set[Path] = set()
    for base in bases:
        if not base.is_dir():
            continue
        if (base / "__init__.py").is_file():
            resolved = base.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield base
            continue
        for child in sorted(base.iterdir()):
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            if child.is_dir() and (child / "__init__.py").is_file():
                resolved = child.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield child
            elif child.is_file() and child.suffix == ".py" and child.name != "setup.py":
                resolved = child.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield child


def _copy_importable_payload(source: Path, site: Path, metadata_name: str) -> tuple[Path, list[str]]:
    importable = list(_iter_importable_roots(source))
    if not importable:
        install_root = site / metadata_name
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, install_root / "source", dirs_exist_ok=True)
        return install_root, [str(install_root / "source")]

    first = importable[0]
    install_root = site / (first.name if first.is_dir() else metadata_name)
    if not first.is_dir():
        install_root.mkdir(parents=True, exist_ok=True)
    payloads: list[str] = []
    for payload in importable:
        if payload.is_dir():
            dest = site / payload.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(payload, dest)
        else:
            dest = site / payload.name
            if dest.exists():
                dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload, dest)
        payloads.append(str(dest))
    return install_root, payloads


def _overlay_meson_build_payloads(source: Path, site: Path, installed: list[str]) -> list[str]:
    """Overlay pcc-managed Meson build outputs onto an installed source tree.

    Meson build directories often contain generated Python files, headers, and
    extension modules while the source tree contains the rest of the package.
    A pip-style local install needs the merged view. This stays package
    agnostic by copying any importable payload from pcc's Meson build dir.
    """
    build_root = source / "build" / "pcc-package" / "meson-build"
    if not build_root.is_dir():
        return installed
    payloads = list(installed)
    seen = set(payloads)
    for payload in _iter_importable_roots(build_root):
        if payload.is_dir():
            dest = site / payload.name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(payload, dest, dirs_exist_ok=True)
        else:
            dest = site / payload.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload, dest)
        dest_text = str(dest)
        if dest_text not in seen:
            seen.add(dest_text)
            payloads.append(dest_text)
    return payloads


def _archive_importable_roots(artifact_dir: Path) -> list[Path]:
    return list(_iter_importable_roots(artifact_dir))


def _copy_payloads_to_site(
    payloads: list[Path],
    site: Path,
    metadata_name: str,
) -> tuple[Path, list[str]]:
    install_root = site / metadata_name
    installed: list[str] = []
    for payload in payloads:
        if payload.is_dir():
            dest = site / payload.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(payload, dest)
            if payload.name == metadata_name:
                install_root = dest
        else:
            dest = site / payload.name
            if dest.exists():
                dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload, dest)
        installed.append(str(dest))
    install_root.mkdir(parents=True, exist_ok=True)
    return install_root, installed


def _copy_or_extract(source: Path, site: Path, metadata_name: str) -> tuple[Path, list[str]]:
    site.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        install_root, installed = _copy_importable_payload(source, site, metadata_name)
        return install_root, _overlay_meson_build_payloads(source, site, installed)

    lower = source.name.lower()
    install_root = site / metadata_name
    if install_root.exists():
        shutil.rmtree(install_root)
    if lower.endswith(".whl") or lower.endswith(".zip"):
        with tempfile.TemporaryDirectory(prefix="pcc_pkg_extract_") as tmp:
            artifact_dir = Path(tmp) / "artifact"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as zf:
                zf.extractall(artifact_dir)
            importable = _archive_importable_roots(artifact_dir)
            if importable:
                return _copy_payloads_to_site(importable, site, metadata_name)
            install_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(artifact_dir, install_root / "artifact")
            return install_root, [str(install_root / "artifact")]
    if lower.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")):
        with tempfile.TemporaryDirectory(prefix="pcc_pkg_extract_") as tmp:
            artifact_dir = Path(tmp) / "artifact"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(source) as tf:
                tf.extractall(artifact_dir)
            importable = _archive_importable_roots(artifact_dir)
            if importable:
                return _copy_payloads_to_site(importable, site, metadata_name)
            install_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(artifact_dir, install_root / "artifact")
            return install_root, [str(install_root / "artifact")]
    artifact_dir = install_root / "artifact"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, artifact_dir / source.name)
    return install_root, [str(artifact_dir / source.name)]


def _build_requirement_tool_wrappers(requires: tuple[str, ...]) -> tempfile.TemporaryDirectory[str] | None:
    cython_requirement = None
    for requirement in requires:
        if requirement.lower().startswith("cython"):
            cython_requirement = requirement
            break
    if cython_requirement is None:
        return None
    temp_dir = tempfile.TemporaryDirectory(prefix="pcc_build_tools_")
    bin_dir = Path(temp_dir.name)
    script = (
        "#!/bin/sh\n"
        "exec uv run --with "
        + repr(cython_requirement)
        + " cython \"$@\"\n"
    )
    for name in ("cython", "cython3"):
        tool = bin_dir / name
        tool.write_text(script, encoding="utf-8")
        tool.chmod(0o755)
    return temp_dir


def _meson_setup_command(source: Path, build_dir: Path, path_env: str) -> list[str] | None:
    meson_tool = shutil.which("meson", path=path_env)
    if meson_tool is not None:
        return [meson_tool, "setup", str(build_dir), str(source)]
    vendored = source / "vendored-meson" / "meson" / "meson.py"
    if vendored.exists():
        return [sys.executable, str(vendored), "setup", str(build_dir), str(source)]
    return None


def _ensure_meson_build_outputs(source: Path, requires: tuple[str, ...]) -> dict[str, object]:
    if not (source / "meson.build").is_file():
        return {"ok": True, "skipped": True, "reason": "no_meson_build", "actions": []}
    build_dir = source / "build" / "pcc-package" / "meson-build"
    timeout = int(os.environ.get("PCC_PACKAGE_BUILD_TIMEOUT", "600"))
    wrappers = _build_requirement_tool_wrappers(requires)
    prefix = wrappers.name if wrappers is not None else ""
    env = os.environ.copy()
    if prefix:
        env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
    path_env = env.get("PATH", "")
    actions: list[dict[str, object]] = []
    try:
        if not (build_dir / "build.ninja").is_file():
            setup_command = _meson_setup_command(source, build_dir, path_env)
            if setup_command is None:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "meson_not_available",
                    "actions": [],
                }
            build_dir.mkdir(parents=True, exist_ok=True)
            setup = subprocess.run(
                setup_command,
                cwd=source,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            actions.append(
                {
                    "kind": "meson_setup",
                    "command": setup_command,
                    "returncode": setup.returncode,
                    "status": "passed" if setup.returncode == 0 else "failed",
                    "stdout": setup.stdout[-4000:],
                    "stderr": setup.stderr[-4000:],
                }
            )
            if setup.returncode != 0:
                return {"ok": False, "skipped": False, "actions": actions}
        ninja = shutil.which("ninja", path=path_env) or "ninja"
        build_command = [ninja, "-C", str(build_dir)]
        build = subprocess.run(
            build_command,
            cwd=source,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        actions.append(
            {
                "kind": "meson_build",
                "command": build_command,
                "returncode": build.returncode,
                "status": "passed" if build.returncode == 0 else "failed",
                "stdout": build.stdout[-4000:],
                "stderr": build.stderr[-4000:],
            }
        )
        return {"ok": build.returncode == 0, "skipped": False, "actions": actions}
    except (OSError, subprocess.TimeoutExpired) as exc:
        actions.append(
            {
                "kind": "meson_build",
                "command": [],
                "returncode": None,
                "status": "failed",
                "stdout": "",
                "stderr": str(exc),
            }
        )
        return {"ok": False, "skipped": False, "actions": actions}
    finally:
        if wrappers is not None:
            wrappers.cleanup()


def _populate_cache_payload(cache_record: Path, installed_payloads: list[str]) -> None:
    cache_record.mkdir(parents=True, exist_ok=True)
    for payload_text in installed_payloads:
        payload = Path(payload_text)
        if not payload.exists():
            continue
        if payload.name == cache_record.name:
            dest = cache_record
        else:
            dest = cache_record / payload.name
        try:
            if dest.resolve() == payload.resolve():
                continue
        except FileNotFoundError:
            pass
        if payload.is_dir():
            if dest.exists():
                for child in dest.iterdir():
                    if child.name == "pcc-package.json":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                shutil.copytree(payload, dest, dirs_exist_ok=True)
            else:
                shutil.copytree(payload, dest)
        else:
            if dest.exists():
                dest.unlink()
            shutil.copy2(payload, dest)


def install_package(
    spec: str,
    *,
    target_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    find_links: list[str] | tuple[str, ...] = (),
    index_urls: list[str] | tuple[str, ...] = (),
    abi: str = "pcc-native",
    use_cache: bool = True,
    build_source: bool = False,
) -> dict[str, object]:
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()
    source, resolved_from = _resolve_spec_with_origin(
        spec,
        cache if use_cache else None,
        find_links,
        index_urls=index_urls,
        abi=abi,
    )
    if source is None:
        return {
            "ok": False,
            "spec": spec,
            "error": "package artifact not found locally or in pcc cache",
        }

    metadata_name = "" if Path(spec).expanduser().exists() else spec
    metadata = inspect_artifact(metadata_name, source)
    inspection = inspect_package(metadata.name, str(source) if source.is_dir() else None)
    build_report: dict[str, object] = {
        "ok": True,
        "skipped": True,
        "reason": "build_source_disabled",
        "actions": [],
    }
    if build_source and source.is_dir():
        build_report = _ensure_meson_build_outputs(source, metadata.pyproject_requires)
    site = Path(target_dir).expanduser().resolve() if target_dir else _default_site_dir()
    install_root, installed_payloads = _copy_or_extract(source, site, metadata.name)

    cache_record = cache / metadata.name
    scan_roots = installed_payloads if installed_payloads else [str(install_root)]
    linkage = linkage_report(roots=scan_roots, abi_mode=abi)
    install_ok = bool(linkage.get("ok")) and bool(build_report.get("ok", True))

    if install_ok:
        cache.mkdir(parents=True, exist_ok=True)
        source_is_cache_record = False
        try:
            source_is_cache_record = source.resolve() == cache_record.resolve()
        except FileNotFoundError:
            source_is_cache_record = False
        if not source_is_cache_record:
            _populate_cache_payload(cache_record, installed_payloads)
        else:
            cache_record.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": 1,
        "ok": install_ok,
        "name": metadata.name,
        "spec": spec,
        "abi_mode": abi,
        "source_path": str(source),
        "resolved_from": resolved_from or "unresolved",
        "installed_path": str(install_root),
        "installed_payload": installed_payloads[0] if installed_payloads else str(install_root),
        "installed_payloads": installed_payloads,
        "cache_record": str(cache_record),
        "links_libpython": bool(linkage.get("links_libpython")),
        "no_libpython_runtime": bool(linkage.get("no_libpython_runtime")),
        "link_libpython_edges": list(linkage.get("link_libpython_edges", [])),
        "linkage": linkage,
        "pcc_native_wheel_tag": pcc_native_wheel_tag(),
        "metadata": metadata.as_dict(),
        "dependency_diagnostics": list(artifact_requires_dist_diagnostics(source)),
        "diagnostics": list(linkage.get("diagnostics", [])),
        "inspection": inspection.as_dict(),
        "build_report": build_report,
    }
    manifest_path = install_root / "pcc-package.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    if install_ok:
        (cache_record / "pcc-package.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc.package install")
    parser.add_argument("spec")
    parser.add_argument("--target", "--target-dir", dest="target_dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--find-links", "-f", action="append", default=[])
    parser.add_argument("--index-url", "-i", action="append", default=[])
    parser.add_argument("--extra-index-url", action="append", default=[])
    parser.add_argument("--abi", default="pcc-native")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--build-source", action="store_true")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    result = install_package(
        ns.spec,
        target_dir=ns.target_dir,
        cache_dir=ns.cache_dir,
        find_links=ns.find_links,
        index_urls=ns.index_url + ns.extra_index_url,
        abi=ns.abi,
        use_cache=not ns.no_cache,
        build_source=ns.build_source,
    )
    if ns.json or True:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
