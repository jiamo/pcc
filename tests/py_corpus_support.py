"""Pure discovery and oracle data for the checked-in Python corpus."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PY_CORPUS_ROOT = Path(__file__).absolute().parent / "py_corpus"


@dataclass(frozen=True)
class PythonCorpusCase:
    name: str
    source_path: Path
    source: str
    expected_stdout: bytes
    expected_status: int


def collect_python_corpus_cases() -> tuple[PythonCorpusCase, ...]:
    if not PY_CORPUS_ROOT.is_dir():
        raise RuntimeError(f"py_corpus not found: {PY_CORPUS_ROOT}")
    cases: list[PythonCorpusCase] = []
    for phase in sorted(PY_CORPUS_ROOT.iterdir()):
        if not phase.is_dir() or not phase.name.startswith("phase"):
            continue
        for case_dir in sorted(phase.iterdir()):
            source = case_dir / "source.py"
            stdout = case_dir / "expected.stdout"
            status = case_dir / "expected.status"
            if not (
                case_dir.is_dir()
                and source.is_file()
                and stdout.is_file()
                and status.is_file()
            ):
                continue
            relative = case_dir.relative_to(PY_CORPUS_ROOT).as_posix()
            cases.append(
                PythonCorpusCase(
                    name=relative.replace("/", "__"),
                    source_path=source,
                    source=source.read_text(encoding="utf-8"),
                    expected_stdout=stdout.read_bytes(),
                    expected_status=int(status.read_text(encoding="utf-8").strip()),
                )
            )
    return tuple(cases)


PYTHON_CORPUS_CASES = collect_python_corpus_cases()
