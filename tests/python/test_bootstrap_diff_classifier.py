from __future__ import annotations

from pcc.bootstrap import diff_classifier


def test_bootstrap_diff_classifier_exports_contract_categories():
    assert set(diff_classifier.CATEGORIES) == {
        "semantic",
        "IR-text",
        "class-layout",
        "object-model",
        "backend-nondeterminism",
        "link-metadata",
        "perf-only",
        "diagnostic",
    }


def test_bootstrap_diff_classifier_routes_common_markers():
    cases = {
        "LC_UUID load command changed": "link-metadata",
        "elapsed_ms stage=2 changed": "perf-only",
        "warning: unsupported construct": "diagnostic",
        "%__pcc_Point_layout_1 field offset changed": "class-layout",
        "py_decref owned ref mismatch": "object-model",
        "worker partition changed symbol order": "backend-nondeterminism",
        "define ptr @user_main()": "IR-text",
        "program stdout differs": "semantic",
    }
    for text, expected in cases.items():
        got = diff_classifier.classify_diff_text(text)
        assert got.category == expected
