"""Fetch a URL into a stored page: {url, final_url, kind, title, text, links, via, status}.

HTML goes through the real browser (browser-harness) so JavaScript-rendered pages, accordions and tabs are read;
plain HTTP is the fallback when the browser is not attached. PDFs are downloaded and read with pypdf.
"""
import io
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/131.0 Safari/537.36 (competitive-analysis research; Purdue)"}
MIN_TEXT = 200
MAX_PDF_PAGES = 40
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3", ".mov",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".ics")

# Open collapsed content before reading: <details>, accordion buttons and tabs. Never clicks real links.
EXPAND_JS = r"""
(() => {
  document.querySelectorAll('details:not([open])').forEach(d => d.open = true);
  const sel = 'button[aria-expanded="false"], [role="button"][aria-expanded="false"], [role="tab"][aria-selected="false"],'
            + ' .accordion-button.collapsed, .accordion__button, summary';
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (n >= 60 || el.closest('nav, header, [role="navigation"], [role="menubar"]')) continue;
    if (el.tagName === 'A' && el.getAttribute('href') && !el.getAttribute('href').startsWith('#')) continue;
    try { el.click(); n++; } catch (e) {}
  }
  return n;
})()
"""


def site(url: str) -> str:
    """Registrable-ish domain: 'business.purdue.edu' -> 'purdue.edu'."""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("ac", "co", "edu") and len(parts[-1]) == 2 \
        else ".".join(parts[-2:])


def html_to_page(html: str, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(title, text, [(anchor, absolute url)]). Table rows become 'cell | cell' lines so tuition tables stay readable."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    title = " ".join((soup.title.get_text() if soup.title else "").split())
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(url, a["href"].strip()))[0]
        if urlparse(href).scheme in ("http", "https") and href not in seen and not href.lower().endswith(SKIP_EXT):
            seen.add(href)
            links.append((" ".join(a.get_text(" ").split())[:100], href))
    for tr in soup.find_all("tr"):
        cells = [" ".join(c.get_text(" ").split()) for c in tr.find_all(["th", "td"])]
        tr.replace_with(soup.new_string("\n" + " | ".join(c for c in cells if c) + "\n"))
    for br in soup.find_all(["br", "p", "li", "h1", "h2", "h3", "h4", "h5", "div", "section"]):
        br.insert_before("\n")
    lines = (" ".join(line.split()) for line in soup.get_text().splitlines())
    text = "\n".join(line for line in lines if line)
    return title, text, links


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES])


CLOUD_NAME = "fieldpipe"  # BU_NAME of the Browser Use cloud daemon this pipeline starts and stops


class BrowserUnavailable(RuntimeError):
    """The browser could not be started or reached; the run stops instead of silently using plain HTTP."""


class Fetcher:
    """Every HTML page is opened in a real browser through browser-harness. Plain HTTP is used only for files a
    browser does not render as text (PDFs, sitemap XML), or for HTML when http_fallback is on.

    Cloud mode (Browser Use Cloud) rotates sessions: a fresh cloud browser every `schools_per_session` schools or
    before `session_minutes` runs out, and again if a session dies mid-school."""

    def __init__(self, mode: str = "auto", session_minutes: int = 15, schools_per_session: int = 2,
                 http_fallback: bool = False):
        if mode == "auto":
            mode = "cloud" if _env_has("BROWSER_USE_API_KEY") else "local"
        self.mode = mode
        self.use_browser = mode != "none"
        self.session_minutes = session_minutes
        self.schools_per_session = schools_per_session
        self.http_fallback = http_fallback or mode == "none"
        self.cloud_started = False
        self.session_started_at = 0.0
        self.session_schools = 0
        self.sessions = 0
        self.browser_ok = 0
        self.browser_fail = 0
        self._h = None
        self._admin = None
        self._tab = None
        self._lock = threading.RLock()

    # browser-harness, wired to the same runtime dirs the repo's ./browser-harness launcher uses
    def _harness(self):
        if self._h is None:
            root = Path(__file__).resolve().parents[2]
            dev_id = subprocess.run(["cksum"], input=str(root), text=True, capture_output=True).stdout.split()[0]
            os.environ.setdefault("BH_HOME", str(root / ".browser-harness-dev"))
            os.environ.setdefault("BH_RUNTIME_DIR", f"/tmp/bh-dev-{dev_id}/runtime")
            os.environ.setdefault("BH_RUNTIME_DIR_SHARED", "1")
            os.environ.setdefault("BH_TMP_DIR", str(root / ".browser-harness-dev" / "tmp"))
            os.environ.setdefault("BH_TMP_DIR_SHARED", "1")
            if self.mode == "cloud":
                os.environ["BU_NAME"] = CLOUD_NAME  # helpers bind to the daemon named at import time
                os.environ.setdefault("BH_OPEN_LIVE_URL", "0")
            from browser_harness import admin, helpers
            self._admin, self._h = admin, helpers
            if self.mode == "local":
                admin.ensure_daemon()
        if self.mode == "cloud" and not self.cloud_started:
            self._start_cloud()
        return self._h

    def _start_cloud(self) -> None:
        if self._admin.daemon_alive(CLOUD_NAME):  # leftover from a crashed run
            self._admin.stop_remote_daemon(CLOUD_NAME)
        browser = self._admin.start_remote_daemon(CLOUD_NAME, timeout=self.session_minutes)
        self.cloud_started, self.session_started_at, self.session_schools = True, time.time(), 0
        self.sessions += 1
        self._tab = None
        log.info("cloud browser session %d started (id %s, %d-min limit); live view: %s", self.sessions,
                 browser.get("id"), self.session_minutes, browser.get("liveUrl"))

    def _stop_cloud(self) -> None:
        if self.cloud_started:
            self.cloud_started = False
            self._tab = None
            try:
                self._admin.stop_remote_daemon(CLOUD_NAME)
                log.info("cloud browser session %d stopped after %.1f min", self.sessions,
                         (time.time() - self.session_started_at) / 60)
            except Exception as exc:
                log.error("could not stop cloud browser %r: %s -- stop it in the Browser Use dashboard", CLOUD_NAME, exc)

    def begin_school(self) -> None:
        """Call before each school: rotates the cloud session after N schools or when time is nearly up."""
        if self.mode != "cloud" or not self.cloud_started:
            return
        age = time.time() - self.session_started_at
        if self.session_schools >= self.schools_per_session or age > max(60, self.session_minutes * 60 - 300):
            log.info("rotating cloud browser (%d school(s), %.1f min used)", self.session_schools, age / 60)
            with self._lock:
                self._stop_cloud()
        self.session_schools += 1

    def close(self) -> None:
        """Stop the cloud browser (cloud browsers bill until stopped)."""
        if self.mode == "cloud" and self._admin:
            self._stop_cloud()

    def browser(self):
        """The harness helpers with this fetcher's tab active. Raises BrowserUnavailable if it cannot be reached."""
        if not self.use_browser:
            return None
        try:
            h = self._harness()
            self._tab = self._tab or h.new_tab()
            h.switch_tab(self._tab)
            return h
        except Exception as exc:  # daemon down, Chrome not debuggable, cloud quota, ...
            raise BrowserUnavailable(f"browser-harness ({self.mode}) unavailable: {exc}") from exc

    def _via_browser(self, url: str, retry: bool = True) -> dict | None:
        with self._lock:
            h = self.browser()
            if h is None:
                return None
            try:
                h.goto_url(url)
                h.wait_for_load(20)
                for _ in range(8):  # JS-rendered pages fill in after the load event
                    try:
                        if len(h.js("document.body ? document.body.innerText : ''") or "") >= MIN_TEXT:
                            break
                    except Exception:
                        pass
                    h.wait(1)
                clicked = h.js(EXPAND_JS) or 0
                if clicked:
                    h.wait(1.5)
                html, final = h.js("[document.documentElement.outerHTML, location.href]")
            except Exception as exc:
                self.browser_fail += 1
                log.warning("browser fetch failed %s: %s", url, exc)
                if retry and self.mode == "cloud":  # the session may have expired: new session, one retry
                    log.info("restarting cloud browser and retrying once")
                    self._stop_cloud()
                    return self._via_browser(url, retry=False)
                return None
        if not str(final).startswith(("http://", "https://")):
            return None
        self.browser_ok += 1
        title, text, links = html_to_page(html, final)
        log.debug("browser %s: %d chars, %d links, %d elements expanded", url, len(text), len(links), clicked)
        return {"final_url": final, "kind": "html", "title": title, "text": text, "links": links, "via": "browser"}

    def _via_http(self, url: str, html_ok: bool) -> dict | None:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=25)
        except requests.RequestException as exc:
            log.warning("http fetch failed %s: %s", url, exc)
            return None
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200:
            log.info("http %s for %s", resp.status_code, url)
            return None
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            try:
                text = pdf_text(resp.content)
            except Exception as exc:
                log.warning("pdf unreadable %s: %s", url, exc)
                return None
            return {"final_url": resp.url, "kind": "pdf", "title": Path(urlparse(resp.url).path).name, "text": text,
                    "links": [], "via": "http"}
        if "html" not in ctype or not html_ok:
            return None
        title, text, links = html_to_page(resp.text, resp.url)
        return {"final_url": resp.url, "kind": "html", "title": title, "text": text, "links": links, "via": "http"}

    def fetch(self, url: str) -> dict:
        if urlparse(url).path.lower().endswith(".pdf"):
            got = self._via_http(url, html_ok=False)
        else:
            got = self._via_browser(url)
            if got is not None and got["final_url"].lower().endswith(".pdf"):  # browser landed on a PDF viewer
                got = self._via_http(got["final_url"], html_ok=False)
            elif (got is None or len(got["text"]) < MIN_TEXT) and self.http_fallback:
                got = self._via_http(url, html_ok=True) or got
        if got is None:
            return {"url": url, "status": "unreadable"}
        status = "ok" if len(got["text"]) >= MIN_TEXT else "empty"
        return {"url": url, "status": status, **got}


def _env_has(key: str) -> bool:
    if os.environ.get(key):
        return True
    env = Path(__file__).resolve().parents[2] / ".env"
    return env.is_file() and any(line.split("=", 1)[0].strip() == key and line.split("=", 1)[1].strip()
                                 for line in env.read_text().splitlines() if "=" in line)


def is_same_site(url: str, start: str) -> bool:
    return site(url) == site(start)


def clean_url(url: str) -> str:
    url = urldefrag(url)[0]
    return re.sub(r"[?&](utm_[a-z]+|fbclid|gclid)=[^&]*", "", url).rstrip("?&")
