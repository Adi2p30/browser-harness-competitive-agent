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


class Fetcher:
    def __init__(self, use_browser: bool = True):
        self.use_browser = use_browser
        self.browser_ok = 0
        self.browser_fail = 0
        self._h = None
        self._tab = None
        self._lock = threading.Lock()

    # browser-harness, wired to the same daemon the repo's ./browser-harness launcher uses
    def _harness(self):
        if self._h is None:
            root = Path(__file__).resolve().parents[2]
            dev_id = subprocess.run(["cksum"], input=str(root), text=True, capture_output=True).stdout.split()[0]
            os.environ.setdefault("BH_HOME", str(root / ".browser-harness-dev"))
            os.environ.setdefault("BH_RUNTIME_DIR", f"/tmp/bh-dev-{dev_id}/runtime")
            os.environ.setdefault("BH_RUNTIME_DIR_SHARED", "1")
            os.environ.setdefault("BH_TMP_DIR", str(root / ".browser-harness-dev" / "tmp"))
            os.environ.setdefault("BH_TMP_DIR_SHARED", "1")
            from browser_harness import helpers
            from browser_harness.admin import ensure_daemon
            ensure_daemon()
            self._h = helpers
        return self._h

    def browser(self):
        """The harness helpers with this fetcher's tab active, or None if the browser is unusable."""
        if not self.use_browser:
            return None
        try:
            h = self._harness()
            self._tab = self._tab or h.new_tab()
            h.switch_tab(self._tab)
            return h
        except Exception as exc:  # daemon down, Chrome not debuggable, ...
            log.warning("browser unavailable (%s); using plain HTTP", exc)
            self.use_browser = False
            return None

    def _via_browser(self, url: str) -> dict | None:
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
                if self.browser_ok == 0 and self.browser_fail >= 5:
                    log.error("browser failed 5 times and never worked; switching to plain HTTP")
                    self.use_browser = False
                return None
        if not str(final).startswith(("http://", "https://")):
            return None
        self.browser_ok += 1
        title, text, links = html_to_page(html, final)
        log.debug("browser %s: %d chars, %d links, %d elements expanded", url, len(text), len(links), clicked)
        return {"final_url": final, "kind": "html", "title": title, "text": text, "links": links, "via": "browser"}

    def _via_http(self, url: str) -> dict | None:
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
        if "html" not in ctype:
            return None
        title, text, links = html_to_page(resp.text, resp.url)
        return {"final_url": resp.url, "kind": "html", "title": title, "text": text, "links": links, "via": "http"}

    def fetch(self, url: str) -> dict:
        is_pdf = urlparse(url).path.lower().endswith(".pdf")
        got = None if is_pdf else self._via_browser(url)
        if got is None or len(got["text"]) < MIN_TEXT:
            got = self._via_http(url) or got
        if got is None:
            return {"url": url, "status": "unreadable"}
        status = "ok" if len(got["text"]) >= MIN_TEXT else "empty"
        return {"url": url, "status": status, **got}


def is_same_site(url: str, start: str) -> bool:
    return site(url) == site(start)


def clean_url(url: str) -> str:
    url = urldefrag(url)[0]
    return re.sub(r"[?&](utm_[a-z]+|fbclid|gclid)=[^&]*", "", url).rstrip("?&")
