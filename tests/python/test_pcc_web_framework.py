"""Typed declarative pcc.web behavior and current-pcc1 product-shaped gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pcc.gateway.buffer import BufferSegment
from pcc.web.models import BodyStream

from pcc.gateway.proxy import UpstreamEndpoint, UpstreamGroup
from pcc.web import (
    App,
    MiddlewareNext,
    Request,
    Response,
    get,
    middleware_next,
    post,
    proxy,
)
from pcc.web.app import ProxyDispatch
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]


def test_body_stream_retains_views_across_park_boundary_and_releases_once() -> None:
    segment = BufferSegment(8)
    segment.write(b"gateway")
    view = segment.view()
    segment.release()
    body = BodyStream(8)
    body.feed(view)
    view.release()
    body.finish()
    assert body.read_chunk() == b"gateway"
    assert body.read_chunk() is None
    body.close()
    assert segment.released


def test_static_routes_parameters_query_and_typed_responses() -> None:
    def user(request):
        return Response.json({
            "id": request.path_params["id"],
            "tag": request.query["tag"][0],
        })

    app = App(routes=(get("/users/{id}", user, name="user"),))
    response = app.dispatch(Request("GET", "/users/42?tag=pcc", headers=[("host", "x")]))
    assert response.status == 200
    assert response.body == b'{"id":"42","tag":"pcc"}'


def test_explicit_static_scalar_binding_does_not_use_signature_inspection() -> None:
    def item(request, item_id, enabled):
        return Response.json({"id": item_id, "enabled": enabled})

    app = App(routes=(get(
        "/items/{id}/{enabled}",
        item,
        bindings=(("id", "int"), ("enabled", "bool")),
    ),))
    response = app.dispatch(Request("GET", "/items/42/true"))
    assert response.body == b'{"id":42,"enabled":true}'
    invalid = app.dispatch(Request("GET", "/items/not-an-int/true"))
    assert invalid.status == 400


def test_middleware_is_ordered_and_next_is_exactly_once() -> None:
    order = []

    def first(request, next_call):
        order.append("first-before")
        response = middleware_next(next_call)
        order.append("first-after")
        return response

    def second(request, next_call):
        order.append("second-before")
        response = middleware_next(next_call)
        order.append("second-after")
        return response

    app = App(routes=(get("/", lambda request: "ok"),), middleware=(first, second))
    response = app.dispatch(Request("GET", "/", headers=[("host", "x")]))
    assert response.body == b"ok"
    assert order == ["first-before", "second-before", "second-after", "first-after"]


def test_middleware_continuation_has_one_explicit_safe_entrypoint() -> None:
    app = App(routes=(get("/", lambda request: "ok"),))
    request = Request("GET", "/", headers=[("host", "x")])
    next_call = MiddlewareNext(app, request, 0)

    with pytest.raises(
        RuntimeError,
        match=r"^MiddlewareNext is not callable; use middleware_next\(next_call\)$",
    ):
        next_call()
    with pytest.raises(
        RuntimeError,
        match=(
            r"^MiddlewareNext\._proceed is internal; "
            r"use middleware_next\(next_call\)$"
        ),
    ):
        next_call._proceed()

    assert middleware_next(next_call).body == b"ok"
    with pytest.raises(RuntimeError, match="called more than once"):
        middleware_next(next_call)
    with pytest.raises(TypeError, match="requires MiddlewareNext"):
        middleware_next(object())


def test_lifespan_callbacks_are_positional_pairs_and_rollback_all_completed() -> None:
    events = []

    def start_one():
        events.append("start-one")

    def start_two():
        events.append("start-two")
        raise RuntimeError("startup failed")

    def stop_one():
        events.append("stop-one")

    def stop_two():
        events.append("stop-two")

    with pytest.raises(ValueError, match="positional pairs"):
        App(startup=(start_one,), shutdown=())

    app = App(
        startup=(start_one, start_two),
        shutdown=(stop_one, stop_two),
    )
    with pytest.raises(RuntimeError, match="startup failed"):
        app.startup()
    assert events == ["start-one", "start-two", "stop-one"]


def test_deterministic_404_405_and_500_mapping() -> None:
    def broken(request):
        raise RuntimeError("secret")

    app = App(routes=(post("/items", broken),))
    assert app.dispatch(Request("GET", "/missing")).status == 404
    wrong = app.dispatch(Request("GET", "/items"))
    assert wrong.status == 405
    assert wrong.headers == [
        ("allow", "POST"),
        ("content-type", "text/plain; charset=utf-8"),
    ]
    failed = app.dispatch(Request("POST", "/items"))
    assert failed.status == 500
    assert b"secret" not in failed.body


def test_proxy_route_produces_transport_plan_not_host_network_io() -> None:
    group = UpstreamGroup("backend", (UpstreamEndpoint("127.0.0.1", 9000),))
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    dispatch = app.dispatch(Request("GET", "/api/users", headers=[("host", "front")]))
    assert isinstance(dispatch, ProxyDispatch)
    assert dispatch.upstream is group
    assert dispatch.request.path_params == {"path": "users"}


def test_schema_is_derived_from_frozen_route_records() -> None:
    app = App(routes=(get("/health", lambda request: "ok", name="health"),))
    assert app.schema() == {
        "pcc_web_schema": 1,
        "routes": [{
            "name": "health",
            "method": "GET",
            "path": "/health",
            "host": "",
            "kind": "local",
        }],
    }


@pytest.mark.integration
def test_current_pcc1_self_no_libpython_declarative_app(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    """The emitted pcc1, not host pcc, compiles and runs the framework app."""
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the pcc.web product gate")
    source = tmp_path / "web_app.py"
    source.write_text(
        '''import pcc.virtual_thread as virtual_thread
from pcc.web import App, Request, Response, get, middleware_next

def health(request):
    return Response.text("healthy")

def item(request, item_id):
    return Response.text("item=" + str(item_id))

def parked(request):
    virtual_thread.yield_now()
    return Response.text("parked")

def ordered(request, next_call):
    request.context["before"] = "yes"
    try:
        next_call._proceed()
    except RuntimeError as error:
        if str(error) == (
            "MiddlewareNext._proceed is internal; "
            + "use middleware_next(next_call)"
        ):
            request.context["unsafe-rejected"] = "yes"
    response = middleware_next(next_call)
    if (
        request.context["before"] == "yes"
        and request.context["unsafe-rejected"] == "yes"
    ):
        response.headers.append(("x-middleware-after", "yes"))
    return response

app = App(routes=(
    get("/health", health),
    get("/items/{id}", item, bindings=(("id", "int"),)),
    get("/parked", parked),
), middleware=(ordered,))

def application_probe() -> int:
    first = virtual_thread.call(
        app.dispatch, Request("GET", "/health", headers=[("host", "local")])
    )
    second = virtual_thread.call(
        app.dispatch, Request("GET", "/items/42", headers=[("host", "local")])
    )
    parked_response = virtual_thread.call(
        app.dispatch, Request("GET", "/parked", headers=[("host", "local")])
    )
    missing = virtual_thread.call(
        app.dispatch, Request("GET", "/missing", headers=[("host", "local")])
    )
    if first.status != 200 or first.body != b"healthy":
        return 1
    if second.status != 200 or second.body != b"item=42":
        return 2
    if parked_response.body != b"parked":
        return 3
    if parked_response.headers[-1] != ("x-middleware-after", "yes"):
        return 4
    if missing.status != 404:
        return 5
    print("PCC1_WEB_DECLARATIVE_OK")
    return 0

def main() -> int:
    thread = virtual_thread.spawn(application_probe)
    virtual_thread.run(1, 256)
    return virtual_thread.result(thread)

main()
''',
        encoding="utf-8",
    )
    executable = tmp_path / "web_app"
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(executable)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "PCC1_WEB_DECLARATIVE_OK" in ran.stdout
