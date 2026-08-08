from pcc.gateway.lifecycle import (
    AdmissionLimits,
    GatewayLifecycle,
    STATE_DRAINING,
    STATE_RUNNING,
    STATE_STOPPED,
)
from pcc.gateway.routing import MethodNotAllowed, Route, RouteConflictError, Router


def _handler(request):
    return request


def test_router_prefers_exact_then_parameter_then_tail() -> None:
    router = Router((
        Route("GET", "/files/{path*}", _handler),
        Route("GET", "/files/{name}", _handler),
        Route("GET", "/files/static", _handler),
    ))
    assert router.match("GET", "/files/static").route.path == "/files/static"
    match = router.match("GET", "/files/readme")
    assert match.params == {"name": "readme"}
    match = router.match("GET", "/files/a/b")
    assert match.params == {"path": "a/b"}


def test_router_reports_405_and_rejects_ambiguous_shapes() -> None:
    router = Router((Route("GET", "/items/{id}", _handler),))
    try:
        router.match("POST", "/items/1")
    except MethodNotAllowed as error:
        assert error.allowed == ["GET"]
    else:
        raise AssertionError("method mismatch did not produce 405 information")
    try:
        Router((
            Route("GET", "/items/{id}", _handler),
            Route("GET", "/items/{name}", _handler),
        ))
    except RouteConflictError:
        return
    raise AssertionError("ambiguous parameter routes were accepted")


def test_router_normalizes_ipv4_dns_and_bracketed_ipv6_host_ports() -> None:
    dns = Route("GET", "/", _handler, host="Example.COM")
    ipv6 = Route("GET", "/v6", _handler, host="[2001:DB8::1]")
    router = Router((dns, ipv6))

    assert router.match("GET", "/", "example.com:8443").route is dns
    assert router.match("GET", "/v6", "[2001:db8::1]:8443").route is ipv6


def test_generation_publish_keeps_old_config_until_connection_releases() -> None:
    lifecycle = GatewayLifecycle({"name": "one"})
    lifecycle.start()
    lifecycle.started()
    assert lifecycle.state == STATE_RUNNING
    held = lifecycle.acquire_generation()
    replacement = lifecycle.publish({"name": "two"})
    assert replacement.generation_id == 2
    assert not held.released
    held.release()
    assert lifecycle.collect_retired() == 1


def test_admission_and_graceful_drain_are_separate_limits() -> None:
    lifecycle = GatewayLifecycle({}, AdmissionLimits(max_connections=1, max_requests=1))
    lifecycle.start()
    lifecycle.started()
    assert lifecycle.admit_connection()
    assert not lifecycle.admit_connection()
    assert lifecycle.admit_request()
    lifecycle.begin_drain()
    assert lifecycle.state == STATE_DRAINING
    assert not lifecycle.finish_drain()
    lifecycle.release_request()
    lifecycle.release_connection()
    assert lifecycle.finish_drain()
    assert lifecycle.state == STATE_STOPPED
