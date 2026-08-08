"""Frozen method/host/path routing for ``pcc.gateway`` and ``pcc.web``."""


class RouteConflictError(ValueError):
    pass


class RouteNotFound(LookupError):
    pass


class MethodNotAllowed(LookupError):
    def __init__(self, allowed) -> None:
        super().__init__("method not allowed")
        self.allowed = allowed


class RouteBindingError(ValueError):
    pass


def _normalize_host(host: str) -> str:
    """Strip one optional port while preserving IPv6 host identity."""
    value = host.strip().lower()
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return value
        suffix = value[closing + 1:]
        if suffix and not suffix.startswith(":"):
            return value
        return value[1:closing]
    first_colon = value.find(":")
    if first_colon >= 0 and first_colon == value.rfind(":"):
        return value[:first_colon]
    return value


class Route:
    def __init__(
        self,
        method: str,
        path: str,
        handler,
        name: str = "",
        host: str = "",
        kind: str = "local",
        target: str = "",
        bindings=(),
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.handler = handler
        self.name = name
        self.host = _normalize_host(host)
        self.kind = kind
        self.target = target
        self.segments, self.parameter_names, self.tail_name = _compile_path(path)
        self.bindings = tuple(bindings)
        bound_names = []
        for binding in self.bindings:
            if len(binding) != 2:
                raise ValueError("route binding must be (name, scalar-kind)")
            parameter_name, scalar_kind = binding
            if parameter_name not in self.parameter_names:
                raise ValueError("route binding names an unknown path parameter")
            if parameter_name in bound_names:
                raise ValueError("route binding duplicates a path parameter")
            if scalar_kind not in ("str", "int", "bool"):
                raise ValueError("unsupported route scalar binding: " + scalar_kind)
            bound_names.append(parameter_name)

    def signature(self) -> str:
        shape = []
        for segment in self.segments:
            if segment[0] == 0:
                shape.append(segment[1])
            elif segment[0] == 1:
                shape.append("{}")
            else:
                shape.append("{*}")
        return self.host + "|" + self.method + "|/" + "/".join(shape)


class RouteMatch:
    def __init__(self, route: Route, params) -> None:
        self.route = route
        self.params = params

    def bound_values(self):
        values = []
        for parameter_name, scalar_kind in self.route.bindings:
            raw = self.params[parameter_name]
            if scalar_kind == "str":
                values.append(raw)
            elif scalar_kind == "int":
                if not raw or (raw[0] == "-" and len(raw) == 1):
                    raise RouteBindingError("invalid integer route parameter")
                start = 1 if raw[0] == "-" else 0
                for char in raw[start:]:
                    if char < "0" or char > "9":
                        raise RouteBindingError("invalid integer route parameter")
                values.append(int(raw))
            elif scalar_kind == "bool":
                lowered = raw.lower()
                if lowered in ("1", "true", "yes"):
                    values.append(True)
                elif lowered in ("0", "false", "no"):
                    values.append(False)
                else:
                    raise RouteBindingError("invalid boolean route parameter")
        return values


def _compile_path(pattern: str):
    if not pattern.startswith("/"):
        raise ValueError("route path must start with '/'")
    if "?" in pattern or "#" in pattern:
        raise ValueError("route path must not contain query or fragment")
    raw = pattern.split("/")[1:]
    if raw == [""]:
        raw = []
    segments = []
    names = []
    tail_name = ""
    for index, segment in enumerate(raw):
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1]
            tail = False
            if name.endswith("*"):
                name = name[:-1]
                tail = True
            if not name or name in names:
                raise ValueError("route parameter names must be non-empty and unique")
            for char in name:
                if not (char == "_" or char.isalnum()):
                    raise ValueError("route parameter name is invalid")
            names.append(name)
            if tail:
                if index != len(raw) - 1:
                    raise ValueError("tail parameter must be the last segment")
                tail_name = name
                segments.append((2, name))
            else:
                segments.append((1, name))
        else:
            if not segment or segment == "." or segment == "..":
                raise ValueError("empty and dot route segments are rejected")
            if "{" in segment or "}" in segment:
                raise ValueError("route braces must cover a whole segment")
            segments.append((0, segment))
    return segments, names, tail_name


def split_target(target: str):
    question = target.find("?")
    if question < 0:
        return target, ""
    return target[:question], target[question + 1:]


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError("origin-form path must start with '/'")
    output = []
    for segment in path.split("/")[1:]:
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            if not output:
                raise ValueError("path escapes routing root")
            output.pop()
        else:
            if "\x00" in segment or "\\" in segment:
                raise ValueError("unsafe routing path")
            output.append(segment)
    return "/" + "/".join(output)


def _match_segments(route: Route, path: str):
    normalized = normalize_path(path)
    values = normalized.split("/")[1:]
    if values == [""]:
        values = []
    params = {}
    value_index = 0
    for kind, expected in route.segments:
        if kind == 2:
            params[expected] = "/".join(values[value_index:])
            value_index = len(values)
            break
        if value_index >= len(values):
            return None
        actual = values[value_index]
        if kind == 0:
            if actual != expected:
                return None
        else:
            params[expected] = actual
        value_index += 1
    if value_index != len(values):
        return None
    return params


class Router:
    """An immutable-after-freeze route table with deterministic precedence."""

    def __init__(self, routes=()) -> None:
        self.routes = []
        self.frozen = False
        for route in routes:
            self.add(route)
        self.freeze()

    def add(self, route: Route) -> None:
        if self.frozen:
            raise RuntimeError("route table is frozen")
        signature = route.signature()
        for existing in self.routes:
            if existing.signature() == signature:
                raise RouteConflictError("ambiguous route shape: " + signature)
        self.routes.append(route)

    def freeze(self) -> None:
        # Exact paths precede parameters, which precede tail captures. Longer
        # exact prefixes win within a family; declaration order breaks ties.
        self.routes.sort(
            key=lambda route: (
                sum(segment[0] for segment in route.segments),
                -len(route.segments),
            )
        )
        self.frozen = True

    def match(self, method: str, target: str, host: str = "") -> RouteMatch:
        path, _ = split_target(target)
        wanted_method = method.upper()
        wanted_host = _normalize_host(host)
        allowed = []
        for route in self.routes:
            if route.host and route.host != wanted_host:
                continue
            params = _match_segments(route, path)
            if params is None:
                continue
            if route.method == wanted_method or route.method == "*":
                return RouteMatch(route, params)
            if route.method not in allowed:
                allowed.append(route.method)
        if allowed:
            raise MethodNotAllowed(allowed)
        raise RouteNotFound(target)
