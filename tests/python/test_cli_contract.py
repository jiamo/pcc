from __future__ import annotations

import inspect

from pcc import cli_bootstrap, cli_contract, cli_core, pcc


def test_shared_cli_contract_is_complete_and_self_consistent():
    assert cli_contract.validate_cli_contract() == ()
    assert {row[1] for row in cli_contract.SHARED_CLI_OPTIONS} == {
        "--backend",
        "--python-libpython",
        "--python-library",
        "--emit-llvm",
        "-o",
        "--verbose",
    }


def test_host_and_bootstrap_parsers_consume_shared_choice_owners():
    assert cli_core.BACKEND_CHOICES is cli_contract.BACKEND_CHOICES
    assert cli_bootstrap.BACKEND_CHOICES is cli_contract.BACKEND_CHOICES
    assert (
        cli_core.PYTHON_LIBPYTHON_CHOICES
        is cli_contract.PYTHON_LIBPYTHON_CHOICES
    )
    assert (
        cli_bootstrap.PYTHON_LIBPYTHON_CHOICES
        is cli_contract.PYTHON_LIBPYTHON_CHOICES
    )
    assert cli_core._DEFAULT_EMIT_LL == cli_contract.DEFAULT_EMIT_LL
    assert cli_bootstrap._DEFAULT_EMIT_LL == cli_contract.DEFAULT_EMIT_LL


def test_legacy_click_adapter_consumes_shared_choice_owners():
    source = inspect.getsource(pcc._build_click_main)
    assert "PYTHON_LIBPYTHON_CHOICES" in source
    assert "BACKEND_CHOICES" in source
    assert "DEFAULT_EMIT_LL" in source


def test_every_nonshared_feature_group_has_explicit_surface_delta():
    records = {
        row[0]: (row[1], row[2], row[3])
        for row in cli_contract.INTENDED_CLI_DIVERGENCES
    }
    assert set(records) == {
        "ir_scaffold",
        "diagnostic_profile_fallback_observability",
        "python_module_runner",
        "gpu_backend",
        "c_project_build_options",
        "pcc1_pytest",
    }
    for present, absent, reason in records.values():
        assert set(present).isdisjoint(absent)
        assert set(present) | set(absent) == set(cli_contract.ALL_CLI_SURFACES)
        assert reason
