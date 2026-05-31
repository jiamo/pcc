"""Package inspection, install manifests, and dry-run shims for pcc planning."""
from __future__ import annotations

__all__ = [
    "execute_build_actions",
    "array_core_report",
    "build_plan_for_artifact",
    "campaign_dashboard",
    "campaign_selection",
    "extension_abi_plan",
    "inspect_artifact",
    "inspect_package",
    "install_package",
    "linkage_report",
    "pip_dry_run_plan",
    "toolchain_report",
    "repository_report",
]


def __getattr__(name: str):
    if name == "inspect_package":
        from .inspect import inspect_package

        return inspect_package
    if name == "build_plan_for_artifact":
        from .build_plan import build_plan_for_artifact

        return build_plan_for_artifact
    if name == "execute_build_actions":
        from .build_exec import execute_build_actions

        return execute_build_actions
    if name == "array_core_report":
        from .array_core import array_core_report

        return array_core_report
    if name == "extension_abi_plan":
        from pcc.capi_surface import extension_abi_plan

        return extension_abi_plan
    if name == "pip_dry_run_plan":
        from .pip_shim import pip_dry_run_plan

        return pip_dry_run_plan
    if name == "install_package":
        from .install import install_package

        return install_package
    if name == "toolchain_report":
        from .toolchain import toolchain_report

        return toolchain_report
    if name == "repository_report":
        from .wheel_repo import repository_report

        return repository_report
    if name == "linkage_report":
        from .linkage import linkage_report

        return linkage_report
    if name == "inspect_artifact":
        from .metadata import inspect_artifact

        return inspect_artifact
    if name == "campaign_dashboard":
        from .campaign import campaign_dashboard

        return campaign_dashboard
    if name == "campaign_selection":
        from .campaign import campaign_selection

        return campaign_selection
    raise AttributeError(name)
