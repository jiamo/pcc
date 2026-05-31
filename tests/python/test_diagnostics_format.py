from __future__ import annotations

import json

from pcc.diagnostics import (
    Diagnostic,
    DiagnosticBag,
    DiagnosticSeverity,
    DiagnosticSpan,
    emit_diagnostics,
    fallback_diagnostic,
)


def test_diagnostic_json_text_and_error_state():
    diag = Diagnostic(
        code="PCC-PY-IMPORT-001",
        severity=DiagnosticSeverity.ERROR,
        phase="python-import",
        span=DiagnosticSpan("pkg/mod.py", 3, 4, 3, 10),
        message="dynamic import requires fallback",
        notes=("module=importlib",),
        suggested_fix="use a native stdlib port or enable libpython fallback",
        fallback_reason="importlib machinery is not native yet",
        docs="docs/issues/self-host-ergonomics.md",
    )
    bag = DiagnosticBag([diag])
    assert bag.has_errors()
    payload = bag.to_json()
    assert payload["schema"] == "pcc.diagnostics.v1"
    assert payload["diagnostics"][0]["code"] == "PCC-PY-IMPORT-001"
    assert payload["diagnostics"][0]["span"]["file"] == "pkg/mod.py"
    text = bag.format_text()
    assert "PCC-PY-IMPORT-001" in text
    assert "fallback:" in text
    assert "fix:" in text


def test_fallback_diagnostic_and_sarif_output():
    diag = fallback_diagnostic(
        code="PCC-PY-FALLBACK-001",
        phase="codegen",
        message="call lowered through compatibility bridge",
        fallback_reason="callee has unknown native signature",
    )
    assert diag.severity == DiagnosticSeverity.WARNING
    sarif_text = emit_diagnostics([diag], fmt="sarif")
    sarif = json.loads(sarif_text)
    assert sarif["version"] == "2.1.0"
    result = sarif["runs"][0]["results"][0]
    assert result["ruleId"] == "PCC-PY-FALLBACK-001"
    assert result["level"] == "warning"


def test_emit_diagnostics_json_format():
    out = emit_diagnostics([
        Diagnostic(code="PCC-GC-001", phase="gc", message="missing traverse")
    ], fmt="json")
    payload = json.loads(out)
    assert payload["diagnostics"][0]["phase"] == "gc"
