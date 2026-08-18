from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "replay_pcc_codegen_worker.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("replay_codegen_worker", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_replay_rewrites_only_owned_outputs_and_restores_stage_env(tmp_path):
    tool = _load_tool()
    compiler = tmp_path / "pcc1"
    compiler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    compiler.chmod(0o755)
    source_result = tmp_path / "old.tsv"
    source_artifacts = tmp_path / "old-artifacts"
    manifest = tmp_path / "source.manifest"
    rows = [
        tool.MANIFEST_SCHEMA,
        str(source_result),
        str(source_artifacts),
        "/frozen/native_exports.json",
        "codegen",
        "/frozen/ast",
        "pcc.__main__",
        "off",
        "on",
        "0",
        "1",
        "subprocess",
        "pcc.backend",
        "0\tpcc.mod\t/frozen/mod.py",
        "1",
        "0",
    ]
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    stage_receipt = tmp_path / "stage.json"
    stage_receipt.write_text(
        json.dumps(
            {
                "environment": {
                    "PCC_RUNTIME_ARCHIVE": "/frozen/runtime.a",
                    "PCC_SOURCE_ROOT": "/frozen/source",
                    "PCC_PY_FRONTEND_JOBS": "auto",
                    "LC_ALL": "C",
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "replay"

    command, environment = tool.prepare_replay(
        compiler=compiler,
        manifest=manifest,
        stage_receipt=stage_receipt,
        output_dir=output,
        native_object=1,
    )

    replay_lines = (output / "worker.manifest").read_text(
        encoding="utf-8"
    ).splitlines()
    assert replay_lines[0] == tool.MANIFEST_SCHEMA
    assert replay_lines[1] == str(output / "worker.tsv")
    assert replay_lines[2] == str(output / "artifacts")
    assert replay_lines[3:] == rows[3:]
    assert environment["PCC_RUNTIME_ARCHIVE"] == "/frozen/runtime.a"
    assert environment["PCC_SOURCE_ROOT"] == "/frozen/source"
    assert environment["PCC_PY_FRONTEND_JOBS"] == "1"
    assert environment["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] == "1"
    assert "LC_ALL" not in environment
    assert command[-2:] == [
        "--pcc-python-multi-codegen-worker",
        str(output / "worker.manifest"),
    ]
    receipt = json.loads((output / "replay.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == tool.REPLAY_SCHEMA
    assert receipt["native_object"] == 1

    with pytest.raises(tool.ReplayError, match="refusing existing"):
        tool.prepare_replay(
            compiler=compiler,
            manifest=manifest,
            stage_receipt=stage_receipt,
            output_dir=output,
            native_object=1,
        )

    override = tmp_path / "selected-native-exports.json"
    override.write_text("{}", encoding="utf-8")
    override_output = tmp_path / "override-replay"
    tool.prepare_replay(
        compiler=compiler,
        manifest=manifest,
        stage_receipt=stage_receipt,
        output_dir=override_output,
        native_object=0,
        exports_path=override,
    )
    override_lines = (override_output / "worker.manifest").read_text(
        encoding="utf-8"
    ).splitlines()
    assert override_lines[3] == str(override.resolve())
    override_receipt = json.loads(
        (override_output / "replay.json").read_text(encoding="utf-8")
    )
    assert override_receipt["exports_override"] == str(override.resolve())

    host_root = tmp_path / "host-source"
    (host_root / "pcc").mkdir(parents=True)
    (host_root / "pcc" / "__main__.py").write_text("pass\n")
    host_output = tmp_path / "host-replay"
    host_command, host_env = tool.prepare_replay(
        compiler=compiler, manifest=manifest, stage_receipt=stage_receipt,
        output_dir=host_output, native_object=1, host_source_root=host_root,
    )
    assert host_command[2:6] == [str(compiler), "-P", "-m", "pcc"]
    assert host_env["PYTHONPATH"] == str(host_root)
    assert host_env["PCC_SOURCE_ROOT"] == str(host_root)
    assert host_env["PCC_DIRECT_INDEXED_SIDECAR"] == "0"
    assert (host_output / "worker.manifest").read_text().splitlines()[3:] == rows[3:]
    host_receipt = json.loads((host_output / "replay.json").read_text())
    assert host_receipt["execution_owner"] == "host-cpython"

    import os
    import subprocess
    import sys

    (host_root / "pcc" / "__init__.py").write_text("marker = 'frozen'\n")
    cwd_package = tmp_path / "cwd" / "pcc"
    cwd_package.mkdir(parents=True)
    (cwd_package / "__init__.py").write_text("marker = 'wrong-cwd'\n")
    imported = subprocess.run(
        [sys.executable, "-P", "-c", "import pcc; print(pcc.marker)"],
        cwd=cwd_package.parent,
        env={**os.environ, "PYTHONPATH": str(host_root)},
        capture_output=True, text=True, timeout=10,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == "frozen\n"
