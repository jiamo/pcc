from __future__ import annotations

from pathlib import Path


def test_c_runtime_log_symbols_are_built_and_called():
    root = Path(__file__).absolute().parents[2]
    makefile = (root / "pcc" / "py_runtime" / "Makefile").read_text()
    py_obj = (root / "pcc" / "py_runtime" / "src" / "py_obj.c").read_text()
    internal = (root / "pcc" / "py_runtime" / "src" / "py_internal.h").read_text()
    runtime_log = (root / "pcc" / "py_runtime" / "src" / "pcc_runtime_log.c").read_text()

    assert "$(SRCDIR)/pcc_runtime_log.c" in makefile
    assert "pcc_runtime_log_event" in internal
    assert "pcc_runtime_log_event_code(1, 1" in py_obj
    assert "pcc_runtime_log_event_code(2, 1" in py_obj
    assert 'case 1: return "alloc_request";' in runtime_log
    assert 'case 1: return "collect_start";' in runtime_log
    assert "PCC_LOG_FORMAT" in runtime_log
    assert "pcc.runtime_log.v1" in runtime_log
