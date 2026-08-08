import hashlib
import json
from pathlib import Path
import subprocess

from source_provenance import (
    artifact_sha256,
    source_identity,
    verify_manifest,
    write_manifest,
)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_source_identity_changes_for_tracked_and_untracked_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    source = repo / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "base")

    head, dirty, clean_digest = source_identity(repo)
    assert len(head) == 40
    assert dirty is False

    source.write_text("value = 2\n", encoding="utf-8")
    _, dirty, tracked_digest = source_identity(repo)
    assert dirty is True
    assert tracked_digest != clean_digest

    extra = repo / "extra.py"
    extra.write_text("extra = True\n", encoding="utf-8")
    _, _, untracked_digest = source_identity(repo)
    assert untracked_digest != tracked_digest


def test_manifest_binds_source_and_compiler_without_source_contents(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    compiler_source = repo / "pcc"
    compiler_source.mkdir()
    (compiler_source / "source.py").write_text(
        "secret_value = 7\n", encoding="utf-8"
    )
    git(repo, "add", "pcc/source.py")
    git(repo, "commit", "-qm", "base")
    compiler = tmp_path / "pcc1"
    compiler.write_bytes(b"native compiler")
    output = tmp_path / "manifest.json"

    write_manifest(repo, compiler, output, "llvm")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "pcc.harness.compiler-source.v1"
    assert payload["compiler_sha256"] == hashlib.sha256(
        b"native compiler"
    ).hexdigest()
    assert payload["python_libpython"] == "off"
    assert "secret_value" not in output.read_text(encoding="utf-8")
    assert artifact_sha256(compiler) == payload["compiler_sha256"]


def test_source_identity_can_exclude_generated_provenance(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    source = repo / "pcc" / "source.py"
    generated = repo / "pcc" / "runtime.provenance.json"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    generated.write_text("first\n", encoding="utf-8")
    git(repo, "add", "pcc")
    git(repo, "commit", "-qm", "base")

    _, _, first = source_identity(
        repo, ("pcc",), ("pcc/runtime.provenance.json",)
    )
    generated.write_text("second\n", encoding="utf-8")
    _, dirty, second = source_identity(
        repo, ("pcc",), ("pcc/runtime.provenance.json",)
    )

    assert dirty is False
    assert second == first


def test_verify_manifest_rejects_changed_source_and_compiler(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    source = repo / "pcc" / "source.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "pcc/source.py")
    git(repo, "commit", "-qm", "base")
    compiler = tmp_path / "pcc1"
    compiler.write_bytes(b"native compiler")
    manifest = tmp_path / "manifest.json"
    write_manifest(repo, compiler, manifest, "llvm")

    verify_manifest(repo, compiler, manifest)
    source.write_text("value = 2\n", encoding="utf-8")
    try:
        verify_manifest(repo, compiler, manifest)
    except RuntimeError as error:
        assert "source digest differs" in str(error)
    else:
        raise AssertionError("changed source must invalidate pcc1")

    source.write_text("value = 1\n", encoding="utf-8")
    compiler.write_bytes(b"different compiler")
    try:
        verify_manifest(repo, compiler, manifest)
    except RuntimeError as error:
        assert "artifact differs" in str(error)
    else:
        raise AssertionError("changed artifact must invalidate pcc1")
