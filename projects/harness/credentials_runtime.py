"""Credential references and secure local provider layering."""

import json
import os


def credential_ref(value: str) -> str:
    """Validate and return a POSIX environment-variable identifier."""
    if value == "":
        raise ValueError("credential reference must not be empty")
    first = value[0]
    if not (first == "_" or first.isalpha()):
        raise ValueError("credential reference must be a POSIX identifier")
    i = 1
    while i < len(value):
        char = value[i]
        if not (char == "_" or char.isalpha() or char.isdigit()):
            raise ValueError("credential reference must be a POSIX identifier")
        i += 1
    return value


class ResolvedCredential:
    """One secret value and the provider layer that supplied it."""

    def __init__(self, value: str, source: str) -> None:
        self.value = value
        self.source = source


class MemoryCredentialProvider:
    """In-memory credential provider for keyless compositions and tests."""

    def __init__(self) -> None:
        self.values = {}
        self.listeners = []

    def resolve(self, ref: str):
        ref = credential_ref(ref)
        value = self.values.get(ref)
        if value is None or value == "":
            return None
        return ResolvedCredential(value, "memory")

    def describe(self, ref: str):
        resolved = self.resolve(ref)
        if resolved is None:
            return {"configured": False, "writable": True}
        return {"configured": True, "source": "memory", "writable": True}

    def set(self, ref: str, value: str) -> None:
        ref = credential_ref(ref)
        if value == "":
            raise ValueError("an empty credential cannot be stored; use unset")
        self.values[ref] = value
        self._notify(ref)

    def unset(self, ref: str) -> None:
        ref = credential_ref(ref)
        if ref not in self.values:
            return
        self.values.pop(ref)
        self._notify(ref)

    def on_updated(self, callback) -> None:
        self.listeners.append(callback)

    def _notify(self, ref: str) -> None:
        listeners = self.listeners.copy()
        i = 0
        while i < len(listeners):
            try:
                listeners[i](ref)
            except Exception:
                pass
            i += 1


class LocalCredentialProvider(MemoryCredentialProvider):
    """Environment and dotenv layers over one owner-only managed YAML file."""

    def __init__(
        self,
        filename: str,
        process_environment=None,
        project_env_path: str = "",
        user_env_path: str = "",
    ) -> None:
        MemoryCredentialProvider.__init__(self)
        self.filename = os.path.abspath(filename)
        self.process_environment = os.environ if process_environment is None else process_environment
        self.project_environment = load_dotenv(project_env_path)
        self.user_environment = load_dotenv(user_env_path)
        self._reload_managed()

    def resolve(self, ref: str):
        ref = credential_ref(ref)
        inherited = self.process_environment.get(ref)
        if isinstance(inherited, str) and inherited != "":
            return ResolvedCredential(inherited, "env")
        stored = self.values.get(ref)
        if stored is not None and stored != "":
            return ResolvedCredential(stored, "file")
        project = self.project_environment.get(ref)
        if project is not None and project != "":
            return ResolvedCredential(project, "project-env")
        user = self.user_environment.get(ref)
        if user is not None and user != "":
            return ResolvedCredential(user, "user-env")
        return None

    def describe(self, ref: str):
        ref = credential_ref(ref)
        resolved = self.resolve(ref)
        if resolved is None:
            return {"configured": False, "writable": True}
        return {
            "configured": True,
            "source": resolved.source,
            "writable": resolved.source != "env",
        }

    def set(self, ref: str, value: str) -> None:
        ref = credential_ref(ref)
        if value == "":
            raise ValueError("an empty credential cannot be stored; use unset")
        self._require_unshadowed(ref, "set")
        self._reload_managed()
        self.values[ref] = value
        self._persist()
        self._notify(ref)

    def unset(self, ref: str) -> None:
        ref = credential_ref(ref)
        self._require_unshadowed(ref, "unset")
        self._reload_managed()
        if ref not in self.values:
            return
        self.values.pop(ref)
        self._persist()
        self._notify(ref)

    def refresh(self) -> None:
        previous = self.values.copy()
        self._reload_managed()
        names = []
        for key in previous:
            names.append(key)
        for key in self.values:
            if key not in previous:
                names.append(key)
        i = 0
        while i < len(names):
            name = names[i]
            if previous.get(name) != self.values.get(name):
                self._notify(name)
            i += 1

    def _require_unshadowed(self, ref: str, verb: str) -> None:
        inherited = self.process_environment.get(ref)
        if isinstance(inherited, str) and inherited != "":
            raise RuntimeError(
                "credential reference "
                + ref
                + " is supplied read-only by the launching environment; "
                + verb
                + " would be shadowed"
            )

    def _reload_managed(self) -> None:
        if not os.path.exists(self.filename):
            self.values = {}
            return
        mode = os.stat(self.filename).st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(
                "credentials file is readable beyond its owner (mode "
                + format(mode, "o")
                + "); run chmod 600 before starting"
            )
        with open(self.filename, "r", encoding="utf-8") as stream:
            text = stream.read()
        self.values = parse_credentials_document(text, self.filename)

    def _persist(self) -> None:
        directory = os.path.dirname(self.filename)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        temporary = self.filename + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            for key in self.values:
                stream.write(key + ": " + json.dumps(self.values[key], ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.filename)


def parse_credentials_document(text: str, filename: str = "credentials"):
    """Parse a strict flat YAML credential mapping without leaking values."""
    values = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped != "" and not stripped.startswith("#"):
            if line.startswith(" ") or line.startswith("\t") or ":" not in line:
                raise ValueError(
                    "invalid credentials document at " + filename + " line " + str(i + 1)
                )
            key, raw = line.split(":", 1)
            key = credential_ref(key.strip())
            if key in values:
                raise ValueError(
                    "duplicate credential reference " + key + " at " + filename
                )
            raw = raw.strip()
            if raw == "":
                raise ValueError("credential value for " + key + " is empty")
            try:
                if raw.startswith('"'):
                    value = json.loads(raw)
                elif raw.startswith("'") and raw.endswith("'"):
                    value = raw[1:-1].replace("''", "'")
                else:
                    lowered = raw.lower()
                    if (
                        raw[0] in "[{&*!|>@`"
                        or lowered in ["null", "~", "true", "false"]
                        or raw[0].isdigit()
                        or raw[0] == "+"
                        or raw[0] == "-"
                    ):
                        raise ValueError("credential scalar is not a string")
                    value = raw
            except Exception as error:
                raise ValueError(
                    "invalid credential scalar for " + key + " at " + filename
                ) from error
            if not isinstance(value, str) or value == "":
                raise ValueError("credential value for " + key + " must be a non-empty string")
            values[key] = value
        i += 1
    return values


def load_dotenv(filename: str):
    """Load a small deterministic dotenv subset; absence is an empty layer."""
    values = {}
    if filename == "" or not os.path.exists(filename):
        return values
    with open(filename, "r", encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line != "" and not line.startswith("#"):
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                raise ValueError("invalid dotenv assignment at line " + str(i + 1))
            key, value = line.split("=", 1)
            key = credential_ref(key.strip())
            value = value.strip()
            if len(value) >= 2 and value[0] == value[len(value) - 1] and value[0] in "\"'":
                quote = value[0]
                value = value[1:-1]
                if quote == '"':
                    value = value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            values[key] = value
        i += 1
    return values
