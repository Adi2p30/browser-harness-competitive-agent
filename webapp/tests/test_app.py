"""Routing-level checks for app.py, with field_pipeline itself stubbed out (no subprocess, no
browser, no RCAC call) — just the Flask routing/gating logic: the run form stays open while a
run is going (multiple runs), and a run started while another is active gets its own --db."""
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("yaml")
pytest.importorskip("openpyxl")
pytest.importorskip("bs4")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as webapp  # noqa: E402
from runner import Run  # noqa: E402


class _FakeProcess:
    def send_signal(self, *_):
        pass


def _fake_run(command):
    run = Run("r" + str(len(webapp._runs)), command, _FakeProcess())
    run.status = "running"
    return run


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webapp, "_rcac_key_missing", lambda: None)
    calls = []

    def fake_start_run(xlsx, agent_workspace, **kwargs):
        calls.append(kwargs)
        return _fake_run([str(xlsx), "--db", str(kwargs["db"])] if kwargs.get("db") else [str(xlsx)])

    monkeypatch.setattr(webapp, "start_run", fake_start_run)
    webapp._runs.clear()
    with webapp.app.test_client() as c:
        yield c, calls


def test_index_shows_program_label_not_sheet(client):
    c, _ = client
    body = c.get("/").get_data(as_text=True)
    assert "Program(s)" in body
    assert "Sheet(s)" not in body


def test_second_run_while_first_running_gets_isolated_db(client):
    c, calls = client
    r1 = c.post("/run", data={"sheet": "Online MS Economics"})
    assert r1.status_code == 302
    r2 = c.post("/run", data={"sheet": "Online MBA"})
    assert r2.status_code == 302  # not redirected back to run 1 — both are allowed to run
    assert len(webapp._runs) == 2
    assert calls[0]["db"] is None        # first run: shared default cache, as before
    assert calls[1]["db"] is not None    # second run started while the first is active: isolated

    body = c.get("/").get_data(as_text=True)
    assert body.count('class="status running"') == 2
