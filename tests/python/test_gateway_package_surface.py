"""Static distribution and public-surface contracts for ``pcc.gateway``.

These tests deliberately distinguish source/package closure from a runtime
claim.  They do not make a host import, wheel file list, or provider manifest
evidence for current-pcc1, no-libpython execution, HTTPS, or nginx replacement.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tomllib

import pcc.gateway as gateway
import pcc.web as web
from pcc.gateway.control import (
    PCC_GATEWAY_CONTROL_ABI_NAME,
    PCC_GATEWAY_CONTROL_ABI_VERSION,
)
from pcc.gateway.server import GatewayServer, NativeVirtualThreadScheduler
from pcc.gateway.tls import (
    PCC_NATIVE_TLS_ABI_VERSION,
    PCC_NATIVE_TLS_ENTRY_SYMBOL,
)
from pcc.py_frontend import pipeline_dependency_closure as closure
from pcc.web.app import App, middleware_next


REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "pcc" / "gateway"
WEB = REPO / "pcc" / "web"
NATIVE_TLS = GATEWAY / "native"


def test_public_facades_are_explicit_unique_and_identity_preserving() -> None:
    assert len(gateway.__all__) == len(set(gateway.__all__))
    assert len(web.__all__) == len(set(web.__all__))
    for name in gateway.__all__:
        assert hasattr(gateway, name), name
    for name in web.__all__:
        assert hasattr(web, name), name

    assert gateway.GatewayServer is GatewayServer
    assert gateway.NativeVirtualThreadScheduler is NativeVirtualThreadScheduler
    assert gateway.PCC_GATEWAY_CONTROL_ABI_NAME == PCC_GATEWAY_CONTROL_ABI_NAME
    assert (
        gateway.PCC_GATEWAY_CONTROL_ABI_VERSION
        == PCC_GATEWAY_CONTROL_ABI_VERSION
    )
    assert gateway.PCC_NATIVE_TLS_ABI_VERSION == PCC_NATIVE_TLS_ABI_VERSION
    assert gateway.PCC_NATIVE_TLS_ENTRY_SYMBOL == PCC_NATIVE_TLS_ENTRY_SYMBOL
    assert web.App is App
    assert web.middleware_next is middleware_next


def test_wheel_lists_reviewed_tls_provider_inputs_without_build_wildcard() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "pcc" in wheel["packages"]
    included = set(wheel["include"])
    assert {
        "pcc/gateway/include/pcc_tls_provider_v1.h",
        "pcc/gateway/native/Makefile",
        "pcc/gateway/native/README.md",
        "pcc/gateway/native/openssl_provider.c",
        "pcc/gateway/native/provider-manifest.json",
    } <= included

    gateway_entries = [
        item for item in included if item.startswith("pcc/gateway/")
    ]
    assert "pcc/gateway/native/**" not in gateway_entries
    assert all("/build/" not in item for item in gateway_entries)
    assert all(
        not item.endswith((".so", ".dylib", ".dll"))
        for item in gateway_entries
    )


def test_tls_manifest_paths_and_public_abi_are_one_packaged_contract() -> None:
    manifest_path = NATIVE_TLS / "provider-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["abi_version"] == gateway.PCC_NATIVE_TLS_ABI_VERSION
    assert manifest["entry_symbol"] == gateway.PCC_NATIVE_TLS_ENTRY_SYMBOL
    for field in ("abi_header", "source", "build_entry"):
        assert (NATIVE_TLS / manifest[field]).resolve().is_file(), field
    assert manifest["distribution"] == {
        "wheel_payload": "reviewed-source-build-inputs-only",
        "prebuilt_shared_library_bundled": False,
        "runtime_library_selection": "explicit-absolute-path",
        "runtime_artifact_digest_authenticated": False,
    }
    assert manifest["runtime_artifact_authentication"] == {
        "algorithm": "sha256",
        "expected_digest_source": "listener configuration",
        "verification_timing": "before dynamic_library_open",
        "pre_open_path_digest_verified": True,
        "reader_owner": "freestanding pcc-Python runtime",
        "stream_buffer_bytes": 32768,
        "default_max_artifact_bytes": 268435456,
        "concurrent_path_replacement_closed": False,
    }
    provider_source = (NATIVE_TLS / manifest["source"]).read_text(
        encoding="utf-8"
    )
    assert manifest["entry_symbol"] in provider_source
    assert manifest["link_boundary"]["libpython"] is False
    assert manifest["link_boundary"]["host_python_interpreter"] is False
    assert manifest["link_boundary"]["zero_libc_claim"] is False


def test_gateway_source_imports_only_compiler_owned_or_native_stdlib_seams() -> None:
    forbidden = {
        "asyncio",
        "gunicorn",
        "http",
        "netty",
        "nginx",
        "socket",
        "ssl",
        "uvicorn",
        "wsgiref",
    }
    pcc_dependencies: set[str] = set()
    stdlib_dependencies: set[str] = set()

    for source_path in sorted((*GATEWAY.glob("*.py"), *WEB.glob("*.py"))):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    root = module.split(".", 1)[0]
                    assert root not in forbidden, (source_path.name, module)
                    if root == "pcc":
                        pcc_dependencies.add(module)
                    else:
                        stdlib_dependencies.add(module)
                continue
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                root = module.split(".", 1)[0]
                assert root not in forbidden, (source_path.name, module)
                if root == "pcc":
                    pcc_dependencies.add(module)
                elif root != "__future__":
                    stdlib_dependencies.add(module)

    assert pcc_dependencies <= {
        "pcc.extern",
        "pcc.gateway",
        "pcc.gateway.buffer",
        "pcc.gateway.models",
        "pcc.gateway.proxy",
        "pcc.gateway.routing",
        "pcc.gateway.server",
        "pcc.py_runtime.py.py_abi_constants",
        "pcc.unsafe",
        "pcc.virtual_thread",
    }
    assert stdlib_dependencies <= {"json", "os", "threading"}
    for module_name in stdlib_dependencies:
        assert (REPO / "pcc" / "py_stdlib" / (module_name + ".py")).is_file()


def test_external_web_application_closes_over_every_gateway_source_module(
    tmp_path: Path,
) -> None:
    application = tmp_path / "app.py"
    application.write_text(
        "from pcc.web import App, Response, get\n"
        "def health(request):\n"
        "    return Response.text('ok')\n"
        "app = App(routes=(get('/health', health),))\n",
        encoding="utf-8",
    )

    paths, modules = closure._collect_relative_module_closure(str(application))
    expected = {
        "pcc.web",
        "pcc.web.app",
        "pcc.web.models",
        "pcc.gateway",
        "pcc.gateway.buffer",
        "pcc.gateway.channel",
        "pcc.gateway.config",
        "pcc.gateway.control",
        "pcc.gateway.dns",
        "pcc.gateway.dns_native",
        "pcc.gateway.http1",
        "pcc.gateway.lifecycle",
        "pcc.gateway.models",
        "pcc.gateway.proxy",
        "pcc.gateway.proxy_http1",
        "pcc.gateway.routing",
        "pcc.gateway.server",
        "pcc.gateway.tls",
    }
    assert expected <= set(modules)
    assert len(paths) == len(modules)
    for source_path, module_name in zip(paths, modules):
        if module_name.startswith(("pcc.gateway", "pcc.web")):
            assert Path(source_path).resolve().is_relative_to(REPO)
