"""Deterministic LLVM-IR reducer driven by an interestingness command.

This tool is deliberately narrower than llvm-reduce: it reduces textual LLVM
IR at function, basic-block, and instruction granularity. Source-language and
pcc frontend-IR reduction are outside this contract. A candidate is retained
only when one independently timed command returns the configured exit code and
its complete stdout/stderr match the configured output triple.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence


_DEFINE_RE = re.compile(r"^\s*define\b.*?@(?:\"([^\"]+)\"|([^\s(]+))\s*\(")
_LABEL_RE = re.compile(r'^\s*(?:[-A-Za-z$._][-\w$.-]*|"[^"]+"):\s*(?:;.*)?$')


class ReductionError(RuntimeError):
    """Stable user-facing reducer failure."""


@dataclasses.dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    kind: str
    label: str


@dataclasses.dataclass(frozen=True)
class InterestingnessContract:
    command: tuple[str, ...]
    timeout_s: float
    expected_exit: int = 0
    expected_stdout: bytes | None = None
    expected_stderr: bytes | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("interestingness command must not be empty")
        if self.timeout_s <= 0:
            raise ValueError("interestingness timeout must be positive")


@dataclasses.dataclass(frozen=True)
class ReductionResult:
    text: str
    original_bytes: int
    reduced_bytes: int
    attempts: int
    accepted: int
    phase_accepts: tuple[tuple[str, int], ...]


class InterestingnessRunner:
    """Materialize candidates and execute the bounded command contract."""

    def __init__(
        self,
        contract: InterestingnessContract,
        *,
        suffix: str = ".ll",
        cwd: str | os.PathLike[str] | None = None,
    ) -> None:
        self.contract = contract
        self.suffix = suffix
        self.cwd = None if cwd is None else os.fspath(cwd)
        self.attempts = 0

    def _command_for(self, candidate: Path) -> list[str]:
        candidate_text = os.fspath(candidate)
        replaced = False
        command: list[str] = []
        for token in self.contract.command:
            if "{input}" in token:
                replaced = True
                command.append(token.replace("{input}", candidate_text))
            else:
                command.append(token)
        if not replaced:
            command.append(candidate_text)
        return command

    def __call__(self, text: str) -> bool:
        self.attempts += 1
        with tempfile.TemporaryDirectory(prefix="pcc-ir-reduce-") as tmp:
            candidate = Path(tmp) / ("candidate" + self.suffix)
            candidate.write_text(text, encoding="utf-8")
            env = dict(os.environ)
            env["PCC_REDUCE_INPUT"] = os.fspath(candidate)
            try:
                result = subprocess.run(
                    self._command_for(candidate),
                    cwd=self.cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.contract.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False
        if result.returncode != self.contract.expected_exit:
            return False
        if (
            self.contract.expected_stdout is not None
            and result.stdout != self.contract.expected_stdout
        ):
            return False
        if (
            self.contract.expected_stderr is not None
            and result.stderr != self.contract.expected_stderr
        ):
            return False
        return True


def _function_spans(lines: Sequence[str]) -> list[TextSpan]:
    spans: list[TextSpan] = []
    index = 0
    while index < len(lines):
        match = _DEFINE_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        name = match.group(1) or match.group(2) or "<anonymous>"
        end = index + 1
        while end < len(lines) and lines[end].strip() != "}":
            end += 1
        if end >= len(lines):
            raise ReductionError(f"unterminated LLVM function {name!r}")
        spans.append(TextSpan(index, end + 1, "function", name))
        index = end + 1
    return spans


def _block_spans(lines: Sequence[str]) -> list[TextSpan]:
    spans: list[TextSpan] = []
    for function in _function_spans(lines):
        body_start = function.start + 1
        body_end = function.end - 1
        starts = [body_start]
        labels: dict[int, str] = {body_start: "<entry>"}
        for index in range(body_start, body_end):
            if _LABEL_RE.match(lines[index]):
                if index != body_start:
                    starts.append(index)
                labels[index] = lines[index].split(":", 1)[0].strip()
        for offset, start in enumerate(starts):
            end = starts[offset + 1] if offset + 1 < len(starts) else body_end
            if start < end:
                spans.append(
                    TextSpan(
                        start,
                        end,
                        "block",
                        function.label + ":" + labels.get(start, "<entry>"),
                    )
                )
    return spans


def _instruction_spans(lines: Sequence[str]) -> list[TextSpan]:
    spans: list[TextSpan] = []
    for block in _block_spans(lines):
        for index in range(block.start, block.end):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith(";") or _LABEL_RE.match(lines[index]):
                continue
            spans.append(TextSpan(index, index + 1, "instruction", block.label))
    return spans


def _remove_spans(lines: Sequence[str], spans: Iterable[TextSpan]) -> list[str]:
    remove: set[int] = set()
    for span in spans:
        remove.update(range(span.start, span.end))
    return [line for index, line in enumerate(lines) if index not in remove]


def _chunks(items: Sequence[TextSpan], count: int) -> list[list[TextSpan]]:
    if not items:
        return []
    count = max(1, min(count, len(items)))
    width = (len(items) + count - 1) // count
    return [list(items[start : start + width]) for start in range(0, len(items), width)]


def _ddmin_phase(
    text: str,
    spans_for: Callable[[Sequence[str]], list[TextSpan]],
    interesting: Callable[[str], bool],
    *,
    protected: frozenset[str],
) -> tuple[str, int]:
    accepted = 0
    granularity = 2
    while True:
        lines = text.splitlines(keepends=True)
        spans = [span for span in spans_for(lines) if span.label not in protected]
        if not spans:
            return text, accepted
        groups = _chunks(spans, granularity)
        changed = False
        for group in groups:
            candidate = "".join(_remove_spans(lines, group))
            if candidate == text:
                continue
            if interesting(candidate):
                text = candidate
                accepted += 1
                granularity = max(2, granularity - 1)
                changed = True
                break
        if changed:
            continue
        if granularity >= len(spans):
            return text, accepted
        granularity = min(len(spans), granularity * 2)


def reduce_ir_text(
    text: str,
    interesting: Callable[[str], bool],
    *,
    keep_functions: Iterable[str] = (),
) -> ReductionResult:
    """Reduce one already-interesting LLVM IR text deterministically."""

    original_bytes = len(text.encode("utf-8"))
    attempts_before = getattr(interesting, "attempts", 0)
    if not interesting(text):
        raise ReductionError("baseline input does not satisfy interestingness contract")

    protected_functions = frozenset(keep_functions)
    phase_accepts: list[tuple[str, int]] = []
    accepted_total = 0
    for name, spans_for, protected in (
        ("function", _function_spans, protected_functions),
        ("block", _block_spans, frozenset()),
        ("instruction", _instruction_spans, frozenset()),
    ):
        text, accepted = _ddmin_phase(
            text,
            spans_for,
            interesting,
            protected=protected,
        )
        phase_accepts.append((name, accepted))
        accepted_total += accepted
    attempts_after = getattr(interesting, "attempts", attempts_before)
    return ReductionResult(
        text=text,
        original_bytes=original_bytes,
        reduced_bytes=len(text.encode("utf-8")),
        attempts=max(1, attempts_after - attempts_before),
        accepted=accepted_total,
        phase_accepts=tuple(phase_accepts),
    )


def _bytes_arg(value: str | None) -> bytes | None:
    if value is None:
        return None
    return value.encode("utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--expect-exit", type=int, default=0)
    parser.add_argument("--expect-stdout")
    parser.add_argument("--expect-stderr")
    parser.add_argument("--keep-function", action="append", default=[])
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="interestingness command; use {input} or the path is appended",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    contract = InterestingnessContract(
        command=tuple(command),
        timeout_s=args.timeout,
        expected_exit=args.expect_exit,
        expected_stdout=_bytes_arg(args.expect_stdout),
        expected_stderr=_bytes_arg(args.expect_stderr),
    )
    source = args.input.read_text(encoding="utf-8")
    runner = InterestingnessRunner(contract, suffix=args.input.suffix or ".ll")
    result = reduce_ir_text(
        source,
        runner,
        keep_functions=args.keep_function,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.text, encoding="utf-8")
    print(
        "pcc-ir-reduce: "
        f"{result.original_bytes}->{result.reduced_bytes} bytes, "
        f"attempts={result.attempts}, accepted={result.accepted}, "
        f"phases={dict(result.phase_accepts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
