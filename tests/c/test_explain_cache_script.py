from __future__ import annotations

import json
import subprocess
import sys


def test_explain_cache_script_json(tmp_path):
    src = tmp_path / "x.c"
    src.write_text("int x;\n")
    result = subprocess.run(
        [sys.executable, "scripts/pcc_explain_cache.py", "--format=json", str(src)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["version"] == "pcc.c.cache.v1"
