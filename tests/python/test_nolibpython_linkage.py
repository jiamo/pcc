from __future__ import annotations

import subprocess


def test_verify_nolibpython_script_uses_system_cc_not_clang_specific():
    script = "scripts/verify_nolibpython.sh"
    text = open(script, "r", encoding="utf-8").read()
    assert "${CC:-cc}" in text
    assert "clang" not in text


def test_verify_nolibpython_script_helpful_smoke_exists():
    result = subprocess.run(["bash", "-n", "scripts/verify_nolibpython.sh"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
