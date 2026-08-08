from harness_runtime import HarnessRuntime, runtime_self_check


UUID_ONE = "11111111-1111-4111-8111-111111111111"


def test_composition_exposes_owned_services_and_disposes():
    runtime = HarnessRuntime()

    assert runtime.loader.mounted_entries() == ["runtime-static"]
    snapshot = runtime.kernel.graph_snapshot()
    assert snapshot["plugins"][0]["name"] == "runtime-static"
    assert snapshot["plugins"][0]["state"] == "ACTIVE"
    assert snapshot["services"][0]["provider"] == "runtime-static"
    assert runtime.resolve_agent() is runtime.agent
    assert runtime.resolve_session() is runtime.session
    assert runtime.kernel.context.service_relationship("agent") == "main:0"
    assert runtime.agent.run_turn("hello").startswith("PCC harness is running")

    runtime.dispose()
    assert runtime.kernel.context.active is False


def test_runtime_self_check_covers_assistant_tool_and_replay(capsys):
    assert runtime_self_check() == 0
    assert "HARNESS_RUNTIME_SELF_CHECK_OK" in capsys.readouterr().out


def test_new_session_replaces_session_scoped_services():
    runtime = HarnessRuntime()
    original_session = runtime.session

    runtime.new_session("next", 10)

    assert runtime.session is not original_session
    assert runtime.session.header.session_id == "next"
    assert runtime.resolve_session() is runtime.session
    assert runtime.resolve_agent() is runtime.agent
    assert runtime.kernel.context.service_relationship("agent") == "next:10"
    assert runtime.kernel.context.service_relationship("session") == "next:10"


def test_durable_home_exposes_identity_store_and_resumes_projection(tmp_path):
    home = str(tmp_path / "harness-home")
    runtime = HarnessRuntime(home=home, identity_generator=lambda: UUID_ONE)

    assert runtime.resolve_identity() == UUID_ONE
    assert runtime.kernel.context.service_relationship("identity") == home
    assert runtime.kernel.context.service_relationship("sessionStore") == home
    assert runtime.resume_or_create_session("durable", 42) is False
    runtime.agent.run_turn("A durable first request")
    runtime.save_session()
    before = runtime.session.projection()
    runtime.dispose()

    restored = HarnessRuntime(home=home, identity_generator=lambda: UUID_ONE)
    assert restored.resume_or_create_session("durable", 99) is True
    after = restored.session.projection()

    assert after.title == "A durable first request"
    assert after.event_count == before.event_count
    assert after.completed_turn_count == 1
    assert after.step_count == 1
    assert after.assistant_chunk_count == 1
    assert after.tool_call_count == 0
    assert after.error_turn_count == 0
    assert after.session_stats.turns == 1
    assert after.session_stats.steps == 1
    assert after.messages[0].content == "A durable first request"


def test_runtime_fork_replaces_services_at_explicit_event_boundary(tmp_path):
    runtime = HarnessRuntime(
        home=str(tmp_path / "home"), identity_generator=lambda: UUID_ONE
    )
    runtime.agent.run_turn("first")
    boundary = runtime.session.count()
    runtime.agent.run_turn("second")

    runtime.fork_session("child", 100, boundary)
    runtime.save_session()

    assert runtime.session.header.parent_session == "main"
    assert runtime.session.header.seed_length == boundary
    assert runtime.session.projection().title == "first"
    assert runtime.session.derive_model_history() == [
        "user: first",
        "assistant: PCC harness is running. You said: first",
    ]
    assert runtime.kernel.context.service_relationship("agent") == "child:100"
