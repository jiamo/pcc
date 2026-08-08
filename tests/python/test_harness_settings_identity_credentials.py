"""Behavioral port tests for Harness settings, credentials and identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "harness"
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from credentials_runtime import LocalCredentialProvider, credential_ref
from identity_runtime import get_or_create_anonymous_user_id, reset_anonymous_user_id
from settings_runtime import JsonFileSettingsProvider, SettingsConflictError, SettingsSchema


UUID_ONE = "11111111-1111-4111-8111-111111111111"
UUID_TWO = "22222222-2222-4222-8222-222222222222"


def test_settings_layers_revision_mutation_redaction_and_atomic_file(tmp_path: Path) -> None:
    filename = tmp_path / "settings.json"
    provider = JsonFileSettingsProvider(str(filename))
    observed: list[tuple[str, int]] = []
    provider.on_document_updated(lambda namespace, revision: observed.append((namespace, revision)))
    scope = provider.register(
        "models",
        SettingsSchema(
            {"model": "default", "nested": {"timeout": 10}, "token": "default-secret"},
            validate=lambda value: (
                None if value["nested"]["timeout"] > 0 else (_ for _ in ()).throw(ValueError("timeout"))
            ),
            secret_paths=[["token"]],
        ),
        base={"model": "base", "nested": {"retries": 2}},
    )

    scope.update({"nested": {"timeout": 20}, "token": "stored-secret"}, 0)
    assert scope.get() == {
        "model": "base",
        "nested": {"timeout": 20, "retries": 2},
        "token": "stored-secret",
    }
    descriptor = provider.describe(redact=True)[0]
    assert "token" not in descriptor["value"]
    assert "stored-secret" not in json.dumps(descriptor)
    assert descriptor["secrets"] == [{"path": ["token"], "set": True}]
    assert descriptor["revision"] == 1
    assert observed == [("models", 1)]

    scope.mutate([{"op": "unset", "path": ["nested", "timeout"]}], 1)
    assert scope.get()["nested"]["timeout"] == 10
    with pytest.raises(SettingsConflictError) as conflict:
        scope.replace({}, 1)
    assert conflict.value.code == "SETTINGS_CONFLICT"
    assert json.loads(filename.read_text(encoding="utf-8"))["models"] == {
        "nested": {},
        "token": "stored-secret",
    }
    assert os.stat(filename).st_mode & 0o077 == 0


def test_settings_rejects_non_json_and_failed_validation_before_persist(tmp_path: Path) -> None:
    filename = tmp_path / "settings.json"
    provider = JsonFileSettingsProvider(str(filename))
    scope = provider.register(
        "agent",
        SettingsSchema({"limit": 2}, lambda value: (
            None if value["limit"] > 0 else (_ for _ in ()).throw(ValueError("positive"))
        )),
    )

    with pytest.raises(ValueError, match="finite"):
        scope.update({"limit": float("inf")})
    with pytest.raises(ValueError, match="positive"):
        scope.update({"limit": 0})

    assert not filename.exists()
    assert scope.get() == {"limit": 2}


def test_credentials_resolve_per_operation_with_secure_precedence(tmp_path: Path) -> None:
    managed = tmp_path / ".credentials.yaml"
    project = tmp_path / "project.env"
    user = tmp_path / "user.env"
    project.write_text("API_KEY=project\nPROJECT_ONLY=project-only\n", encoding="utf-8")
    user.write_text("API_KEY=user\nUSER_ONLY=user-only\n", encoding="utf-8")
    environment = {"API_KEY": "process"}
    provider = LocalCredentialProvider(str(managed), environment, str(project), str(user))

    assert provider.resolve("API_KEY").source == "env"
    assert provider.describe("API_KEY") == {
        "configured": True,
        "source": "env",
        "writable": False,
    }
    with pytest.raises(RuntimeError, match="shadowed"):
        provider.set("API_KEY", "must-not-write")

    environment.pop("API_KEY")
    provider.set("API_KEY", "managed-secret")
    assert provider.resolve("API_KEY").source == "file"
    assert provider.resolve("API_KEY").value == "managed-secret"
    assert provider.resolve("PROJECT_ONLY").source == "project-env"
    assert provider.resolve("USER_ONLY").source == "user-env"
    assert "managed-secret" not in json.dumps(provider.describe("API_KEY"))
    assert os.stat(managed).st_mode & 0o077 == 0

    provider.unset("API_KEY")
    assert provider.resolve("API_KEY").source == "project-env"


def test_credential_diagnostics_never_include_secret_values(tmp_path: Path) -> None:
    managed = tmp_path / ".credentials.yaml"
    secret = "do-not-leak-this-secret"
    managed.write_text("API_KEY: [" + secret + "]\n", encoding="utf-8")
    os.chmod(managed, 0o600)

    with pytest.raises(ValueError) as caught:
        LocalCredentialProvider(str(managed), {})

    assert secret not in str(caught.value)
    with pytest.raises(ValueError):
        credential_ref("not-a-ref")


def test_anonymous_identity_persists_memoizes_and_resets(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    calls = [UUID_ONE, UUID_TWO]

    def generate() -> str:
        return calls.pop(0)

    first = get_or_create_anonymous_user_id(home, generate)
    second = get_or_create_anonymous_user_id(home, generate)
    assert first == UUID_ONE
    assert second == UUID_ONE
    identity_file = Path(home) / ".anonymous-user-id"
    assert identity_file.read_text(encoding="utf-8") == UUID_ONE + "\n"
    assert os.stat(identity_file).st_mode & 0o077 == 0

    assert reset_anonymous_user_id(home) is True
    assert get_or_create_anonymous_user_id(home, generate) == UUID_TWO


@pytest.mark.integration
def test_current_pcc1_secret_redaction() -> None:
    binary = PROJECT / "build" / "harness-core"
    completed = subprocess.run(
        [str(binary), "--self-check"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "HARNESS_RUNTIME_SELF_CHECK_OK" in completed.stdout
