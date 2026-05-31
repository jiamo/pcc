from __future__ import annotations


def test_public_pcc_launcher_always_uses_full_cli(monkeypatch):
    import pcc.cli_core as cli_core
    import pcc.cli_launcher as launcher

    calls = []

    def fake_cli_main(argv):
        calls.append(list(argv))
        return 17

    monkeypatch.setattr(cli_core, "cli_main", fake_cli_main)

    assert launcher.main(["pcc/__main__.py", "-o", "out"]) == 17
    assert calls == [["pcc/__main__.py", "-o", "out"]]
