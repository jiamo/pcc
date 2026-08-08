"""Assembled PCC Harness runtime composition."""

import os

from agent_runtime import AgentConfig, AgentLoop, PromptRuntime
from credentials_runtime import MemoryCredentialProvider, credential_ref
from identity_runtime import get_or_create_anonymous_user_id
from loader_runtime import (
    Bundle,
    EntryPatch,
    LoaderEntry,
    PluginCatalog,
    PluginLoader,
    ProfileComposer,
    RuntimeProfile,
    dump_entry_tree,
)
from model_runtime import DeterministicModelProvider, ModelProviderRegistry
from plugin_runtime import PluginKernel
from session_runtime import Session, SessionHeader
from session_persistence import JsonlSessionStore
from settings_runtime import SettingsProvider, SettingsSchema
from todo_plan_runtime import PlanModeRuntime, TodoRuntime
from tool_runtime import create_default_tools


class HarnessRuntime:
    """One owned plugin composition and its default agent session."""

    def __init__(
        self,
        session_id: str = "main",
        created_at: int = 0,
        home: str = "",
        resume: bool = False,
        identity_generator=None,
    ) -> None:
        self.service_generation = session_id + ":" + str(created_at)
        self.kernel = PluginKernel()
        self.prompt = PromptRuntime()
        self.prompt.register(
            "persona", 0, "You are DeepSeek Harness running natively on PCC."
        )
        self.tools = create_default_tools()
        self.todo = TodoRuntime(self.tools, False)
        self.plan = PlanModeRuntime(
            self.prompt,
            self.tools,
            "You are in plan mode. Explore and design before presenting the complete plan.",
        )
        self.models = ModelProviderRegistry()
        self.models.register("deterministic", DeterministicModelProvider())
        self.session = Session(SessionHeader(session_id, created_at))
        self.config = AgentConfig("deterministic", "pcc-keyless")
        self.agent = AgentLoop(
            self.session, self.prompt, self.tools, self.models, self.config
        )
        self.plan.attach(self.agent)
        self.settings = SettingsProvider()
        self.settings_scope = self.settings.register(
            "harness",
            SettingsSchema(
                {
                    "provider": "deterministic",
                    "model": "pcc-keyless",
                    "apiKeyEnv": "DEEPSEEK_API_KEY",
                }
            ),
        )
        self.credentials = MemoryCredentialProvider()
        self.home = ""
        self.identity = ""
        self.session_store = None
        root = self.kernel.context
        self.plugin_catalog = PluginCatalog()
        catalog_registration = self.plugin_catalog.register(
            "harness-runtime-static", self._install_static_services
        )
        root.effect(
            lambda: catalog_registration.dispose,
            "plugin-catalog:harness-runtime-static",
        )
        self.loader = PluginLoader(self.kernel, self.plugin_catalog)
        self.loader.reconcile(
            [
                LoaderEntry(
                    "runtime-static",
                    "harness-runtime-static",
                    provides=[
                        "prompt",
                        "tools",
                        "models",
                        "settings",
                        "credentials",
                        "planMode",
                    ],
                )
            ]
        )
        self.session_effect = root.provide(
            "session", self.session, self.service_generation
        )
        self.agent_effect = root.provide(
            "agent", self.agent, self.service_generation
        )
        if home != "":
            self.configure_home(home, identity_generator)
            if resume:
                self.resume_session(session_id)

    def _install_static_services(self, scope, config) -> None:
        scope.provide("prompt", self.prompt)
        scope.provide("tools", self.tools)
        scope.provide("models", self.models)
        scope.provide("settings", self.settings)
        scope.provide("credentials", self.credentials)
        scope.provide("planMode", self.plan)

    def configure_home(self, home: str, identity_generator=None) -> None:
        """Bind durable identity and session services to one Harness home."""
        if home == "":
            raise ValueError("Harness home must not be empty")
        if self.home != "":
            raise RuntimeError("Harness home is already configured")
        self.home = os.path.abspath(home)
        self.identity = get_or_create_anonymous_user_id(
            self.home, identity_generator
        )
        self.session_store = JsonlSessionStore(
            os.path.join(self.home, "sessions")
        )
        root = self.kernel.context
        root.provide("identity", self.identity, self.home)
        root.provide("sessionStore", self.session_store, self.home)

    def has_persisted_session(self, session_id: str) -> bool:
        """Return whether the configured home contains a session log."""
        return self.resolve_session_store().contains(session_id)

    def save_session(self) -> str:
        """Atomically persist the current append-only session log."""
        return self.resolve_session_store().save(self.session)

    def resume_session(self, session_id: str) -> None:
        """Replace current session-scoped services from one durable log."""
        self._replace_session(self.resolve_session_store().load(session_id))

    def resume_or_create_session(
        self, session_id: str, created_at: int = 0
    ) -> bool:
        """Resume a durable session, or create it when no log exists.

        @returns True when an existing log was resumed.
        """
        if self.has_persisted_session(session_id):
            self.resume_session(session_id)
            return True
        self.new_session(session_id, created_at)
        return False

    def fork_session(
        self, session_id: str, created_at: int, event_count: int = -1
    ) -> None:
        """Replace current services with a fork at an event boundary."""
        self._replace_session(
            self.session.fork(session_id, created_at, event_count)
        )

    def new_session(self, session_id: str, created_at: int = 0) -> None:
        self._replace_session(Session(SessionHeader(session_id, created_at)))

    def _replace_session(self, session: Session) -> None:
        self.agent_effect.dispose()
        self.session_effect.dispose()
        self.service_generation = (
            session.header.session_id + ":" + str(session.header.created_at)
        )
        self.session = session
        self.agent = AgentLoop(
            self.session, self.prompt, self.tools, self.models, self.config
        )
        self.plan.attach(self.agent)
        root = self.kernel.context
        self.session_effect = root.provide(
            "session", self.session, self.service_generation
        )
        self.agent_effect = root.provide(
            "agent", self.agent, self.service_generation
        )

    def dispose(self) -> None:
        self.loader.dispose()
        self.kernel.dispose()

    def resolve_agent(self) -> AgentLoop:
        """Resolve the typed agent service from the root composition."""
        return self.kernel.context.get("agent")

    def resolve_session(self) -> Session:
        """Resolve the typed session service from the root composition."""
        return self.kernel.context.get("session")

    def resolve_identity(self) -> str:
        """Resolve the stable anonymous identity for the configured home."""
        return self.kernel.context.get("identity")

    def resolve_session_store(self) -> JsonlSessionStore:
        """Resolve the durable session store for the configured home."""
        return self.kernel.context.get("sessionStore")


def runtime_self_check() -> int:
    """Check assistant, tool, replay, and plugin service paths."""
    runtime = HarnessRuntime()
    first = runtime.agent.run_turn("hello")
    if first != "PCC harness is running. You said: hello":
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: assistant")
        return 1
    second = runtime.agent.run_turn("/tool echo native pcc")
    if second != "Tool returned: native pcc":
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: tool")
        return 2
    projection = runtime.session.projection()
    if len(projection.messages) != 6:
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: replay")
        return 3
    if projection.session_stats.turns != 2 or projection.session_stats.steps != 3:
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: session-stats")
        return 12
    descriptor = runtime.settings.describe(True)[0]
    if descriptor["value"]["apiKeyEnv"] != credential_ref("DEEPSEEK_API_KEY"):
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: settings")
        return 5
    if (
        runtime.kernel.context.service_relationship("agent")
        != runtime.service_generation
        or runtime.kernel.context.service_relationship("session")
        != runtime.service_generation
    ):
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: service")
        return 4
    graph = runtime.kernel.graph_snapshot()
    if (
        runtime.loader.mounted_entries() != ["runtime-static"]
        or graph["plugins"][0]["name"] != "runtime-static"
        or graph["plugins"][0]["state"] != "ACTIVE"
    ):
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-graph")
        return 10

    composer = ProfileComposer()
    composer.register_bundle(
        Bundle("base", [LoaderEntry("profile-probe", "probe", {"layer": "base"})])
    )
    composed = composer.compose(
        RuntimeProfile("headless", ["base"]),
        cli_patches=[
            EntryPatch(
                "profile-probe",
                LoaderEntry("profile-probe", "probe", {"layer": "cli"}),
            )
        ],
    )
    if (
        composed[0].config["layer"] != "cli"
        or "profile-probe" not in dump_entry_tree(composed)
    ):
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-profile")
        return 11
    cordis_values = []

    def install_probe_consumer(scope) -> None:
        cordis_values.append(scope.require("cordisProbe", "probe-consumer"))

    def install_probe_provider(scope) -> None:
        scope.provide("cordisProbe", "provider-v1")

    runtime.kernel.install(
        "probe-consumer", install_probe_consumer, inject=["cordisProbe"]
    )
    if runtime.kernel.plugin_state("probe-consumer") != "PENDING":
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-pending")
        return 6
    runtime.kernel.install(
        "probe-provider",
        install_probe_provider,
        provides=["cordisProbe"],
    )
    if (
        runtime.kernel.plugin_state("probe-consumer") != "ACTIVE"
        or cordis_values != ["provider-v1"]
    ):
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-activate")
        return 7
    runtime.kernel.unload("probe-provider")
    withdraw_state = runtime.kernel.plugin_state("probe-consumer")
    if withdraw_state != "PENDING":
        print(
            "HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-withdraw state="
            + withdraw_state
        )
        return 8
    runtime.kernel.unload("probe-consumer")

    effect_events = []
    effect_scope = runtime.kernel.context.child("effect-probe")

    def effect_setup():
        yield lambda: effect_events.append("first-off")
        yield lambda: effect_events.append("second-off")

    effect_scope.effect(effect_setup)
    effect_scope.dispose()
    if effect_events != ["second-off", "first-off"]:
        print("HARNESS_RUNTIME_SELF_CHECK_FAILED: cordis-generator-effect")
        return 9
    runtime.dispose()
    print("HARNESS_RUNTIME_SELF_CHECK_OK")
    return 0
