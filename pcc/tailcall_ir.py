from __future__ import annotations

from dataclasses import dataclass
import json
import re


_FUNC_RE = re.compile(r"define\s+.+?@(?P<name>[\w.$]+)\(.*?\)\s*\{(?P<body>.*?)\n\}", re.S)
_SELF_TAIL_RE = re.compile(r"call\s+.+?@(?P<name>[\w.$]+)\(.*?\)\s*\n\s*ret\s+")
_VOID_SELF_TAIL_RE_TEMPLATE = r"(?P<indent>\s*)call\s+void\s+@{name}\((?P<args>.*?)\)\s*\n(?P=indent)ret\s+void"


@dataclass(frozen=True)
class TailcallCandidate:
    function: str
    rewritten: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class TailcallRewriteResult:
    ir_text: str
    candidates: tuple[TailcallCandidate, ...]

    @property
    def rewritten(self) -> bool:
        return any(c.rewritten for c in self.candidates)

    def report_json(self) -> str:
        return json.dumps({"schema": "pcc.tailcall.rewrite.v1", "candidates": [c.to_json() for c in self.candidates]}, indent=2, sort_keys=True)


def analyze_self_tailcalls(ir_text: str) -> list[TailcallCandidate]:
    out: list[TailcallCandidate] = []
    for match in _FUNC_RE.finditer(ir_text):
        name = match.group("name")
        tail = _SELF_TAIL_RE.search(match.group("body"))
        if tail is not None and tail.group("name") == name:
            out.append(TailcallCandidate(name, False, "self tail call detected"))
    return out


def rewrite_simple_void_self_tailcalls(ir_text: str) -> TailcallRewriteResult:
    """Rewrite the conservative void self-tail-call pattern.

    Supported form:

        call void @f(...)
        ret void

    becomes:

        br label %entry ; pcc.tailcall.self

    This is intentionally narrower than the full phi-loop TCO required for
    value-returning functions such as ``fact_tail``.  It is nevertheless a real
    transformer: the returned IR text changes the control flow and callers can
    wire it into an IR pass pipeline without changing this API.
    """
    candidates: list[TailcallCandidate] = []
    rewritten_text = ir_text
    offset = 0
    for match in list(_FUNC_RE.finditer(ir_text)):
        name = match.group("name")
        body = match.group("body")
        pattern = re.compile(
            _VOID_SELF_TAIL_RE_TEMPLATE.format(name=re.escape(name)),
            re.S,
        )
        if pattern.search(body) is None:
            tail = _SELF_TAIL_RE.search(body)
            if tail is not None and tail.group("name") == name:
                candidates.append(TailcallCandidate(
                    name,
                    False,
                    "self tail call needs value phi-loop rewrite",
                ))
            continue
        new_body, count = pattern.subn(
            lambda m: f"{m.group('indent')}br label %entry ; pcc.tailcall.self",
            body,
        )
        if count == 0:
            continue
        start = match.start("body") + offset
        end = match.end("body") + offset
        rewritten_text = rewritten_text[:start] + new_body + rewritten_text[end:]
        offset += len(new_body) - len(body)
        candidates.append(TailcallCandidate(
            name,
            True,
            f"rewrote {count} void self-tail-call site(s)",
        ))
    if not candidates:
        candidates = analyze_self_tailcalls(ir_text)
    return TailcallRewriteResult(rewritten_text, tuple(candidates))


def format_tailcall_report(ir_text: str) -> str:
    return json.dumps({
        "schema": "pcc.tailcall.v1",
        "candidates": [c.to_json() for c in analyze_self_tailcalls(ir_text)],
    }, indent=2, sort_keys=True)
