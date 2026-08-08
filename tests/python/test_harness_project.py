"""Focused gates for the PCC-native DeepSeek Harness project."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "harness"
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def _load_core():
    spec = importlib.util.spec_from_file_location(
        "pcc_harness_core", PROJECT / "harness_core.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_logs_every_model_visible_turn_value() -> None:
    core = _load_core()
    agent = core.create_default_agent()

    response = agent.run_turn("/tool echo native pcc")

    assert response == "Tool returned: native pcc"
    assert [event.event_type for event in agent.session.events] == [
        "turn/start",
        "user/message",
        "step/start",
        "request/header",
        "assistant/chunk",
        "tool/call",
        "tool/result",
        "step/end",
        "step/start",
        "request/header",
        "assistant/chunk",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    assert agent.session.derive_model_history() == [
        "user: /tool echo native pcc",
        "tool: native pcc",
        "assistant: Tool returned: native pcc",
    ]


def test_host_entrypoint_runs_the_same_self_check() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROJECT / "app.py"), "--self-check"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout == "HARNESS_RUNTIME_SELF_CHECK_OK\n"


def test_project_records_exact_upstream_and_forbids_node_runtime() -> None:
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    instructions = (PROJECT / "AGENTS.md").read_text(encoding="utf-8")
    upstream = (PROJECT / "migration" / "upstream.json").read_text(
        encoding="utf-8"
    )

    assert "47f943859bef60e4160492346772ded9b24f765a" in upstream
    assert "pcc1" in readme
    assert "no libpython" in readme
    assert "Do not introduce Node.js" in instructions
