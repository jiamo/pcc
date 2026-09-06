"""Opt-in durable pytest reports: -p scripts.pytest_live_report --pcc-live-report PATH.

The controller records each report as it arrives, including xdist reports.
Failure tracebacks survive a later outer watchdog or interrupted session.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest


def pytest_addoption(parser):
    parser.addoption("--pcc-live-report", default=None, help="New JSONL report path")


def pytest_configure(config):
    path = config.getoption("--pcc-live-report")
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(LiveReport(Path(path), config), "pcc-live-report")


class LiveReport:
    def __init__(self, path, config):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("x", encoding="utf-8")
        self.collections = set()
        self.write(
            "start",
            argv=list(config.invocation_params.args),
            root=str(config.rootpath),
            markexpr=config.getoption("markexpr"),
            keyword=config.getoption("keyword"),
            ignore=config.getoption("ignore") or [],
            ignore_glob=config.getoption("ignore_glob") or [],
            deselect=config.getoption("deselect") or [],
            collect_only=config.getoption("collectonly"),
            lf=bool(config.getoption("lf", default=False)),
            stepwise=bool(config.getoption("stepwise", default=False)),
            stepwise_skip=bool(config.getoption("stepwise_skip", default=False)),
            inifile=str(config.inipath or ""),
            override_ini=config.getoption("override_ini") or [],
            pyargs=bool(config.getoption("pyargs", default=False)),
            noconftest=bool(config.getoption("noconftest", default=False)),
            confcutdir=str(config.getoption("confcutdir") or ""),
            source_manifest=os.environ.get("PCC_VALIDATION_SOURCE_MANIFEST", ""),
            validation_environment={
                name: os.environ.get(name, "")
                for name in (
                    "PCC_VALIDATION_SOURCE_MANIFEST",
                    "PCC_VALIDATION_INSTALLATION_SHA256",
                    "PCC1_BINARY",
                    "PCC_SOURCE_ROOT",
                    "PCC_REPO_ROOT",
                    "PCC_RUNTIME_ARCHIVE",
                )
            },
        )

    def write(self, event, **fields):
        self.stream.write(json.dumps({"event": event, "time": time.time(), **fields}) + "\n")
        self.stream.flush()
        if fields.get("outcome") == "failed" or event == "finish":
            os.fsync(self.stream.fileno())

    def pytest_runtest_logreport(self, report):
        fields = dict(
            nodeid=report.nodeid,
            when=report.when,
            outcome=report.outcome,
            duration=report.duration,
        )
        if report.failed or report.skipped:
            fields["longrepr"] = report.longreprtext
        if hasattr(report, "wasxfail"):
            fields["wasxfail"] = report.wasxfail
        self.write("report", **fields)

    def pytest_collectreport(self, report):
        if report.failed:
            self.write(
                "collection",
                nodeid=report.nodeid,
                outcome=report.outcome,
                longrepr=report.longreprtext,
            )

    def record_collection(self, nodeids):
        identity = tuple(nodeids)
        if identity not in self.collections:
            self.collections.add(identity)
            self.write("collected", nodeids=list(identity))

    def pytest_collection_finish(self, session):
        if not session.config.getoption("numprocesses", default=0):
            self.record_collection([item.nodeid for item in session.items])

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        self.record_collection(ids)

    def pytest_deselected(self, items):
        self.write("deselected", nodeids=[item.nodeid for item in items])

    def pytest_sessionfinish(self, session, exitstatus):
        self.write(
            "finish",
            exitstatus=int(exitstatus),
            testscollected=session.testscollected,
            testsfailed=session.testsfailed,
        )

    def pytest_unconfigure(self, config):
        self.stream.close()
