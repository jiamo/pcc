"""Local pcc-native wheel/artifact repository manifest.

This is package-agnostic repository bookkeeping for downstream ecosystem work.
It scans local artifacts, records pcc-native compatibility signals, and keeps
the no-libpython linkage claim explicit at the repository boundary.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .linkage import linkage_report
from .metadata import current_platform_tag, inspect_artifact, pcc_native_wheel_tag


_ARTIFACT_SUFFIXES = (
    ".whl",
    ".zip",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
)


def _is_repository_artifact(path: Path) -> bool:
    lower = path.name.lower()
    return path.is_file() and any(lower.endswith(suffix) for suffix in _ARTIFACT_SUFFIXES)


def _iter_repository_artifacts(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    if _is_repository_artifact(root):
        return (root,)
    return tuple(
        path for path in sorted(root.rglob("*"))
        if _is_repository_artifact(path)
    )


def _copy_artifacts(root: Path, artifacts: list[str] | tuple[str, ...]) -> list[str]:
    copied: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for artifact_text in artifacts:
        source = Path(artifact_text).expanduser().resolve()
        if not source.is_file():
            continue
        dest = root / source.name
        if source != dest:
            shutil.copy2(source, dest)
        copied.append(str(dest))
    return copied


def _compatible_reason(metadata: dict[str, object]) -> tuple[bool, str]:
    source_kind = str(metadata.get("source_kind") or "")
    python_tag = metadata.get("python_tag")
    abi_tag = metadata.get("abi_tag")
    platform_tag = metadata.get("platform_tag")
    current = current_platform_tag()
    if source_kind == "sdist":
        return True, "source_artifact"
    if source_kind != "wheel":
        return False, "unsupported_artifact_kind"
    if python_tag == "py3" and abi_tag == "none" and platform_tag == "any":
        return True, "pure_python_wheel"
    if (
        python_tag == f"pcc{sys.version_info.major}"
        and abi_tag == "pcc_native"
        and platform_tag == current
    ):
        return True, "pcc_native_wheel"
    return False, "wheel_tag_not_pcc_native_compatible"


def repository_report(
    root: str | Path,
    *,
    add_artifacts: list[str] | tuple[str, ...] = (),
    abi_mode: str = "pcc-native",
    write_manifest: bool = False,
) -> dict[str, object]:
    repo_root = Path(root).expanduser().resolve()
    copied = _copy_artifacts(repo_root, add_artifacts) if add_artifacts else []
    artifacts = _iter_repository_artifacts(repo_root)
    rows: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    for artifact in artifacts:
        metadata = inspect_artifact("", artifact).as_dict()
        compatible, reason = _compatible_reason(metadata)
        linkage = linkage_report(artifacts=[str(artifact)], abi_mode=abi_mode)
        row_diagnostics: list[dict[str, object]] = []
        if not compatible:
            row_diagnostics.append(
                {
                    "code": "PCC-REPO-TAG-INCOMPATIBLE",
                    "message": "artifact wheel tag is not compatible with pcc-native repository policy",
                    "reason": reason,
                }
            )
        row_diagnostics.extend(linkage["diagnostics"])  # type: ignore[arg-type]
        diagnostics.extend(row_diagnostics)
        rows.append(
            {
                "name": metadata["name"],
                "path": str(artifact),
                "source_kind": metadata["source_kind"],
                "python_tag": metadata["python_tag"],
                "abi_tag": metadata["abi_tag"],
                "platform_tag": metadata["platform_tag"],
                "pcc_native_compatible": compatible,
                "compatibility_reason": reason,
                "links_libpython": linkage["links_libpython"],
                "no_libpython_runtime": linkage["no_libpython_runtime"],
                "linkage": linkage,
                "diagnostics": row_diagnostics,
            }
        )
    ok = all(row["pcc_native_compatible"] for row in rows) and all(
        bool(row["linkage"]["ok"]) for row in rows  # type: ignore[index]
    )
    report = {
        "schema": "pcc.wheel-repository.v1",
        "ok": ok,
        "root": str(repo_root),
        "artifact_count": len(rows),
        "copied_artifacts": copied,
        "pcc_native_wheel_tag": pcc_native_wheel_tag(),
        "current_platform_tag": current_platform_tag(),
        "artifacts": rows,
        "diagnostics": diagnostics,
    }
    if write_manifest:
        repo_root.mkdir(parents=True, exist_ok=True)
        manifest = repo_root / "pcc-wheel-repository.json"
        manifest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        report["manifest_path"] = str(manifest)
    else:
        report["manifest_path"] = None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc.package wheel-repo")
    parser.add_argument("--root", required=True)
    parser.add_argument("--add", action="append", default=[])
    parser.add_argument("--abi", dest="abi_mode", default="pcc-native")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    report = repository_report(
        ns.root,
        add_artifacts=ns.add,
        abi_mode=ns.abi_mode,
        write_manifest=ns.write_manifest,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
