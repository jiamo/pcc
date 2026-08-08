"""CLI compatibility surface over the assembled PCC Harness runtime."""

from harness_runtime import HarnessRuntime, runtime_self_check
from session_runtime import SESSION_FORMAT_VERSION


def create_default_agent():
    """Create the default keyless agent composition."""
    return HarnessRuntime().agent


def render_transcript(agent) -> None:
    """Print the compact transcript derived only from logged events."""
    history = agent.session.derive_model_history()
    i = 0
    while i < len(history):
        print(history[i])
        i += 1


def self_check() -> int:
    """Run the assembled runtime check retained by the original CLI flag."""
    return runtime_self_check()


def _require_option_value(args, index: int, option: str) -> str:
    if index + 1 >= len(args):
        raise ValueError(option + " requires a value")
    return args[index + 1]


def _parse_cli(args):
    home = ""
    session_id = "main"
    created_at = 0
    fork_id = ""
    fork_event_count = -1
    prompt = "hello from pcc"
    prompt_set = False
    i = 1
    while i < len(args):
        value = args[i]
        if value == "--home":
            home = _require_option_value(args, i, value)
            i += 2
        elif value == "--session":
            session_id = _require_option_value(args, i, value)
            i += 2
        elif value == "--created-at":
            created_at = int(_require_option_value(args, i, value))
            i += 2
        elif value == "--fork":
            fork_id = _require_option_value(args, i, value)
            i += 2
        elif value == "--fork-event-count":
            fork_event_count = int(_require_option_value(args, i, value))
            i += 2
        elif value.startswith("--"):
            raise ValueError("unknown Harness option: " + value)
        elif prompt_set:
            raise ValueError("only one prompt argument is supported")
        else:
            prompt = value
            prompt_set = True
            i += 1
    if fork_id != "" and home == "":
        raise ValueError("--fork requires --home")
    return home, session_id, created_at, fork_id, fork_event_count, prompt


def run_cli(args) -> int:
    """Run one keyless native CLI turn or the runtime self-check."""
    if len(args) > 1 and args[1] == "--self-check":
        return runtime_self_check()
    home, session_id, created_at, fork_id, fork_event_count, prompt = _parse_cli(
        args
    )
    runtime = HarnessRuntime(session_id, created_at, home)
    resumed = False
    if home != "":
        resumed = runtime.resume_or_create_session(session_id, created_at)
        if fork_id != "":
            runtime.fork_session(fork_id, created_at, fork_event_count)
    runtime.agent.run_turn(prompt)
    if home != "":
        runtime.save_session()
    projection = runtime.session.projection()
    print("DeepSeek Harness / PCC native core")
    print(
        "session="
        + runtime.session.header.session_id
        + " format="
        + str(SESSION_FORMAT_VERSION)
        + " durable="
        + ("true" if home != "" else "false")
        + " resumed="
        + ("true" if resumed else "false")
    )
    print("title=" + projection.title)
    if home != "":
        print("identity=" + runtime.resolve_identity())
    render_transcript(runtime.agent)
    runtime.dispose()
    return 0
