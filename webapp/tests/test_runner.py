"""Exercises runner.py's subprocess tailing, phase mapping, and SSE replay against a stub
script that prints lines in field_pipeline's exact log format — no real pipeline, no browser,
no RCAC call. See docs/okf/project/components/webapp.md."""
import json
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner import launch, stop_run  # noqa: E402

STUB = textwrap.dedent("""
    import sys
    print("10:00:00 INFO    field_pipeline: version folder: /tmp/fake_run_dir", flush=True)
    print("10:00:01 INFO    field_pipeline: === [1/2] Sheet | UNIV A | Program | 3 field(s) | elapsed 0.0 min", flush=True)
    print("10:00:02 INFO    field_pipeline.discover: found start page", flush=True)
    print("10:00:03 INFO    field_pipeline.fetch: 2 readable page(s) (0 pdf) -> 5 chunks", flush=True)
    print("10:00:04 INFO    field_pipeline.extract: group tuition done", flush=True)
    sys.exit(0)
""")


def _wait(run, timeout=5):
    deadline = time.time() + timeout
    while run.status == "running" and time.time() < deadline:
        time.sleep(0.02)
    return run


def test_tail_parses_phases_and_version_dir(tmp_path):
    run = _wait(launch([sys.executable, "-c", STUB], tmp_path))
    assert run.status == "done"
    assert run.return_code == 0
    assert run.out_dir == Path("/tmp/fake_run_dir")
    phases = [e["phase"] for e in run.events]
    assert phases == ["pipeline", "pipeline", "discover", "fetch", "extract"]
    assert run.events[1]["row_start"] is True
    assert run.events[0]["row_start"] is False


def test_stream_replays_from_last_seq_and_ends():
    run = _wait(launch([sys.executable, "-c", STUB], Path(".")))
    events = list(run.stream(last_seq=2))  # skip the first two already-seen events
    *lines, end = events
    seqs = [json.loads(line.split("data: ", 1)[1]) for line in lines]
    assert [s["seq"] for s in seqs] == [3, 4, 5]
    assert end.startswith("event: end")
    assert json.loads(end.split("data: ", 1)[1])["status"] == "done"


def test_stop_sends_sigint_and_marks_stopped(tmp_path):
    sleeper = "import time, signal, sys\ntry:\n    time.sleep(10)\nexcept KeyboardInterrupt:\n    sys.exit(130)\n"
    run = launch([sys.executable, "-c", sleeper], tmp_path)
    time.sleep(0.2)
    stop_run(run)
    _wait(run)
    assert run.status == "stopped"
