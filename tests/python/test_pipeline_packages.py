"""Installed-package discovery contracts behind the pipeline facade."""

from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_packages


def test_native_extension_abi_names_are_classified_without_package_rules():
    assert pipeline_packages.native_extension_name_uses_cpython_abi(
        "module.cpython-313-darwin.so"
    )
    assert pipeline_packages.native_extension_name_uses_cpython_abi(
        "module.abi3.so"
    )
    assert not pipeline_packages.native_extension_name_uses_cpython_abi(
        "module.pcc-native.so"
    )


def test_package_root_diagnostic_finds_nested_cpython_extension(tmp_path):
    package = tmp_path / "pkg"
    nested = package / "nested"
    nested.mkdir(parents=True)
    extension = nested / "native.cpython-313-darwin.so"
    extension.write_bytes(b"artifact")

    assert pipeline_packages.package_root_no_libpython_diagnostic(str(package)) == (
        "PCC-PKG-004",
        str(extension),
    )


def test_pipeline_facade_reexports_package_discovery_helpers():
    assert pipeline._package_site_roots is pipeline_packages.package_site_roots
    assert (
        pipeline._resolve_pcc_native_extension_path
        is pipeline_packages.resolve_pcc_native_extension_path
    )
    assert (
        pipeline._package_root_no_libpython_diagnostic
        is pipeline_packages.package_root_no_libpython_diagnostic
    )
