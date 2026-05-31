"""Profile event recorder for pcc compiler/runtime investigations.

The multi-year roadmap calls for phase timings, subprocess timings, pass
timings, IR-size metrics, allocation counters, and runtime events.  This module
provides the stable JSON schema and a tiny timing API.  It is intentionally
standalone so existing CLI/pipeline code can adopt it phase by phase without a
large dependency cycle.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import time
from typing import Any, Iterator, Optional


@dataclass(frozen=True)
class ProfileEvent:
    name: str
    category: str
    start_ns: int
    duration_ns: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "start_ns": self.start_ns,
            "duration_ns": self.duration_ns,
            "duration_ms": self.duration_ns / 1_000_000.0,
            "metadata": dict(self.metadata),
        }


class ProfileRecorder:
    def __init__(self, *, schema: str = "pcc.profile.v1") -> None:
        self.schema = schema
        self.started_ns = time.perf_counter_ns()
        self.events: list[ProfileEvent] = []
        self.counters: dict[str, int] = {}
        self.metadata: dict[str, Any] = {}

    @contextmanager
    def phase(
        self,
        name: str,
        *,
        category: str = "phase",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Iterator[None]:
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            end = time.perf_counter_ns()
            self.events.append(ProfileEvent(
                name=name,
                category=category,
                start_ns=start - self.started_ns,
                duration_ns=end - start,
                metadata=dict(metadata or {}),
            ))

    def record_event(
        self,
        name: str,
        *,
        category: str,
        duration_ns: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.events.append(ProfileEvent(
            name=name,
            category=category,
            start_ns=time.perf_counter_ns() - self.started_ns,
            duration_ns=duration_ns,
            metadata=dict(metadata or {}),
        ))

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value

    def phase_totals_ms(self) -> dict[str, float]:
        totals: dict[str, int] = {}
        for event in self.events:
            if event.category == "phase":
                totals[event.name] = totals.get(event.name, 0) + event.duration_ns
        return {k: v / 1_000_000.0 for k, v in sorted(totals.items())}

    def top_events(self, limit: int = 10) -> list[dict[str, Any]]:
        ordered = sorted(
            self.events,
            key=lambda e: e.duration_ns,
            reverse=True,
        )
        return [e.to_json() for e in ordered[:limit]]

    def to_json(self) -> dict[str, Any]:
        ended_ns = time.perf_counter_ns()
        return {
            "schema": self.schema,
            "total_ns": ended_ns - self.started_ns,
            "total_ms": (ended_ns - self.started_ns) / 1_000_000.0,
            "metadata": dict(self.metadata),
            "counters": dict(sorted(self.counters.items())),
            "phase_totals_ms": self.phase_totals_ms(),
            "events": [e.to_json() for e in self.events],
            "top_events": self.top_events(),
        }

    def format_json(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True)


def write_profile_json(path: str, recorder: ProfileRecorder) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(recorder.format_json())
        f.write("\n")


def make_subprocess_event(
    argv: list[str],
    *,
    returncode: int,
    duration_ns: int,
    cwd: Optional[str] = None,
) -> ProfileEvent:
    return ProfileEvent(
        name="subprocess",
        category="subprocess",
        start_ns=0,
        duration_ns=duration_ns,
        metadata={
            "argv": list(argv),
            "returncode": returncode,
            "cwd": cwd,
        },
    )
