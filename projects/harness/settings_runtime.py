"""Validated revisioned settings with atomic PCC-owned file persistence."""

import json
import math
import os


def settings_namespace(value: str) -> str:
    """Validate and return a lowercase kebab-case settings namespace."""
    if value == "" or value[0] < "a" or value[0] > "z":
        raise ValueError("settings namespace must start with a lowercase letter")
    i = 1
    while i < len(value):
        char = value[i]
        if not (
            (char >= "a" and char <= "z")
            or (char >= "0" and char <= "9")
            or char == "-"
        ):
            raise ValueError("settings namespace must be lowercase kebab-case")
        i += 1
    return value


class SettingsConflictError(Exception):
    """A write refused because its expected raw-section revision is stale."""

    def __init__(self, namespace: str, expected: int, actual: int) -> None:
        Exception.__init__(
            self,
            "settings namespace "
            + namespace
            + " changed since it was read (expected revision "
            + str(expected)
            + ", now "
            + str(actual)
            + ")",
        )
        self.code = "SETTINGS_CONFLICT"
        self.expected = expected
        self.actual = actual


class SettingsSchema:
    """Defaults, semantic validator and explicit secret field paths."""

    def __init__(self, defaults=None, validate=None, secret_paths=None) -> None:
        self.defaults = {} if defaults is None else clone_json(defaults)
        self.validate = validate
        self.secret_paths = [] if secret_paths is None else clone_paths(secret_paths)
        validate_json(self.defaults)

    def resolve(self, base, user):
        value = clone_json(self.defaults)
        if base is not None:
            value = deep_merge(value, base)
        if user is not None:
            value = deep_merge(value, user)
        if self.validate is not None:
            self.validate(value)
        return value


class SettingsRegistration:
    """Internal owner for one namespace and its observers."""

    def __init__(self, namespace: str, schema: SettingsSchema, base, applies: str) -> None:
        self.namespace = namespace
        self.schema = schema
        self.base = None if base is None else clone_json(base)
        self.applies = applies
        self.value = {}
        self.revision = 0
        self.watchers = []
        self.active = True


class SettingsWatch:
    """Idempotent settings observer disposer."""

    def __init__(self, registration: SettingsRegistration, callback) -> None:
        self.registration = registration
        self.callback = callback
        self.active = True

    def dispose(self) -> None:
        self.active = False


class SettingsScope:
    """Owner-facing handle for one registered namespace."""

    def __init__(self, provider, registration: SettingsRegistration) -> None:
        self.provider = provider
        self.registration = registration

    def get(self):
        self._require_active()
        return clone_json(self.registration.value)

    def watch(self, callback) -> SettingsWatch:
        self._require_active()
        watch = SettingsWatch(self.registration, callback)
        self.registration.watchers.append(watch)
        return watch

    def update(self, patch, expected_revision: int = -1) -> None:
        self._require_active()
        self.provider.update(self.registration.namespace, patch, expected_revision)

    def replace(self, section, expected_revision: int = -1) -> None:
        self._require_active()
        self.provider.replace(self.registration.namespace, section, expected_revision)

    def mutate(self, operations, expected_revision: int = -1) -> None:
        self._require_active()
        self.provider.mutate(self.registration.namespace, operations, expected_revision)

    def dispose(self) -> None:
        if not self.registration.active:
            return
        self.registration.active = False
        i = 0
        while i < len(self.registration.watchers):
            self.registration.watchers[i].dispose()
            i += 1

    def _require_active(self) -> None:
        if not self.registration.active:
            raise RuntimeError("settings scope is disposed: " + self.registration.namespace)


class SettingsProvider:
    """Settings resolution, validation, revisions and observer publication."""

    def __init__(self, document=None, writable: bool = True) -> None:
        self.document = {} if document is None else clone_json(document)
        validate_document(self.document)
        self.writable = writable
        self.registrations = []
        self.document_listeners = []
        self.stopped = False

    def register(
        self,
        namespace: str,
        schema: SettingsSchema,
        base=None,
        applies: str = "live",
    ) -> SettingsScope:
        namespace = settings_namespace(namespace)
        if applies != "live" and applies != "restart":
            raise ValueError("settings applies must be live or restart")
        if self._index_of(namespace) >= 0:
            raise ValueError("settings namespace already registered: " + namespace)
        if base is not None:
            validate_section(base, "settings base")
        user = self.document.get(namespace)
        if user is not None:
            validate_section(user, "stored settings section")
        registration = SettingsRegistration(namespace, schema, base, applies)
        registration.value = schema.resolve(base, user)
        self.registrations.append(registration)
        return SettingsScope(self, registration)

    def get(self, namespace: str):
        index = self._index_of(namespace)
        if index < 0:
            return None
        return clone_json(self.registrations[index].value)

    def describe(self, redact: bool = False):
        descriptors = []
        i = 0
        while i < len(self.registrations):
            registration = self.registrations[i]
            user = self.document.get(registration.namespace)
            value = clone_json(registration.value)
            base = None if registration.base is None else clone_json(registration.base)
            user_copy = None if user is None else clone_json(user)
            secrets = []
            if redact:
                value, value_secrets = redact_secrets(value, registration.schema.secret_paths)
                base, base_secrets = redact_secrets(base, registration.schema.secret_paths)
                user_copy, user_secrets = redact_secrets(user_copy, registration.schema.secret_paths)
                secrets = merge_secret_state(value_secrets, base_secrets, user_secrets)
            descriptors.append(
                {
                    "namespace": registration.namespace,
                    "value": value,
                    "revision": registration.revision,
                    "base": base,
                    "user": user_copy,
                    "applies": registration.applies,
                    "secrets": secrets,
                }
            )
            i += 1
        return descriptors

    def on_document_updated(self, callback) -> None:
        self.document_listeners.append(callback)

    def update(self, namespace: str, patch, expected_revision: int = -1) -> None:
        validate_section(patch, "settings patch")
        current = self.document.get(namespace, {})
        self._write(namespace, deep_merge(current, patch), expected_revision)

    def replace(self, namespace: str, section, expected_revision: int = -1) -> None:
        validate_section(section, "settings section")
        self._write(namespace, clone_json(section), expected_revision)

    def mutate(self, namespace: str, operations, expected_revision: int = -1) -> None:
        current = clone_json(self.document.get(namespace, {}))
        i = 0
        while i < len(operations):
            operation = operations[i]
            if not isinstance(operation, dict):
                raise ValueError("settings mutation must be an object")
            kind = operation.get("op")
            path = operation.get("path")
            if kind != "set" and kind != "unset":
                raise ValueError("settings mutation op must be set or unset")
            if not isinstance(path, list):
                raise ValueError("settings mutation path must be a list")
            current = apply_path_operation(current, kind, path, operation.get("value"))
            i += 1
        self._write(namespace, current, expected_revision)

    def publish(self, document) -> None:
        validate_document(document)
        next_document = clone_json(document)
        i = 0
        while i < len(self.registrations):
            registration = self.registrations[i]
            next_user = next_document.get(registration.namespace)
            if next_user is not None:
                validate_section(next_user, "published settings section")
            try:
                next_value = registration.schema.resolve(registration.base, next_user)
            except Exception:
                i += 1
                continue
            old_user = self.document.get(registration.namespace)
            raw_changed = not deep_equal_json(old_user, next_user)
            value_changed = not deep_equal_json(registration.value, next_value)
            if raw_changed:
                registration.revision += 1
            previous = registration.value
            registration.value = next_value
            if value_changed:
                self._notify_watchers(registration, next_value, previous)
            if raw_changed:
                self._notify_document(registration)
            i += 1
        self.document = next_document

    def stop(self) -> None:
        self.stopped = True
        i = 0
        while i < len(self.registrations):
            self.registrations[i].active = False
            i += 1

    def persist(self, namespace: str, section) -> None:
        """Provider hook called before a validated settings commit."""
        return None

    def _write(self, namespace: str, section, expected_revision: int) -> None:
        if self.stopped:
            raise RuntimeError("settings provider is disposed")
        if not self.writable:
            raise RuntimeError("settings provider is read-only")
        index = self._index_of(namespace)
        if index < 0:
            raise KeyError("settings namespace is not registered: " + namespace)
        registration = self.registrations[index]
        if expected_revision >= 0 and expected_revision != registration.revision:
            raise SettingsConflictError(namespace, expected_revision, registration.revision)
        next_value = registration.schema.resolve(registration.base, section)
        previous = registration.value
        raw_changed = not deep_equal_json(self.document.get(namespace), section)
        value_changed = not deep_equal_json(previous, next_value)
        if not raw_changed:
            return
        self.persist(namespace, section)
        self.document[namespace] = clone_json(section)
        registration.value = next_value
        registration.revision += 1
        if value_changed:
            self._notify_watchers(registration, next_value, previous)
        self._notify_document(registration)

    def _notify_watchers(self, registration, next_value, previous) -> None:
        watchers = registration.watchers.copy()
        i = 0
        while i < len(watchers):
            watch = watchers[i]
            if watch.active:
                try:
                    watch.callback(clone_json(next_value), clone_json(previous))
                except Exception:
                    pass
            i += 1

    def _notify_document(self, registration) -> None:
        listeners = self.document_listeners.copy()
        i = 0
        while i < len(listeners):
            try:
                listeners[i](registration.namespace, registration.revision)
            except Exception:
                pass
            i += 1

    def _index_of(self, namespace: str) -> int:
        i = 0
        while i < len(self.registrations):
            registration = self.registrations[i]
            if registration.active and registration.namespace == namespace:
                return i
            i += 1
        return -1


class JsonFileSettingsProvider(SettingsProvider):
    """One atomically replaced JSON settings document."""

    def __init__(self, filename: str) -> None:
        self.filename = os.path.abspath(filename)
        document = {}
        if os.path.exists(self.filename):
            with open(self.filename, "r", encoding="utf-8") as stream:
                text = stream.read()
            document = {} if text.strip() == "" else json.loads(text)
        SettingsProvider.__init__(self, document, True)

    @property
    def document_path(self) -> str:
        return self.filename

    def prepare_document(self) -> str:
        directory = os.path.dirname(self.filename)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if not os.path.exists(self.filename):
            self._write_document(self.document)
        return self.filename

    def refresh(self) -> None:
        if not os.path.exists(self.filename):
            self.publish({})
            return
        with open(self.filename, "r", encoding="utf-8") as stream:
            text = stream.read()
        self.publish({} if text.strip() == "" else json.loads(text))

    def persist(self, namespace: str, section) -> None:
        next_document = clone_json(self.document)
        next_document[namespace] = clone_json(section)
        self._write_document(next_document)

    def _write_document(self, document) -> None:
        directory = os.path.dirname(self.filename)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        temporary = self.filename + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.filename)


def validate_document(document) -> None:
    validate_section(document, "settings document")
    for namespace in document:
        settings_namespace(namespace)
        validate_section(document[namespace], "settings namespace section")


def validate_section(section, label: str) -> None:
    if not isinstance(section, dict):
        raise ValueError(label + " must be a plain object")
    validate_json(section)


def validate_json(value, path: str = "$", ancestors=None) -> None:
    if ancestors is None:
        ancestors = []
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("settings value at " + path + " must be finite")
        return
    if isinstance(value, list):
        marker = id(value)
        if marker in ancestors:
            raise ValueError("settings value has a cycle at " + path)
        ancestors.append(marker)
        i = 0
        while i < len(value):
            validate_json(value[i], path + "[" + str(i) + "]", ancestors)
            i += 1
        ancestors.pop()
        return
    if isinstance(value, dict):
        marker = id(value)
        if marker in ancestors:
            raise ValueError("settings value has a cycle at " + path)
        ancestors.append(marker)
        for key in value:
            if not isinstance(key, str):
                raise ValueError("settings object key at " + path + " must be a string")
            validate_json(value[key], path + "." + key, ancestors)
        ancestors.pop()
        return
    raise ValueError("settings value at " + path + " is not JSON-compatible")


def clone_json(value):
    if isinstance(value, list):
        result = []
        i = 0
        while i < len(value):
            result.append(clone_json(value[i]))
            i += 1
        return result
    if isinstance(value, dict):
        result = {}
        for key in value:
            result[key] = clone_json(value[key])
        return result
    return value


def deep_merge(base, overlay):
    validate_section(base, "settings merge base")
    validate_section(overlay, "settings merge overlay")
    result = clone_json(base)
    for key in overlay:
        incoming = overlay[key]
        current = result.get(key)
        if isinstance(current, dict) and isinstance(incoming, dict):
            result[key] = deep_merge(current, incoming)
        else:
            result[key] = clone_json(incoming)
    return result


def deep_equal_json(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        if len(left) != len(right):
            return False
        i = 0
        while i < len(left):
            if not deep_equal_json(left[i], right[i]):
                return False
            i += 1
        return True
    if isinstance(left, dict):
        if len(left) != len(right):
            return False
        for key in left:
            if key not in right or not deep_equal_json(left[key], right[key]):
                return False
        return True
    return left == right


def apply_path_operation(section, kind: str, path, value=None):
    result = clone_json(section)
    if len(path) == 0:
        if kind == "unset":
            return {}
        validate_section(value, "settings root mutation value")
        return clone_json(value)
    current = result
    i = 0
    while i < len(path) - 1:
        key = path[i]
        if not isinstance(key, str) or key == "":
            raise ValueError("settings mutation path components must be non-empty strings")
        child = current.get(key)
        if not isinstance(child, dict):
            if kind == "unset":
                return result
            child = {}
            current[key] = child
        current = child
        i += 1
    key = path[len(path) - 1]
    if not isinstance(key, str) or key == "":
        raise ValueError("settings mutation path components must be non-empty strings")
    if kind == "set":
        validate_json(value)
        current[key] = clone_json(value)
    elif key in current:
        current.pop(key)
    return result


def redact_secrets(value, secret_paths):
    result = clone_json(value)
    states = []
    i = 0
    while i < len(secret_paths):
        path = secret_paths[i]
        current = result
        found = True
        j = 0
        while j < len(path) - 1:
            if not isinstance(current, dict) or path[j] not in current:
                found = False
                break
            current = current[path[j]]
            j += 1
        last = path[len(path) - 1] if len(path) > 0 else ""
        is_set = found and isinstance(current, dict) and last in current
        if is_set:
            current.pop(last)
        states.append({"path": path.copy(), "set": is_set})
        i += 1
    return result, states


def merge_secret_state(*groups):
    result = []
    i = 0
    while i < len(groups):
        group = groups[i]
        j = 0
        while j < len(group):
            item = group[j]
            key = ".".join(item["path"])
            found = -1
            k = 0
            while k < len(result):
                if ".".join(result[k]["path"]) == key:
                    found = k
                    break
                k += 1
            if found < 0:
                result.append({"path": item["path"].copy(), "set": item["set"]})
            elif item["set"]:
                result[found]["set"] = True
            j += 1
        i += 1
    return result


def clone_paths(paths):
    result = []
    i = 0
    while i < len(paths):
        path = paths[i]
        if not isinstance(path, list) or len(path) == 0:
            raise ValueError("secret path must be a non-empty string list")
        copied = []
        j = 0
        while j < len(path):
            if not isinstance(path[j], str) or path[j] == "":
                raise ValueError("secret path components must be non-empty strings")
            copied.append(path[j])
            j += 1
        result.append(copied)
        i += 1
    return result
