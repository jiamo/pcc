#!/usr/bin/env python3
"""Aggregate macOS sample(1) top-of-stack self counts.

The GC/bootstrap perf investigations use repeated ``sample <pid> <seconds>``
captures over pcc bootstrap workers. This helper keeps the parsing and category
accounting reproducible instead of hand-summing volatile text files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_LEADING_COUNT = re.compile(r"^\s*(?P<count>[0-9][0-9,]*)\s+(?P<symbol>\S.*)$")
_TRAILING_COUNT = re.compile(r"^\s*(?P<symbol>.*?)\s+(?P<count>[0-9][0-9,]*)\s*$")
_PARENS_SUFFIX = re.compile(r"\s+\(in\s+[^)]*\).*$")
_PLUS_SUFFIX = re.compile(r"\s+\+\s+[0-9].*$")
_ADDR_SUFFIX = re.compile(r"\s+\[[0-9a-fxA-F]+\].*$")


_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "wait",
        re.compile(
            r"(^|[^A-Za-z0-9_])(__wait4(?:_nocancel)?|wait4|waitpid)"
            r"($|[^A-Za-z0-9_])"
        ),
    ),
    (
        "gc_index",
        re.compile(
            r"\b("
            r"pcc_gc_(object|ptr|frame|forwarding|zpage).*index"
            r"|py_gc_index_"
            r")"
        ),
    ),
    (
        "gc_read_barrier",
        re.compile(r"(pcc_gc_load_ptr|pcc_gc_note_(relocation_read|load))"),
    ),
    ("gc_other", re.compile(r"(pcc_gc_|py_gc_backend|user_py_gc_backend)")),
    (
        "compare_sort",
        re.compile(r"(cmp_threeway|py_obj_sorted|py_obj_compare|py_obj_lt)"),
    ),
    (
        "class_lookup",
        re.compile(r"(py_class_attrs_dict|class_lookup|strs_eq|py_class__)"),
    ),
    ("allocator", re.compile(r"(malloc|calloc|realloc|free|nanov2)")),
)


@dataclass(frozen=True)
class SymbolCount:
    symbol: str
    count: int


def _clean_symbol(symbol: str) -> str:
    symbol = _PARENS_SUFFIX.sub("", symbol)
    symbol = _PLUS_SUFFIX.sub("", symbol)
    symbol = _ADDR_SUFFIX.sub("", symbol)
    return symbol.strip()


def _parse_count_line(line: str) -> SymbolCount | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("-"):
        return None
    if stripped.startswith(("Sort by", "Binary Images", "Call graph", "Sample")):
        return None

    match = _LEADING_COUNT.match(line)
    if match is None:
        match = _TRAILING_COUNT.match(line)
    if match is None:
        return None

    raw_symbol = match.group("symbol")
    symbol = _clean_symbol(raw_symbol)
    if not symbol or symbol.startswith(("Thread_", "DispatchQueue_")):
        return None
    if symbol.endswith(":"):
        return None
    count = int(match.group("count").replace(",", ""))
    return SymbolCount(symbol=symbol, count=count)


def _top_stack_lines(text: str) -> Iterable[str]:
    saw_section = False
    in_section = False
    buffered: list[str] = []
    for line in text.splitlines():
        if "Sort by top of stack" in line:
            saw_section = True
            in_section = True
            continue
        if in_section and line.startswith(
            ("Binary Images:", "Call graph:", "Sample analysis")
        ):
            in_section = False
            continue
        if in_section:
            yield line
        elif not saw_section:
            buffered.append(line)
    if not saw_section:
        yield from buffered


def parse_sample_text(text: str) -> list[SymbolCount]:
    counts: dict[str, int] = {}
    for line in _top_stack_lines(text):
        parsed = _parse_count_line(line)
        if parsed is None:
            continue
        counts[parsed.symbol] = counts.get(parsed.symbol, 0) + parsed.count
    return [
        SymbolCount(symbol=symbol, count=count)
        for symbol, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def classify_symbol(symbol: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(symbol):
            return category
    return "other"


def summarize_counts(counts: Iterable[SymbolCount]) -> dict[str, object]:
    by_symbol: dict[str, int] = {}
    by_category: dict[str, int] = {}
    total = 0
    for item in counts:
        by_symbol[item.symbol] = by_symbol.get(item.symbol, 0) + item.count
        category = classify_symbol(item.symbol)
        by_category[category] = by_category.get(category, 0) + item.count
        total += item.count
    wait = by_category.get("wait", 0)
    sorted_symbols = [
        {
            "symbol": symbol,
            "count": count,
            "category": classify_symbol(symbol),
        }
        for symbol, count in sorted(
            by_symbol.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    return {
        "total_self": total,
        "non_wait_self": total - wait,
        "categories": dict(sorted(by_category.items())),
        "symbols": sorted_symbols,
    }


def _read_inputs(paths: list[str]) -> tuple[str, list[str]]:
    if not paths:
        return sys.stdin.read(), ["<stdin>"]
    chunks: list[str] = []
    labels: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files = sorted(
                child
                for child in path.iterdir()
                if child.is_file() and child.suffix in {"", ".txt", ".sample"}
            )
        else:
            files = [path]
        for file_path in files:
            chunks.append(file_path.read_text(encoding="utf-8", errors="replace"))
            labels.append(str(file_path))
    return "\n".join(chunks), labels


def _print_text(summary: dict[str, object], top: int) -> None:
    total = int(summary["total_self"])
    non_wait = int(summary["non_wait_self"])
    print(f"total_self {total}")
    print(f"non_wait_self {non_wait}")
    print("categories")
    categories = summary["categories"]
    assert isinstance(categories, dict)
    for category, value in sorted(
        categories.items(),
        key=lambda item: (-int(item[1]), str(item[0])),
    ):
        pct_total = (100.0 * int(value) / total) if total else 0.0
        pct_non_wait = (100.0 * int(value) / non_wait) if non_wait else 0.0
        print(f"  {category} {value} {pct_total:.1f}% total {pct_non_wait:.1f}% nonwait")
    print("top_symbols")
    symbols = summary["symbols"]
    assert isinstance(symbols, list)
    for entry in symbols[:top]:
        assert isinstance(entry, dict)
        print(f"  {entry['count']} {entry['category']} {entry['symbol']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate macOS sample(1) top-of-stack self counts."
    )
    parser.add_argument("inputs", nargs="*", help="sample text files or dirs")
    parser.add_argument("--json", action="store_true", help="write JSON summary")
    parser.add_argument("--top", type=int, default=20, help="symbols to print")
    args = parser.parse_args(argv)

    text, labels = _read_inputs(args.inputs)
    summary = summarize_counts(parse_sample_text(text))
    summary["inputs"] = labels
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_text(summary, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
