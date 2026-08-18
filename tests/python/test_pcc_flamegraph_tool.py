from __future__ import annotations

import collections
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pcc_flamegraph.py"


def _sample_output(argv) -> Path:
    return Path(argv[argv.index("-file") + 1])


def _load_tool():
    spec = importlib.util.spec_from_file_location("pcc_flamegraph_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure_main(monkeypatch, tool, tmp_path: Path):
    binary = tmp_path / "pcc1"
    binary.write_bytes(b"native")
    out = tmp_path / "profile.svg"
    folded = tmp_path / "profile.folded"
    monkeypatch.setattr(tool, "_busiest_leaf", lambda _pid: 123)
    monkeypatch.setattr(tool, "_executable", lambda _pid: binary)
    monkeypatch.setattr(tool, "_image_load_address", lambda _text, _image: 4096)
    monkeypatch.setattr(tool, "_text_vmaddr", lambda _binary: 4096)
    monkeypatch.setattr(
        tool,
        "_fold",
        lambda _text, _image, _symbols, _slide, **_kwargs: collections.Counter(
            {"root": 1}
        ),
    )
    monkeypatch.setattr(tool, "_svg", lambda *_args: "<svg/>")
    monkeypatch.setattr(tool, "_run", lambda _argv: "2026-08-21 00:00:00\n")
    monkeypatch.setattr(
        tool.sys,
        "argv",
        [
            str(SCRIPT),
            "cpu",
            "123",
            "1",
            "--folded",
            str(folded),
            "-o",
            str(out),
        ],
    )
    return binary, out, folded


def test_malloc_call_tree_weights_reported_bytes_not_allocation_count():
    tool = _load_tool()
    report = """\
Call graph:
    10 (2.0K) ???  (in pcc1)  load address 0x1000 + 0x0
    + 4 (512) ???  (in pcc1)  load address 0x1000 + 0x10
"""
    symbols = [(0, "root"), (16, "child")]

    counted = tool._fold(report, "pcc1", symbols, 0x1000)
    weighted = tool._fold(
        report,
        "pcc1",
        symbols,
        0x1000,
        allocation_bytes=True,
    )

    assert counted == collections.Counter({"root": 6, "root;child": 4})
    assert weighted == collections.Counter(
        {"root": 1536, "root;child": 512}
    )
    assert tool._allocation_amount_bytes("1 byte") == 1
    assert tool._allocation_amount_bytes("128 bytes") == 128


def test_cpu_capture_precedes_symbol_loading_and_allows_short_lived_target(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    _binary, out, folded = _configure_main(monkeypatch, tool, tmp_path)
    events = []

    def fake_sample(argv, **_kwargs):
        events.append(("sample", tuple(argv)))
        _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_symbols(_binary):
        events.append(("symbols", ()))
        return [(0, "root")]

    monkeypatch.setattr(tool.subprocess, "run", fake_sample)
    monkeypatch.setattr(tool, "_symbols", fake_symbols)

    assert tool.main() == 0
    assert events[0][0] == "sample"
    assert events[1][0] == "symbols"
    sample_argv = events[0][1]
    assert sample_argv == (
        "sample",
        "123",
        "1",
        "-mayDie",
        "-file",
        sample_argv[-1],
    )
    assert out.read_text(encoding="utf-8") == "<svg/>"
    assert folded.read_text(encoding="utf-8") == "root 1\n"


def test_cpu_capture_fails_before_symbol_loading_when_sample_writes_no_report(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    _configure_main(monkeypatch, tool, tmp_path)
    symbol_calls = []
    sample_paths = []

    def failed_sample(argv, **_kwargs):
        sample_paths.append(_sample_output(argv))
        return SimpleNamespace(
            returncode=1, stdout="", stderr="target exited before capture"
        )

    monkeypatch.setattr(
        tool.subprocess,
        "run",
        failed_sample,
    )
    monkeypatch.setattr(
        tool,
        "_symbols",
        lambda _binary: symbol_calls.append(True),
    )

    with pytest.raises(SystemExit, match="sample failed.*exit 1.*target exited"):
        tool.main()
    assert symbol_calls == []
    assert sample_paths and not sample_paths[0].exists()


def test_cpu_capture_accepts_nonzero_sample_with_valid_partial_report(
    monkeypatch, tmp_path, capsys
):
    tool = _load_tool()
    _configure_main(monkeypatch, tool, tmp_path)

    def fake_sample(argv, **_kwargs):
        _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        return SimpleNamespace(
            returncode=1, stdout="", stderr="process exited during sample"
        )

    monkeypatch.setattr(tool.subprocess, "run", fake_sample)
    monkeypatch.setattr(tool, "_symbols", lambda _binary: [(0, "root")])

    assert tool.main() == 0
    assert "partial CPU capture accepted" in capsys.readouterr().err


def test_cpu_capture_rejects_binary_replacement_before_loading_symbols(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    binary, _out, _folded = _configure_main(monkeypatch, tool, tmp_path)
    symbol_calls = []

    def fake_sample(argv, **_kwargs):
        _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        binary.write_bytes(b"replacement executable")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tool.subprocess, "run", fake_sample)
    monkeypatch.setattr(
        tool,
        "_symbols",
        lambda _binary: symbol_calls.append(True),
    )

    with pytest.raises(SystemExit, match="executable changed while capturing"):
        tool.main()
    assert symbol_calls == []


def test_cpu_partial_report_without_target_image_fails_before_symbols(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    _binary, out, folded = _configure_main(monkeypatch, tool, tmp_path)
    symbol_calls = []

    def fake_sample(argv, **_kwargs):
        _sample_output(argv).write_text("wrong image", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="target exited")

    monkeypatch.setattr(tool.subprocess, "run", fake_sample)
    monkeypatch.setattr(tool, "_image_load_address", lambda _text, _image: None)
    monkeypatch.setattr(tool, "_symbols", lambda _binary: symbol_calls.append(True))

    with pytest.raises(SystemExit, match="no image named 'pcc1'"):
        tool.main()
    assert symbol_calls == []
    assert not out.exists() and not folded.exists()


def test_cpu_partial_report_with_no_folded_stacks_is_rejected(monkeypatch, tmp_path):
    tool = _load_tool()
    _binary, out, folded = _configure_main(monkeypatch, tool, tmp_path)

    def fake_sample(argv, **_kwargs):
        _sample_output(argv).write_text("image but no stacks", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="target exited")

    monkeypatch.setattr(tool.subprocess, "run", fake_sample)
    monkeypatch.setattr(tool, "_symbols", lambda _binary: [(0, "root")])
    monkeypatch.setattr(
        tool,
        "_fold",
        lambda *_args, **_kwargs: collections.Counter(),
    )

    with pytest.raises(SystemExit, match="no stacks collected"):
        tool.main()
    assert not out.exists() and not folded.exists()


@pytest.mark.parametrize("changed_field", range(5))
def test_cpu_capture_rejects_each_binary_identity_change(
    monkeypatch, tmp_path, changed_field
):
    tool = _load_tool()
    _configure_main(monkeypatch, tool, tmp_path)
    before = (1, 2, 3, 4, 5)
    changed = list(before)
    changed[changed_field] += 1
    identities = iter((before, tuple(changed)))

    def fake_sample(argv, **_kwargs):
        _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tool, "_binary_identity", lambda _path: next(identities))
    monkeypatch.setattr(tool.subprocess, "run", fake_sample)

    with pytest.raises(SystemExit, match="executable changed while capturing"):
        tool.main()


@pytest.mark.parametrize(
    ("mode", "extra"),
    (("heap", ()), ("peak", ("-highWaterMark",))),
)
def test_allocation_modes_still_capture_before_symbol_loading(
    monkeypatch, tmp_path, mode, extra
):
    tool = _load_tool()
    _binary, out, _folded = _configure_main(monkeypatch, tool, tmp_path)
    events = []
    monkeypatch.setattr(
        tool.sys,
        "argv",
        [str(SCRIPT), mode, "123", "-o", str(out)],
    )

    def fake_history(argv, **_kwargs):
        events.append(("capture", tuple(argv)))
        return SimpleNamespace(returncode=0, stdout="allocation tree", stderr="")

    def fake_symbols(_binary):
        events.append(("symbols", ()))
        return [(0, "root")]

    monkeypatch.setattr(tool.subprocess, "run", fake_history)
    monkeypatch.setattr(tool, "_symbols", fake_symbols)

    assert tool.main() == 0
    assert events[0] == (
        "capture",
        ("malloc_history", "123", "-callTree", "-collapseRecursion", *extra),
    )
    assert events[1][0] == "symbols"


def test_host_mode_dispatches_without_native_process_discovery(monkeypatch):
    tool = _load_tool()
    monkeypatch.setattr(
        tool.sys,
        "argv",
        [str(SCRIPT), "host", "--argv", "--", "probe.py"],
    )
    monkeypatch.setattr(tool, "_host_main", lambda args: 7 if args.argv else 8)
    monkeypatch.setattr(
        tool,
        "_busiest_leaf",
        lambda _pid: (_ for _ in ()).throw(AssertionError("native path used")),
    )

    assert tool.main() == 7


@pytest.mark.parametrize("mode", ("cpu", "heap", "peak"))
def test_exact_pid_profiles_coordinator_without_following_its_child(
    monkeypatch, tmp_path, mode
):
    tool = _load_tool()
    binary, out, _folded = _configure_main(monkeypatch, tool, tmp_path)
    monkeypatch.setattr(tool.sys, "argv", [str(SCRIPT), mode, "123", "1", "--exact-pid", "-o", str(out)])
    observed = []

    def no_child_selection(_pid):
        raise AssertionError("explicit coordinator was replaced by a child")

    def executable(pid):
        observed.append(("executable", pid))
        return binary

    def capture(argv, **_kwargs):
        observed.append(("capture", argv[1]))
        if mode == "cpu":
            _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="allocation tree", stderr="")

    monkeypatch.setattr(tool, "_busiest_leaf", no_child_selection)
    monkeypatch.setattr(tool, "_executable", executable)
    monkeypatch.setattr(tool.subprocess, "run", capture)
    monkeypatch.setattr(tool, "_symbols", lambda _binary: [(0, "root")])
    assert tool.main() == 0
    assert observed == [("executable", 123), ("capture", "123")]


def test_default_still_follows_busiest_child(monkeypatch, tmp_path):
    tool = _load_tool()
    binary, _out, _folded = _configure_main(monkeypatch, tool, tmp_path)
    observed = []
    monkeypatch.setattr(tool, "_busiest_leaf", lambda _pid: 456)
    monkeypatch.setattr(tool, "_executable", lambda pid: observed.append(pid) or binary)
    monkeypatch.setattr(tool, "_symbols", lambda _binary: [(0, "root")])

    def capture(argv, **_kwargs):
        observed.append(argv[1])
        _sample_output(argv).write_text("captured call tree", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tool.subprocess, "run", capture)
    assert tool.main() == 0
    assert observed == [456, "456"]


@pytest.mark.parametrize("argv", (("host", "--exact-pid"), ("cpu", "0", "--exact-pid")))
def test_exact_pid_rejects_non_native_or_missing_target(monkeypatch, argv):
    tool = _load_tool()
    monkeypatch.setattr(tool.sys, "argv", [str(SCRIPT), *argv])
    monkeypatch.setattr(tool, "_busiest_leaf", lambda _pid: pytest.fail("discovered invalid target"))
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 2
