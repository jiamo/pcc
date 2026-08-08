"""Stable anonymous identity scoped to one Harness home."""

import os
import uuid


ANONYMOUS_USER_ID_FILE_NAME = ".anonymous-user-id"
_memo_paths = []
_memo_values = []


def is_uuid(value: str) -> bool:
    if len(value) != 36:
        return False
    i = 0
    while i < len(value):
        if i == 8 or i == 13 or i == 18 or i == 23:
            if value[i] != "-":
                return False
        else:
            char = value[i].lower()
            if not ((char >= "0" and char <= "9") or (char >= "a" and char <= "f")):
                return False
        i += 1
    return True


def get_or_create_anonymous_user_id(home: str, generate=None) -> str:
    """Return a memoized, best-effort persisted random UUID for one home."""
    filename = os.path.abspath(os.path.join(home, ANONYMOUS_USER_ID_FILE_NAME))
    index = _memo_index(filename)
    if index >= 0:
        return _memo_values[index]
    persisted = _read_id(filename)
    if persisted is None:
        created = str(uuid.uuid4()) if generate is None else generate()
        if not is_uuid(created):
            raise ValueError("anonymous identity generator returned an invalid UUID")
        persisted = _persist_first_writer(filename, created)
    _memo_paths.append(filename)
    _memo_values.append(persisted)
    return persisted


def reset_anonymous_user_id(home: str) -> bool:
    """Delete one home's identity and process memo; the next read mints a new id."""
    filename = os.path.abspath(os.path.join(home, ANONYMOUS_USER_ID_FILE_NAME))
    index = _memo_index(filename)
    if index >= 0:
        _memo_paths.pop(index)
        _memo_values.pop(index)
    if not os.path.exists(filename):
        return False
    os.unlink(filename)
    return True


def _read_id(filename: str):
    try:
        with open(filename, "r", encoding="utf-8") as stream:
            value = stream.read().strip()
    except OSError:
        return None
    return value if is_uuid(value) else None


def _persist_first_writer(filename: str, created: str) -> str:
    try:
        os.makedirs(os.path.dirname(filename), mode=0o700, exist_ok=True)
        with open(filename, "x", encoding="utf-8") as stream:
            stream.write(created + "\n")
        os.chmod(filename, 0o600)
        return created
    except OSError:
        winner = _read_id(filename)
        if winner is not None:
            return winner
        try:
            with open(filename, "w", encoding="utf-8") as stream:
                stream.write(created + "\n")
            os.chmod(filename, 0o600)
        except OSError:
            pass
        return created


def _memo_index(filename: str) -> int:
    i = 0
    while i < len(_memo_paths):
        if _memo_paths[i] == filename:
            return i
        i += 1
    return -1
