"""The distilled knowledge pages must match the investigations they summarise.

`docs/knowledge/denied-experiments.md` and its siblings are generated from
`docs/investigations/*.md`.  A stale page is worse than none: an agent reads it,
concludes an approach was denied (or was never tried) and acts on a fact that
the investigations no longer support.  Regenerate with
`uv run python scripts/distill_investigations.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "distill_investigations.py"
PAGES = (
    "docs/knowledge/denied-experiments.md",
    "docs/knowledge/confirmed-root-causes.md",
    "docs/knowledge/symptom-routing.md",
)


def test_generated_knowledge_pages_are_not_stale() -> None:
    done = subprocess.run(
        [sys.executable, str(TOOL), "--check"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_page_exists_and_names_its_generator() -> None:
    for relative in PAGES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "scripts/distill_investigations.py" in text, relative
        assert len(text.splitlines()) > 20, relative
