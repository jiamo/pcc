"""Deterministic source identity for the project-local pcc1 artifact."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PCC_SOURCE_PATHS = ("pcc", "scripts/bootstrap.sh", "pyproject.toml", "uv.lock")
PCC_SOURCE_EXCLUDES = (
    "pcc/py_runtime/libpy_runtime_pcc_py.a.provenance.json",
)


def _git_bytes(repo: Path, arguments) -> bytes:
    return subprocess.check_output(
        ["git", *arguments], cwd=repo, stderr=subprocess.DEVNULL
    )


def _git_pathspecs(pathspecs, excludes):
    values = list(pathspecs)
    for relative in excludes:
        values.append(":(exclude)" + relative)
    return values


def source_identity(repo: Path, pathspecs=(".",), excludes=()):
    """Return HEAD, dirty flag, and a content digest without storing source."""
    head = _git_bytes(repo, ["rev-parse", "HEAD"]).strip().decode("ascii")
    selected = _git_pathspecs(pathspecs, excludes)
    diff = _git_bytes(repo, ["diff", "--binary", "HEAD", "--", *selected])
    untracked_raw = _git_bytes(
        repo,
        [
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *selected,
        ],
    )
    untracked = []
    for raw in untracked_raw.split(b"\0"):
        if raw != b"":
            untracked.append(raw.decode("utf-8"))
    untracked.sort()
    digest = hashlib.sha256()
    digest.update(b"pcc.harness.source.v1\0")
    digest.update(head.encode("ascii"))
    digest.update(b"\0tracked-diff\0")
    digest.update(diff)
    for relative in untracked:
        digest.update(b"\0untracked\0")
        digest.update(relative.encode("utf-8"))
        path = repo / relative
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            digest.update(path.read_bytes())
    return head, bool(diff or untracked), digest.hexdigest()


def artifact_sha256(path: Path) -> str:
    """Hash one published artifact without loading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if chunk == b"":
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(
    repo: Path, compiler: Path, output: Path, backend: str
) -> None:
    """Write the current source/artifact identity as stable JSON."""
    head, dirty, digest = source_identity(
        repo, PCC_SOURCE_PATHS, PCC_SOURCE_EXCLUDES
    )
    payload = {
        "schema": "pcc.harness.compiler-source.v1",
        "head_commit": head,
        "dirty": dirty,
        "source_digest": digest,
        "source_paths": list(PCC_SOURCE_PATHS),
        "source_excludes": list(PCC_SOURCE_EXCLUDES),
        "compiler_sha256": artifact_sha256(compiler),
        "construction_backend": backend,
        "application_backend": "self",
        "python_libpython": "off",
    }
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_manifest(repo: Path, compiler: Path, manifest: Path) -> None:
    """Reject a compiler artifact that is stale or differs from its manifest."""
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema") != "pcc.harness.compiler-source.v1":
        raise RuntimeError("unsupported pcc1 source manifest schema")
    head, dirty, digest = source_identity(
        repo, PCC_SOURCE_PATHS, PCC_SOURCE_EXCLUDES
    )
    if payload.get("head_commit") != head:
        raise RuntimeError("pcc1 HEAD differs from current PCC source")
    if payload.get("dirty") != dirty or payload.get("source_digest") != digest:
        raise RuntimeError(
            "pcc1 source digest differs from current PCC source; "
            "run projects/harness/bootstrap-pcc1.sh"
        )
    if payload.get("compiler_sha256") != artifact_sha256(compiler):
        raise RuntimeError("pcc1 artifact differs from its source manifest")


def main(args) -> int:
    if len(args) == 5 and args[1] == "--verify":
        try:
            verify_manifest(
                Path(args[2]).resolve(),
                Path(args[3]).resolve(),
                Path(args[4]).resolve(),
            )
        except Exception as error:
            print("pcc1 source verification failed: " + str(error))
            return 1
        return 0
    if len(args) != 5:
        print(
            "usage: source_provenance.py REPO PCC1 OUTPUT BACKEND | "
            "source_provenance.py --verify REPO PCC1 MANIFEST"
        )
        return 2
    write_manifest(
        Path(args[1]).resolve(),
        Path(args[2]).resolve(),
        Path(args[3]).resolve(),
        args[4],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
