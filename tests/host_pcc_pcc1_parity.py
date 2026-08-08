"""Shared contracts for host-pcc versus pcc1 semantic parity gates.

This module deliberately contains no pytest hooks and starts no compiler.  It
owns the small, deterministic interfaces used by the parity tests: manifest
classification, immutable pcc1 receipts, and durable failure reports.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


MANIFEST_SCHEMA = "pcc.host-pcc-pcc1-parity-manifest.v1"
PCC1_RECEIPT_SCHEMA = "pcc.self-host-pcc1-receipt.v1"
REGISTRY_ID = "tests.python.test_self_host_oracle_diff.CASES"

ALLOWED_EXCLUSION_REASONS = frozenset(
    {
        "external_tool_only",
        "host_libpython_only",
        "not_source_program",
        "platform_or_hardware_unavailable",
        "pytest_infrastructure_only",
    }
)


class ParityContractError(ValueError):
    """Raised when a parity manifest, receipt, or report is not trustworthy."""


@dataclass(frozen=True)
class ParityApplicability:
    applicable: tuple[tuple[str, str], ...]
    exclusions: tuple[dict[str, str], ...]
    candidate_names_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_names_sha256(names: Iterable[str]) -> str:
    """Return the canonical discovery-ratchet digest for candidate names."""

    canonical = "".join(name + "\n" for name in sorted(names))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _valid_case_name(name: str) -> bool:
    return bool(name) and all(character.isalnum() or character == "_" for character in name)


def load_applicability_manifest(
    path: Path,
    cases: Sequence[tuple[str, str]],
    *,
    registry_id: str = REGISTRY_ID,
) -> ParityApplicability:
    """Validate and apply the versioned manifest to the current case registry.

    The default is intentionally explicit in the manifest.  A candidate added
    to the registry still changes the count and digest, so it cannot silently
    inherit that default until the manifest is reviewed and updated.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ParityContractError(f"cannot read parity manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ParityContractError("parity manifest root must be an object")
    expected_keys = {
        "schema",
        "registry",
        "default_classification",
        "candidate_count",
        "candidate_names_sha256",
        "exclusions",
    }
    if set(raw) != expected_keys:
        raise ParityContractError(
            "parity manifest keys must be exactly " + ", ".join(sorted(expected_keys))
        )
    if raw["schema"] != MANIFEST_SCHEMA:
        raise ParityContractError(f"unsupported parity manifest schema: {raw['schema']!r}")
    if raw["registry"] != registry_id:
        raise ParityContractError(f"unexpected parity candidate registry: {raw['registry']!r}")
    if raw["default_classification"] != "applicable":
        raise ParityContractError("parity manifest default must be explicit 'applicable'")

    names = [name for name, _source in cases]
    if any(not isinstance(name, str) or not _valid_case_name(name) for name in names):
        raise ParityContractError(
            "every parity candidate must have a filesystem-safe alphanumeric name"
        )
    if len(names) != len(set(names)):
        raise ParityContractError("parity candidate names must be unique")
    actual_digest = candidate_names_sha256(names)
    if raw["candidate_count"] != len(names):
        raise ParityContractError(
            "unclassified parity candidate addition/removal: "
            f"manifest count={raw['candidate_count']!r}, current count={len(names)}"
        )
    if raw["candidate_names_sha256"] != actual_digest:
        raise ParityContractError(
            "unclassified parity candidate addition/removal/rename: "
            f"manifest digest={raw['candidate_names_sha256']!r}, "
            f"current digest={actual_digest!r}"
        )

    exclusions_raw = raw["exclusions"]
    if not isinstance(exclusions_raw, list):
        raise ParityContractError("parity manifest exclusions must be a list")
    exclusions: list[dict[str, str]] = []
    excluded_names: set[str] = set()
    current_names = set(names)
    exclusion_keys = {"case", "reason", "owner", "note"}
    for index, item in enumerate(exclusions_raw):
        if not isinstance(item, dict) or set(item) != exclusion_keys:
            raise ParityContractError(
                f"exclusion {index} keys must be exactly "
                + ", ".join(sorted(exclusion_keys))
            )
        if any(not isinstance(item[key], str) or not item[key] for key in exclusion_keys):
            raise ParityContractError(f"exclusion {index} fields must be non-empty strings")
        name = item["case"]
        if name not in current_names:
            raise ParityContractError(f"stale parity exclusion for unknown case {name!r}")
        if name in excluded_names:
            raise ParityContractError(f"duplicate parity exclusion for {name!r}")
        if item["reason"] not in ALLOWED_EXCLUSION_REASONS:
            raise ParityContractError(
                f"unsupported parity exclusion reason {item['reason']!r} for {name!r}"
            )
        excluded_names.add(name)
        exclusions.append(dict(item))

    applicable = tuple(case for case in cases if case[0] not in excluded_names)
    if not applicable:
        raise ParityContractError("parity manifest must leave at least one applicable case")
    return ParityApplicability(
        applicable=applicable,
        exclusions=tuple(exclusions),
        candidate_names_sha256=actual_digest,
    )


def pcc1_receipt_path(binary: Path) -> Path:
    return binary.with_name(binary.name + ".receipt.json")


def pcc1_receipt_payload(
    binary: Path,
    *,
    source_key: str,
    object_cache_identity: str,
) -> dict[str, Any]:
    if not binary.is_file():
        raise ParityContractError(f"pcc1 binary does not exist: {binary}")
    return {
        "schema": PCC1_RECEIPT_SCHEMA,
        "source_key": source_key,
        "object_cache_identity": object_cache_identity,
        "binary_sha256": _sha256_file(binary),
        "producer": "host-pcc",
        "stage": "pcc0-host-to-pcc1",
        "mode": {
            "backend": "self",
            "ir_scaffold": "on",
            "python_libpython": "off",
        },
    }


def write_pcc1_receipt(
    binary: Path,
    receipt: Path,
    *,
    source_key: str,
    object_cache_identity: str,
) -> dict[str, Any]:
    """Atomically publish a receipt after the binary has been published."""

    payload = pcc1_receipt_payload(
        binary,
        source_key=source_key,
        object_cache_identity=object_cache_identity,
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_name(f".{receipt.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, receipt)
    finally:
        if temporary.exists():
            temporary.unlink()
    return payload


def verify_pcc1_receipt(
    binary: Path,
    receipt: Path,
    *,
    source_key: str,
    object_cache_identity: str,
) -> dict[str, Any]:
    try:
        actual = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ParityContractError(f"cannot read pcc1 receipt {receipt}: {exc}") from exc
    expected = pcc1_receipt_payload(
        binary,
        source_key=source_key,
        object_cache_identity=object_cache_identity,
    )
    if actual != expected:
        raise ParityContractError(
            f"pcc1 receipt does not match current source/artifact: {receipt}"
        )
    if not os.access(binary, os.X_OK):
        raise ParityContractError(f"pcc1 binary is not executable: {binary}")
    return expected


def parity_report_directory(source_key: str) -> Path:
    configured = os.environ.get("PCC_HOST_PCC1_PARITY_REPORT_DIR")
    root = (
        Path(configured).expanduser()
        if configured
        else Path.home()
        / ".cache"
        / "pcc"
        / "test-artifacts"
        / "host-pcc-pcc1-parity"
    )
    return root / source_key[:24]


def write_parity_failure_report(
    *,
    source_key: str,
    case_name: str,
    payload: dict[str, Any],
) -> Path:
    """Write the diagnostic before pytest raises, so watchdogs cannot erase it."""

    if not _valid_case_name(case_name):
        raise ParityContractError(f"unsafe parity report case name: {case_name!r}")
    report_dir = parity_report_directory(source_key)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{case_name}.failure.json"
    document = {
        **payload,
        "schema": "pcc.host-pcc-pcc1-parity-failure.v1",
        "recorded_at_unix_ns": time.time_ns(),
        "source_key": source_key,
        "case": case_name,
    }
    temporary = report.with_name(f".{report.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, report)
    finally:
        if temporary.exists():
            temporary.unlink()
    return report
