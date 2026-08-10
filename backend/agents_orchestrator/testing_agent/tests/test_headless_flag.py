from __future__ import annotations

import agents_orchestrator.testing_agent.tools.ui_testing_agent as ui


def test_chrome_options_headless_toggle(monkeypatch):
    monkeypatch.setattr(ui, "TESTING_AGENT_HEADLESS", True, raising=False)
    opts = ui._build_chrome_options()
    args = " ".join(getattr(opts, "arguments", []))
    assert "headless" in args


def test_chrome_options_visible_by_default(monkeypatch):
    monkeypatch.setattr(ui, "TESTING_AGENT_HEADLESS", False, raising=False)
    opts = ui._build_chrome_options()
    args = " ".join(getattr(opts, "arguments", []))
    assert "headless" not in args
