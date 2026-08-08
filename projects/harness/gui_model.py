"""PCC Harness GUI state derived from the session event log."""

from harness_runtime import HarnessRuntime


class HarnessGuiState:
    """Small first-shell state owner shared by headless and native GUI paths."""

    def __init__(self) -> None:
        self.runtime = HarnessRuntime()
        self.agent = self.runtime.agent
        self.phase = "hero"
        self.status = "Ready"

    def new_session(self) -> None:
        self.runtime.new_session("main")
        self.agent = self.runtime.agent
        self.phase = "hero"
        self.status = "Ready"

    def submit_sample(self) -> str:
        self.status = "Running"
        response = self.agent.run_turn("hello from pcc gui")
        self.phase = "active"
        self.status = "Ready"
        return response

    def transcript(self):
        return self.agent.session.derive_model_history()

    def visible_regions(self):
        return [
            "sidebar",
            "session-navigation",
            "trajectory",
            "composer",
            "status",
            "settings",
        ]

    def visible_labels(self):
        labels = [
            "deepseek",
            "New Session",
            "Workspaces",
            "pcc",
            "Sessions",
            "Settings",
            self.status,
        ]
        if self.phase == "hero":
            labels += [
                "DeepSeek Harness",
                "Preview",
                "Choose workspace",
                "What can I help you build?",
            ]
        else:
            labels += [
                "PCC Harness",
                "Chat",
                "Trajectory",
                "hello from pcc gui",
                "PCC harness is running. You said: hello from pcc gui",
            ]
        return labels
