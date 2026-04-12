from __future__ import annotations

import os


def translation_unit_jobs(default: int = 2) -> int:
    """Avoid nested process-pool oversubscription under pytest-xdist."""
    return 1 if os.environ.get("PYTEST_XDIST_WORKER") else default
