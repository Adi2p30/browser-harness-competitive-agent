"""Find pages by searching, inside the real browser (browser-harness): web search engines restricted to the school's
domain, and the school's own site-search box. Returns (title, url) lists; an engine that shows a challenge/CAPTCHA
page is switched off for the rest of the run (challenges are never solved or bypassed).
"""
import base64
import logging
import re
from urllib.parse import parse_qs, quote_plus, urlparse

from .fetch import Fetcher, clean_url, is_same_site

log = logging.getLogger(__name__)
CHALLENGE = re.compile(r"captcha|are you a robot|unusual traffic|verify you are human|challenge|access denied", re.I)

# Mark the most likely site-search input, return what was found. Prefers visible inputs inside a form.
MARK_SEARCH_INPUT = r"""
(() => {
  const sel = 'input[type=search], input[name=q], input[name=s], input[name=query], input[name=search], '
            + 'input[name=keys], input[name=k], input[name=searchtext], input[aria-label*="earch"], input[placeholder*="earch"]';
  const all = [...document.querySelectorAll(sel)].filter(e => e.type !== 'hidden');
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const el = all.find(e => visible(e) && e.form) || all.find(e => e.form) || all.find(visible);
  if (!el) return null;
  el.setAttribute('data-fp-search', '1');
  return {visible: visible(el), form: !!el.form, action: el.form ? el.form.action : ''};
})()
"""


def bing_target(href: str) -> str:
    """Bing wraps results as bing.com/ck/a?...&u=a1<base64url(target)>; return the real target URL."""
    if "bing.com/ck/" not in href:
        return href
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        try:
            b64 = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(b64).decode("utf-8", "replace")
        except ValueError:
            pass
    return href


class Searcher:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.disabled: set[str] = set()
        self.stats = {"ddg": 0, "bing": 0, "site": 0}

    def _open(self, url: str):
        h = self.fetcher.browser()
        h.goto_url(url)
        h.wait_for_load(15)
        h.wait(1.5)
        return h

    def _challenged(self, h, engine: str) -> bool:
        text = (h.js("document.title + ' ' + (document.body ? document.body.innerText.slice(0, 800) : '')") or "")
        if CHALLENGE.search(text):
            log.warning("%s showed a challenge page; %s search off for this run", engine, engine)
            self.disabled.add(engine)
            return True
        return False

    def ddg(self, query: str, n: int = 8) -> list[tuple[str, str]]:
        if "ddg" in self.disabled:
            return []
        with self.fetcher._lock:
            try:
                h = self._open(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
                if self._challenged(h, "ddg"):
                    return []
                raw = h.js("[...document.querySelectorAll('a.result__a')].slice(0, %d).map(a => [a.innerText, a.href])" % n) or []
            except Exception as exc:
                log.warning("ddg search failed: %s", exc)
                return []
        out = []
        for title, href in raw:  # results are //duckduckgo.com/l/?uddg=<target>
            target = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            if target.startswith("http") and "duckduckgo.com" not in urlparse(target).netloc:
                out.append((title, target))
        self.stats["ddg"] += 1
        return out

    def bing(self, query: str, n: int = 8) -> list[tuple[str, str]]:
        if "bing" in self.disabled:
            return []
        with self.fetcher._lock:
            try:
                h = self._open(f"https://www.bing.com/search?q={quote_plus(query)}")
                if self._challenged(h, "bing"):
                    return []
                raw = h.js("[...document.querySelectorAll('li.b_algo h2 a')].slice(0, %d).map(a => [a.innerText, a.href])" % n) or []
            except Exception as exc:
                log.warning("bing search failed: %s", exc)
                return []
        self.stats["bing"] += 1
        return [(t, bing_target(u)) for t, u in raw if u.startswith("http")]

    def site_search(self, start: str, query: str, n: int = 10) -> list[tuple[str, str]]:
        """Type the query into the school's own search box (found on the start page) and collect result links that
        are on the school's site and were not already links on the start page (so menus and footers drop out)."""
        if "site" in self.disabled:
            return []
        with self.fetcher._lock:
            try:
                h = self._open(start)
                before = set(h.js("[...document.links].map(a => a.href)") or [])
                found = h.js(MARK_SEARCH_INPUT)
                if not found:
                    log.info("no site-search box on %s", start)
                    return []
                if found["visible"]:
                    h.fill_input('[data-fp-search="1"]', query)
                    h.press_key("Enter")
                else:
                    h.js("(q => { const e = document.querySelector('[data-fp-search=\"1\"]'); e.value = q; e.form.submit(); })"
                         f"({query!r})")
                h.wait_for_load(15)
                h.wait(3)  # Google CSE / JS result widgets render after load
                links = h.js("[...document.querySelectorAll('main a[href], #main a[href], .gsc-results a[href], "
                             "[role=main] a[href], body a[href]')].map(a => [a.innerText.trim(), a.href])") or []
            except Exception as exc:
                log.warning("site search failed on %s: %s", start, exc)
                return []
        out, seen = [], set()
        for title, url in links:
            url = clean_url(url)
            if (url in before or url in seen or not is_same_site(url, start) or not title
                    or re.search(r"[?&](q|s|query|search|keys)=|/search", url, re.I)):
                continue
            seen.add(url)
            out.append((title[:100], url))
            if len(out) >= n:
                break
        self.stats["site"] += 1
        return out

    def find(self, start: str, university: str, program: str, short: str) -> list[tuple[str, str, str]]:
        """[(source, title, url)] from every enabled engine, school-site results only. Engines are queried with the
        university's name rather than a site: operator (bots often get site: ignored); off-site results are dropped."""
        q = " ".join(f"{university} {program} {short}".split())
        hits = []
        for engine, search in (("bing", lambda: self.bing(q, 10)), ("ddg", lambda: self.ddg(q, 10)),
                               ("site", lambda: self.site_search(start, f"{program} {short}".strip()))):
            results = search()
            hits += [(engine, t, u) for t, u in results if is_same_site(u, start)]
            if hits and engine != "site":
                break  # one web engine is enough; try the next only if this one gave nothing
        return hits
