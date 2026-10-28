"""Launches `python -m field_pipeline` as a subprocess and tails its log so the web app can
show every step live. field_pipeline owns one browser session and one SQLite store per
process, so only one Run is ever active at a time (enforced by app.py, not here)."""
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

LOG_LINE = re.compile(r"^(\d\d:\d\d:\d\d) (\w+)\s+([\w.]+): (.*)$")
ROW_START = re.compile(r"^=== \[(\d+)/(\d+)\]")
VERSION_DIR = re.compile(r"^version folder: (.+)$")

# field_pipeline's logger names (agent-workspace/field_pipeline/*.py: logging.getLogger(__name__),
# except __main__.py which uses "field_pipeline") mapped to the stage the web UI groups them under.
PHASE_OF = {
    "field_pipeline": "pipeline",
    "field_pipeline.discover": "discover",
    "field_pipeline.fetch": "fetch",
    "field_pipeline.search": "fetch",
    "field_pipeline.extract": "extract",
    "field_pipeline.llm": "extract",
    "field_pipeline.ipeds": "ipeds",
}
PHASE_LABELS = {"pipeline": "Row", "discover": "Discover", "fetch": "Fetch", "extract": "Extract",
                "ipeds": "IPEDS", "other": "Other"}


def _parse_line(line: str, seq: int) -> dict:
    m = LOG_LINE.match(line)
    if not m:
        return {"seq": seq, "ts": "", "level": "RAW", "logger": "", "phase": "other", "message": line,
                "row_start": False}
    ts, level, logger, message = m.groups()
    return {"seq": seq, "ts": ts, "level": level, "logger": logger, "phase": PHASE_OF.get(logger, "other"),
            "message": message, "row_start": bool(ROW_START.match(message))}


class Run:
    def __init__(self, run_id: str, command: list[str], process: subprocess.Popen):
        self.id = run_id
        self.command = command
        self.process = process
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.events: list[dict] = []
        self.status = "running"  # running | done | error | stopped
        self.out_dir: Path | None = None
        self.return_code: int | None = None
        self.stop_requested = False
        self.started_at = time.time()

    def decisions(self) -> list[dict]:
        """One row per decided cell, from the run's decisions.csv, once it has been written."""
        import csv
        if not self.out_dir or not (self.out_dir / "decisions.csv").exists():
            return []
        with open(self.out_dir / "decisions.csv", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def stream(self, last_seq: int = 0):
        """SSE lines: every event after last_seq, then new ones as they arrive, until the run ends."""
        while True:
            with self.cond:
                while True:
                    new = [e for e in self.events if e["seq"] > last_seq]
                    if new or self.status != "running":
                        break
                    self.cond.wait(timeout=1.0)
            for e in new:
                last_seq = e["seq"]
                yield f"id: {e['seq']}\nevent: line\ndata: {_json(e)}\n\n"
            if not new and self.status != "running":
                yield f"event: end\ndata: {_json({'status': self.status, 'return_code': self.return_code})}\n\n"
                return


def _json(obj) -> str:
    import json
    return json.dumps(obj)


def _read(run: Run) -> None:
    seq = 0
    for line in iter(run.process.stdout.readline, ""):
        line = line.rstrip("\n")
        if not line:
            continue
        seq += 1
        event = _parse_line(line, seq)
        with run.lock:
            run.events.append(event)
            if (m := VERSION_DIR.match(event["message"])) and run.out_dir is None:
                run.out_dir = Path(m.group(1))
            run.cond.notify_all()
    run.process.stdout.close()
    code = run.process.wait()
    with run.lock:
        run.return_code = code
        run.status = "stopped" if run.stop_requested else ("done" if code == 0 else "error")
        run.cond.notify_all()


def launch(cmd: list[str], cwd: Path) -> Run:
    """Runs cmd as a subprocess, tailing its combined stdout/stderr into a Run's event buffer."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    run = Run(uuid.uuid4().hex[:8], cmd, process)
    threading.Thread(target=_read, args=(run,), daemon=True).start()
    return run


def start_run(xlsx: Path, agent_workspace: Path, *, sheets: list[str], only: list[str],
              limit: int | None, overwrite: bool, fresh: bool, browser: str, db: Path | None = None) -> Run:
    cmd = [sys.executable, "-m", "field_pipeline", str(xlsx), "-v"]
    for s in sheets:
        cmd += ["--sheet", s]
    for o in only:
        cmd += ["--only", o]
    if limit:
        cmd += ["--limit", str(limit)]
    if overwrite:
        cmd.append("--overwrite")
    if fresh:
        cmd.append("--fresh")
    cmd += ["--browser", browser]
    if db:
        cmd += ["--db", str(db)]
    return launch(cmd, agent_workspace)


def stop_run(run: Run) -> None:
    """SIGINT, not terminate: field_pipeline.__main__.main() only saves partial results and
    records the run (fetcher.close(), record_run()) on the KeyboardInterrupt/finally path."""
    with run.lock:
        if run.status != "running":
            return
        run.stop_requested = True
    run.process.send_signal(signal.SIGINT)
