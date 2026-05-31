import json
import subprocess
import sys


def test_passes_explain_json():
    result = subprocess.run(
        [sys.executable, "scripts/pcc_passes_explain.py", "--format=json"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["schema"] == "pcc.pass_explain.v1"
