#!/usr/bin/env python3
"""Aggregate CPython 3.15 Tachyon flamegraph HTML across worker processes."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from typing import Any


_DATA_PREFIX = "const EMBEDDED_DATA = "


class TachyonAggregateError(RuntimeError):
    pass


def load_embedded_data(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.startswith(_DATA_PREFIX):
                continue
            payload = line[len(_DATA_PREFIX) :].rstrip()
            if not payload.endswith(";"):
                raise TachyonAggregateError(
                    f"unterminated EMBEDDED_DATA in {path}"
                )
            data = json.loads(payload[:-1])
            if not isinstance(data, dict):
                raise TachyonAggregateError(
                    f"EMBEDDED_DATA is not an object in {path}"
                )
            return data
    raise TachyonAggregateError(f"missing EMBEDDED_DATA in {path}")


def _string(value: object, strings: list[object]) -> str:
    if isinstance(value, int) and 0 <= value < len(strings):
        return str(strings[value])
    return str(value or "")


def _rank(counter: Counter, *, top: int, total: int) -> list[dict[str, Any]]:
    rows = []
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:top]
    for key, samples in ranked:
        row: dict[str, Any] = {
            "samples": samples,
            "percent": (100.0 * samples / total) if total else 0.0,
        }
        if isinstance(key, tuple):
            row.update(
                {
                    "filename": key[0],
                    "lineno": key[1],
                    "function": key[2],
                    "module": key[3],
                }
            )
        else:
            row["name"] = key
        rows.append(row)
    return rows


def aggregate(paths: list[Path], *, top: int = 30) -> dict[str, Any]:
    if not paths:
        raise TachyonAggregateError("no Tachyon HTML inputs")
    functions: Counter = Counter()
    files: Counter = Counter()
    opcodes: Counter = Counter()
    profiles: list[dict[str, Any]] = []
    total_root_samples = 0
    total_self_samples = 0

    for path in sorted(paths):
        data = load_embedded_data(path)
        strings = list(data.get("strings") or [])
        opcode_names = dict(
            ((data.get("opcode_mapping") or {}).get("names") or {})
        )
        root_samples = int(data.get("value") or 0)
        total_root_samples += root_samples
        stats = data.get("stats") or {}
        profiles.append(
            {
                "path": str(path),
                "root_samples": root_samples,
                "duration_s": float(stats.get("duration_sec") or 0.0),
                "sample_rate": float(stats.get("sample_rate") or 0.0),
                "error_rate": float(stats.get("error_rate") or 0.0),
            }
        )

        pending = [data]
        while pending:
            node = pending.pop()
            children = node.get("children") or []
            if isinstance(children, list):
                pending.extend(children)
            self_samples = int(node.get("self") or 0)
            filename = _string(node.get("filename"), strings)
            function = _string(node.get("funcname"), strings)
            module = _string(node.get("module"), strings)
            lineno = int(node.get("lineno") or 0)
            if self_samples:
                key = (filename, lineno, function, module)
                functions[key] += self_samples
                files[filename or "<unknown>"] += self_samples
                total_self_samples += self_samples
            for opcode, samples in (node.get("opcodes") or {}).items():
                name = opcode_names.get(str(opcode), f"<{opcode}>")
                opcodes[str(name)] += int(samples)

    profiles.sort(key=lambda item: (-item["root_samples"], item["path"]))
    return {
        "schema": "pcc.tachyon.aggregate.v1",
        "profile_count": len(profiles),
        "total_root_samples": total_root_samples,
        "total_self_samples": total_self_samples,
        "top_self_functions": _rank(
            functions,
            top=top,
            total=total_self_samples,
        ),
        "top_self_files": _rank(files, top=top, total=total_self_samples),
        "top_frame_opcodes": _rank(
            opcodes,
            top=top,
            total=sum(opcodes.values()),
        ),
        "profiles": profiles,
    }


def _write_json(path: Path, payload: object) -> None:
    if path.exists():
        raise TachyonAggregateError(f"refusing existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _print_rows(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    for row in rows:
        label = row.get("name")
        if label is None:
            label = "{}:{} {}".format(
                row["filename"], row["lineno"], row["function"]
            )
        print(f"{row['samples']:8d} {row['percent']:7.2f}%  {label}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir")
    parser.add_argument("--glob", default="*.html")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top must be positive")
    input_dir = Path(args.input_dir).expanduser().resolve()
    result = aggregate(list(input_dir.glob(args.glob)), top=args.top)
    if args.json_out:
        _write_json(Path(args.json_out).expanduser().absolute(), result)
    print(
        "profiles={} root_samples={} self_samples={}".format(
            result["profile_count"],
            result["total_root_samples"],
            result["total_self_samples"],
        )
    )
    _print_rows("\ntop self functions", result["top_self_functions"])
    _print_rows("\ntop self files", result["top_self_files"])
    _print_rows("\ntop frame opcodes", result["top_frame_opcodes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
