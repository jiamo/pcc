"""Validated JSONL persistence for PCC Harness sessions.

This module is written against Python file operations so PCC can own their
lowering and platform implementation. It never launches or embeds CPython.
"""

import json
import os

from session_runtime import Session, SessionEvent, SessionHeader


class JsonlSessionStore:
    """Atomic whole-log JSONL store with explicit format validation."""

    def __init__(self, directory: str) -> None:
        if directory == "":
            raise ValueError("session directory must not be empty")
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)

    def save(self, session: Session) -> str:
        path = self.path_for(session.header.session_id)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(header_record(session.header), ensure_ascii=False))
            stream.write("\n")
            i = 0
            while i < len(session.events):
                stream.write(
                    json.dumps(event_record(session.events[i]), ensure_ascii=False)
                )
                stream.write("\n")
                i += 1
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path

    def load(self, session_id: str) -> Session:
        path = self.path_for(session_id)
        with open(path, "r", encoding="utf-8") as stream:
            first = stream.readline()
            if first == "":
                raise ValueError("session file is empty")
            header = decode_header(json.loads(first))
            if header.session_id != session_id:
                raise ValueError("session filename and header id differ")
            events = []
            expected = 1
            for line in stream:
                if line.strip() == "":
                    continue
                event = decode_event(json.loads(line))
                if event.sequence != expected:
                    raise ValueError("session event sequence is not contiguous")
                events.append(event)
                expected += 1
        return Session(header, events)

    def contains(self, session_id: str) -> bool:
        """Return whether a validated session id has a durable log."""
        return os.path.exists(self.path_for(session_id))

    def delete(self, session_id: str) -> bool:
        path = self.path_for(session_id)
        if not os.path.exists(path):
            return False
        os.unlink(path)
        return True

    def list_ids(self):
        ids = []
        for name in os.listdir(self.directory):
            if name.endswith(".jsonl"):
                ids.append(name[:-6])
        ids.sort()
        return ids

    def path_for(self, session_id: str) -> str:
        validate_filename_id(session_id)
        return os.path.join(self.directory, session_id + ".jsonl")


def validate_filename_id(session_id: str) -> None:
    if session_id == "" or session_id == "." or session_id == "..":
        raise ValueError("invalid session id")
    if "/" in session_id or "\\" in session_id or "\x00" in session_id:
        raise ValueError("session id must not contain path separators")


def header_record(header: SessionHeader):
    return {
        "kind": "header",
        "version": header.version,
        "id": header.session_id,
        "createdAt": header.created_at,
        "cwd": header.cwd,
        "parentSession": header.parent_session,
        "seedLength": header.seed_length,
        "origin": header.origin,
        "delegationDepth": header.delegation_depth,
        "agentPreset": header.agent_preset,
    }


def event_record(event: SessionEvent):
    return {
        "kind": "event",
        "sequence": event.sequence,
        "type": event.event_type,
        "text": event.text,
        "turn": event.turn,
        "step": event.step,
        "callId": event.call_id,
        "name": event.name,
        "arguments": event.arguments,
        "reason": event.reason,
        "source": event.source,
        "metadata": event.metadata,
        "ignorable": event.ignorable,
        "time": event.event_time,
        "inputTokens": event.input_tokens,
        "outputTokens": event.output_tokens,
    }


def decode_header(record) -> SessionHeader:
    if record.get("kind") != "header":
        raise ValueError("first session record must be a header")
    return SessionHeader(
        require_string(record, "id"),
        require_int(record, "createdAt"),
        optional_string(record, "cwd"),
        optional_string(record, "parentSession"),
        optional_int(record, "seedLength"),
        optional_string(record, "origin"),
        optional_int(record, "delegationDepth"),
        optional_string(record, "agentPreset"),
        require_int(record, "version"),
    )


def decode_event(record) -> SessionEvent:
    if record.get("kind") != "event":
        raise ValueError("session body record must be an event")
    return SessionEvent(
        require_int(record, "sequence"),
        require_string(record, "type"),
        optional_string(record, "text"),
        optional_int(record, "turn"),
        optional_int(record, "step"),
        optional_string(record, "callId"),
        optional_string(record, "name"),
        optional_string(record, "arguments"),
        optional_string(record, "reason"),
        optional_string(record, "source"),
        optional_string(record, "metadata"),
        bool(record.get("ignorable", False)),
        require_int(record, "time"),
        require_int(record, "inputTokens"),
        require_int(record, "outputTokens"),
    )


def require_string(record, name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str):
        raise ValueError("session field must be a string: " + name)
    return value


def optional_string(record, name: str) -> str:
    value = record.get(name, "")
    if not isinstance(value, str):
        raise ValueError("session field must be a string: " + name)
    return value


def require_int(record, name: str) -> int:
    value = record.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("session field must be an integer: " + name)
    return value


def optional_int(record, name: str) -> int:
    value = record.get(name, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("session field must be an integer: " + name)
    return value
