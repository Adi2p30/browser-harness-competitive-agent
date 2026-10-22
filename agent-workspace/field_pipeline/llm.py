"""Minimal client for Purdue RCAC GenAI Studio (OpenAI-compatible chat completions)."""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests

API_URL = os.environ.get("RCAC_API_URL", "https://genai.rcac.purdue.edu/api/chat/completions")
MODEL = os.environ.get("RCAC_MODEL", "qwen3.8:27b")
TIMEOUT, RETRIES = 120, 8  # normal replies take 5-95 s; a hung request is retried sooner
MIN_INTERVAL = 1.5  # seconds between request starts across all threads (RCAC rate-limits bursts)
_gate = threading.Lock()
_last_start = [0.0]
LOG_PATH: Path | None = None  # every prompt/reply appended here as JSONL when set
_log_lock = threading.Lock()


def _post(*args, limit: float, **kwargs):
    """requests.post with a wall-clock limit. requests' own timeout only bounds each socket read, so a server that
    stalls mid-reply can hang a call for hours; the post runs on a daemon thread that is abandoned after `limit`
    seconds (daemon, so it never blocks the process from exiting). Raises TimeoutError."""
    out: list = []
    t = threading.Thread(target=lambda: out.append(_safe(requests.post, *args, **kwargs)), daemon=True)
    t.start()
    t.join(limit)
    if not out:
        raise TimeoutError
    if isinstance(out[0], BaseException):
        raise out[0]
    return out[0]


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:  # handed back to the caller's thread
        return exc
log = logging.getLogger(__name__)


def api_key() -> str:
    if not os.environ.get("RCAC_API_KEY"):
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "RCAC_API_KEY" and v.strip():
                    os.environ["RCAC_API_KEY"] = v.strip().strip("\"'")
    if not os.environ.get("RCAC_API_KEY"):
        raise RuntimeError("RCAC_API_KEY missing: add it to .env in the repo root")
    return os.environ["RCAC_API_KEY"]


def chat(prompt: str) -> str:
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    delay, started = 3, time.time()
    for attempt in range(1, RETRIES + 1):
        with _gate:  # pace request starts so parallel groups don't burst past the rate limit
            pause = _last_start[0] + MIN_INTERVAL - time.time()
            if pause > 0:
                time.sleep(pause)
            _last_start[0] = time.time()
        try:
            try:
                resp = _post(API_URL, headers=headers, json=body, timeout=TIMEOUT, limit=TIMEOUT + 10)
            except TimeoutError:
                log.warning("RCAC call exceeded %d s (attempt %d); retrying", TIMEOUT + 10, attempt)
                continue
            if not resp.ok and "rate limit" in resp.text[:300].casefold():
                wait_s = min(15 * 2 ** (attempt - 1), 240)
                log.warning("RCAC rate limit (attempt %d); waiting %d s", attempt, wait_s)
                time.sleep(wait_s)
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(f"RCAC rejected the API key (HTTP {resp.status_code})")
            if resp.ok:
                content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
                if content:
                    _record(prompt, content, time.time() - started)
                    return content
                log.warning("RCAC returned empty content (attempt %d)", attempt)
            else:
                log.warning("RCAC HTTP %s (attempt %d): %s", resp.status_code, attempt, resp.text[:200])
        except requests.RequestException as exc:
            log.warning("RCAC request error (attempt %d): %s", attempt, exc)
        time.sleep(delay)
        delay = min(delay * 2, 60)
    _record(prompt, None, time.time() - started)
    raise RuntimeError("RCAC failed after retries")


def chat_json(prompt: str) -> dict:
    """chat() and parse the first JSON object in the reply; one retry on unparseable output."""
    for _ in range(2):
        text = re.sub(r"```(?:json)?|<think>.*?</think>", "", chat(prompt), flags=re.S)
        start = text.find("{")
        if start >= 0:
            try:
                return json.JSONDecoder().raw_decode(text[start:])[0]
            except json.JSONDecodeError:
                pass
        log.warning("unparseable RCAC reply: %r", text[:200])
    return {}


def _record(prompt: str, reply: str | None, seconds: float) -> None:
    if LOG_PATH:
        with _log_lock, LOG_PATH.open("a") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": MODEL,
                                "seconds": round(seconds, 1), "prompt": prompt, "reply": reply}) + "\n")
