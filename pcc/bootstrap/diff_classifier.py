"""Classify pcc2/pcc3 bootstrap differences into the fixed-point taxonomy.

The classifier is intentionally conservative. It is a triage aid for the
bootstrap contract: every non-identical pcc2/pcc3 artifact should be routed to
one of the documented categories before anyone patches around it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


CATEGORIES = (
    "semantic",
    "IR-text",
    "class-layout",
    "object-model",
    "backend-nondeterminism",
    "link-metadata",
    "perf-only",
    "diagnostic",
)

_CATEGORY_KEYWORDS = (
    (
        "link-metadata",
        (
            "lc_uuid",
            "code signature",
            "codesign",
            "build id",
            "build-id",
            "load command",
            "__linkedit",
            "mach-o",
            "rpath",
        ),
    ),
    (
        "perf-only",
        (
            "elapsed_ms",
            "wall",
            "duration",
            "rss",
            "throughput",
            "benchmark",
            "speedup",
            "pause",
        ),
    ),
    (
        "diagnostic",
        (
            "traceback",
            "warning:",
            "error:",
            "diagnostic",
            "stderr",
            "unsupported",
            "assertionerror",
        ),
    ),
    (
        "class-layout",
        (
            "class layout",
            "field offset",
            "slot offset",
            "pyclassobject",
            "layout_",
            "struct type",
            "%__pcc_",
        ),
    ),
    (
        "object-model",
        (
            "refcount",
            "py_decref",
            "py_incref",
            "borrowed",
            "owned ref",
            "type_tag",
            "pyobjectheader",
            "gc root",
            "weakref",
        ),
    ),
    (
        "backend-nondeterminism",
        (
            "register allocation",
            "instruction order",
            "basic block order",
            "symbol order",
            "nondetermin",
            "thread worker",
            "worker partition",
        ),
    ),
    (
        "IR-text",
        (
            "define ",
            "declare ",
            "attributes #",
            "moduleid",
            "target triple",
            "llvm.",
            "%.owned",
            "phi ",
        ),
    ),
)


@dataclass(frozen=True)
class DiffClassification:
    category: str
    confidence: str
    reason: str


def classify_diff_text(diff_text: str, *, left_path: str = "", right_path: str = "") -> DiffClassification:
    """Classify a textual bootstrap diff into one taxonomy bucket."""
    haystack = "\n".join((left_path, right_path, diff_text)).lower()
    for category, needles in _CATEGORY_KEYWORDS:
        for needle in needles:
            if needle in haystack:
                return DiffClassification(category, "heuristic", f"matched {needle!r}")
    return DiffClassification("semantic", "fallback", "no narrower bootstrap-diff marker matched")


def classify_files(left: str | Path, right: str | Path) -> DiffClassification:
    """Classify two artifact files by path and a bounded byte/text sample."""
    left_path = Path(left)
    right_path = Path(right)
    sample_parts = []
    for path in (left_path, right_path):
        try:
            data = path.read_bytes()[:65536]
        except OSError as exc:
            sample_parts.append(f"diagnostic: could not read {path}: {exc}")
            continue
        sample_parts.append(data.decode("utf-8", errors="replace"))
    return classify_diff_text(
        "\n".join(sample_parts),
        left_path=str(left_path),
        right_path=str(right_path),
    )
