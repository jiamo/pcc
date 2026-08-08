"""Typed declarative HTTP application framework above ``pcc.gateway``."""

import pcc.virtual_thread as virtual_thread

from pcc.gateway.proxy import ProxySpec, ProxyTransportPlan
from pcc.gateway.routing import (
    MethodNotAllowed,
    Route,
    RouteBindingError,
    RouteNotFound,
    Router,
)
from pcc.gateway.server import GatewayServer

from .models import HttpError, Request, Response

_MIDDLEWARE_CONTINUATION_ERROR = (
    "MiddlewareNext is not callable; use middleware_next(next_call)"
)
_MIDDLEWARE_PROCEED_ERROR = (
    "MiddlewareNext._proceed is internal; use middleware_next(next_call)"
)


class MiddlewareNext:
    def __init__(self, app, request: Request, index: int) -> None:
        self.app = app
        self.request = request
        self.index = index
        self.called = False

    def __call__(self):
        raise RuntimeError(_MIDDLEWARE_CONTINUATION_ERROR)

    def _proceed(self):
        """Reject direct continuation dispatch on host and compiled paths."""

        raise RuntimeError(_MIDDLEWARE_PROCEED_ERROR)


def _middleware_proceed(next_call):
    """Compiler-visible implementation reachable only through the public helper."""

    if not isinstance(next_call, MiddlewareNext):
        raise TypeError("middleware_next requires MiddlewareNext")
    if next_call.called:
        raise RuntimeError("middleware next called more than once")
    next_call.called = True
    return virtual_thread.call(
        next_call.app._dispatch_middleware,
        next_call.request,
        next_call.index,
    )


def middleware_next(next_call):
    """Resume a middleware chain through an explicit may-park edge.

    Middleware should call this helper rather than relying on dynamic
    ``__call__`` dispatch.  Because the helper is a compiled, directly-bound
    function, before/after middleware code remains in the parent continuation
    while a downstream handler parks.
    """
    return _middleware_proceed(next_call)


class App:
    def __init__(
        self,
        routes=(),
        middleware=(),
        startup=(),
        shutdown=(),
        error_handlers=None,
        upstreams=(),
    ) -> None:
        if error_handlers is None:
            error_handlers = {}
        self.routes = tuple(routes)
        self.router = Router(self.routes)
        self.middleware = tuple(middleware)
        self.startup_callbacks = tuple(startup)
        self.shutdown_callbacks = tuple(shutdown)
        if len(self.startup_callbacks) != len(self.shutdown_callbacks):
            raise ValueError(
                "startup and shutdown callbacks must be positional pairs"
            )
        self.error_handlers = dict(error_handlers)
        self.upstreams = {}
        for group in upstreams:
            if group.name in self.upstreams:
                raise ValueError("duplicate upstream group: " + group.name)
            self.upstreams[group.name] = group
        self.started = False

    def startup(self) -> None:
        if self.started:
            raise RuntimeError("application already started")
        completed = 0
        try:
            for callback in self.startup_callbacks:
                callback()
                completed += 1
        except Exception:
            index = completed - 1
            while index >= 0:
                try:
                    self.shutdown_callbacks[index]()
                except Exception:
                    pass
                index -= 1
            # The startup failure remains the primary error.  Rollback runs
            # every completed positional pair even when one cleanup fails.
            raise
        self.started = True

    def shutdown(self) -> None:
        if not self.started:
            return
        first_error = None
        for callback in reversed(self.shutdown_callbacks):
            try:
                callback()
            except Exception as error:
                if first_error is None:
                    first_error = error
        self.started = False
        if first_error is not None:
            raise first_error

    def _route_request(self, request: Request):
        host = request.header("host", "")
        match = self.router.match(request.method, request.target, host)
        request.path_params = match.params
        route = match.route
        if route.kind == "proxy":
            if route.target not in self.upstreams:
                return Response.text("upstream unavailable", 503)
            return ProxyDispatch(route, self.upstreams[route.target], request)
        bound_values = match.bound_values()
        if bound_values:
            result = virtual_thread.call(
                route.handler,
                request,
                *bound_values,
            )
        else:
            result = virtual_thread.call(route.handler, request)
        if isinstance(result, Response):
            return result
        if isinstance(result, bytes):
            return Response.bytes(result)
        if isinstance(result, str):
            return Response.text(result)
        return Response.json(result)

    def _dispatch_middleware(self, request: Request, index: int):
        if index >= len(self.middleware):
            return self._route_request(request)
        next_call = MiddlewareNext(self, request, index + 1)
        return virtual_thread.call(
            self.middleware[index],
            request,
            next_call,
        )

    def dispatch_proxy_head(self, request: Request):
        """Return an early streaming proxy plan when no middleware intervenes.

        Middleware may intentionally replace routing or consume the complete
        body, so that shape stays on the RequestEnd dispatch path.  A direct
        proxy route is policy-only and can safely begin upstream backpressure
        as soon as its request head has been validated.
        """
        if self.middleware:
            return None
        try:
            match = self.router.match(
                request.method,
                request.target,
                request.header("host", ""),
            )
        except (MethodNotAllowed, RouteNotFound, RouteBindingError):
            return None
        route = match.route
        if route.kind != "proxy" or route.target not in self.upstreams:
            return None
        request.path_params = match.params
        return ProxyDispatch(route, self.upstreams[route.target], request)

    def dispatch(self, request: Request):
        try:
            return self._dispatch_middleware(request, 0)
        except MethodNotAllowed as error:
            return Response.text(
                "method not allowed",
                405,
                [("allow", ", ".join(error.allowed))],
            )
        except RouteNotFound:
            return Response.text("not found", 404)
        except RouteBindingError:
            return Response.text("invalid route parameter", 400)
        except HttpError as error:
            if error.status in self.error_handlers:
                return virtual_thread.call(
                    self.error_handlers[error.status],
                    request,
                    error,
                )
            return Response.text(error.detail, error.status)
        except Exception as error:
            if 500 in self.error_handlers:
                return virtual_thread.call(
                    self.error_handlers[500],
                    request,
                    error,
                )
            return Response.text("internal server error", 500)

    def schema(self):
        routes = []
        for route in self.routes:
            routes.append({
                "name": route.name,
                "method": route.method,
                "path": route.path,
                "host": route.host,
                "kind": route.kind,
            })
        return {"pcc_web_schema": 1, "routes": routes}

    def run(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        carrier_count: int = 0,
        config=None,
    ) -> int:
        server = GatewayServer(self, host, port, carrier_count, config)
        return virtual_thread.call(server.run)


class ProxyDispatch(ProxyTransportPlan):
    """A request-scoped proxy plan consumed by the gateway connection loop."""

    def __init__(self, route: Route, upstream, request: Request) -> None:
        self.route = route
        self.upstream = upstream
        self.request = request
        self.spec = route.handler


def route(
    method: str,
    path: str,
    handler,
    name: str = "",
    host: str = "",
    bindings=(),
) -> Route:
    return Route(method, path, handler, name, host, bindings=bindings)


def get(path: str, handler, name: str = "", host: str = "", bindings=()) -> Route:
    return route("GET", path, handler, name, host, bindings)


def post(path: str, handler, name: str = "", host: str = "", bindings=()) -> Route:
    return route("POST", path, handler, name, host, bindings)


def put(path: str, handler, name: str = "", host: str = "", bindings=()) -> Route:
    return route("PUT", path, handler, name, host, bindings)


def delete(path: str, handler, name: str = "", host: str = "", bindings=()) -> Route:
    return route("DELETE", path, handler, name, host, bindings)


def proxy(
    path: str,
    upstream: str,
    method: str = "*",
    name: str = "",
    host: str = "",
    strip_prefix: str = "",
    timeouts=None,
    retry=None,
    trust_forwarded: bool = False,
) -> Route:
    spec = ProxySpec(upstream, strip_prefix, timeouts, retry, trust_forwarded)
    return Route(method, path, spec, name, host, "proxy", upstream)
