"""Current-pcc1 source-closure contract for first-class gateway components."""

from pathlib import Path

from pcc.py_frontend import pipeline_dependency_closure as closure


REPO = Path(__file__).resolve().parents[2]


def test_gateway_and_web_are_allowlisted_pcc_owned_components() -> None:
    gateway = closure._locate_pcc_owned_component_source("pcc.gateway")
    web = closure._locate_pcc_owned_component_source("pcc.web")
    assert gateway == str(REPO / "pcc" / "gateway" / "__init__.py")
    assert web == str(REPO / "pcc" / "web" / "__init__.py")
    assert closure._locate_pcc_owned_component_source("pcc.backend") is None


def test_external_application_closure_includes_gateway_and_web_sources(tmp_path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        "from pcc.web import App, Response, get\n"
        "def health(request):\n"
        "    return Response.text('ok')\n"
        "app = App(routes=(get('/health', health),))\n",
        encoding="utf-8",
    )
    paths, modules = closure._collect_relative_module_closure(str(source))
    assert "pcc.web" in modules
    assert "pcc.web.app" in modules
    assert "pcc.web.models" in modules
    assert "pcc.gateway" in modules
    assert "pcc.gateway.routing" in modules
    assert len(paths) == len(modules)
