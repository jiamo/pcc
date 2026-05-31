from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


FORBIDDEN_NEEDLES = (
    "PY_TYPE_NUMPY_ARRAY",
    "py_dealloc_numpy_array",
    "py_numpy.c",
    "numpy.array",
)

FORBIDDEN_TORCH_NEEDLES = (
    "PY_TYPE_TORCH",
    "THPTensor",
    "py_torch.c",
    "torch.tensor",
)

FORBIDDEN_BRANCH_PATTERNS = (
    re.compile(r"\bif\s+package\s*==\s*['\"]numpy['\"]"),
    re.compile(r"\bif\s+package_name\s*==\s*['\"]numpy['\"]"),
    re.compile(r"\bif\s+name\s*==\s*['\"]numpy['\"]"),
)

FORBIDDEN_TORCH_BRANCH_PATTERNS = (
    re.compile(r"\bif\s+package\s*==\s*['\"](?:torch|pytorch)['\"]"),
    re.compile(r"\bif\s+package_name\s*==\s*['\"](?:torch|pytorch)['\"]"),
    re.compile(r"\bif\s+name\s*==\s*['\"](?:torch|pytorch)['\"]"),
    re.compile(r"\bif\s+module\.startswith\(\s*['\"]torch\.['\"]\s*\)"),
    re.compile(r"\bif\s+name\.startswith\(\s*['\"]torch\.['\"]\s*\)"),
)

SCAN_ROOTS = (
    REPO / "pcc" / "py_frontend",
    REPO / "pcc" / "py_runtime",
    REPO / "pcc" / "codegen",
    REPO / "pcc" / "package",
    REPO / "pcc" / "array_core.py",
    REPO / "pcc" / "package_compat.py",
    REPO / "pcc" / "cli_bootstrap.py",
)

TORCH_METADATA_MENTION_ALLOWLIST = {
    Path("pcc/package_compat.py"),
    Path("pcc/package/campaign.py"),
}


def _iter_scanned_files():
    for root in SCAN_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".c", ".h"}:
                yield path


def test_compiler_and_runtime_do_not_special_case_numpy():
    offenders: list[str] = []
    for path in _iter_scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO)
        for needle in FORBIDDEN_NEEDLES:
            if needle in text:
                offenders.append(f"{rel}: contains {needle!r}")
        for pattern in FORBIDDEN_BRANCH_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{rel}: contains NumPy package branch")
    assert not offenders, "\n".join(offenders)


def test_compiler_and_runtime_do_not_special_case_torch():
    offenders: list[str] = []
    for path in _iter_scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO)
        lowered = text.lower()
        if rel not in TORCH_METADATA_MENTION_ALLOWLIST:
            if "pytorch" in lowered:
                offenders.append(f"{rel}: contains 'pytorch'")
            if "torch" in lowered:
                offenders.append(f"{rel}: contains 'torch'")
        for needle in FORBIDDEN_TORCH_NEEDLES:
            if needle in text:
                offenders.append(f"{rel}: contains {needle!r}")
        for pattern in FORBIDDEN_TORCH_BRANCH_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{rel}: contains torch package branch")
    assert not offenders, "\n".join(offenders)
