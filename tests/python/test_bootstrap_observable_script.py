from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bootstrap_observable_help_mentions_profile_and_diagnostics():
    result = subprocess.run(
        [sys.executable, str(Path("scripts/pcc_bootstrap_observable.py")), "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--diagnostic-format" in result.stdout
    assert "--profile-json" in result.stdout
