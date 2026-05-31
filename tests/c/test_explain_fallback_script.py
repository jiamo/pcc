from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_explain_fallback_script_json():
    result = subprocess.run(
        [sys.executable, str(Path("scripts/pcc_explain_fallback.py")),
         "--format=json", "numpy"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["count"] == 1
