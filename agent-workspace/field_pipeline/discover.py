"""Stage 1: find candidate pages for one row without an LLM.

Sources: the row's start link, the links on it (and on the best next pages), the school's sitemap, and optionally a
Bing search through the real browser. Each URL is scored per field group, and the page budget is split across
groups so every group gets its own best pages instead of one global crawl order.
"""
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from .fetch import HEADERS, SKIP_EXT, Fetcher, clean_url, is_same_site, site
from .fields import GROUPS
from .store import Store

log = logging.getLogger(__name__)
OLD_YEAR = re.compile(r"(?<!\d)(20(?:0\d|1\d|2[0-4]))(?!\d)")  # 2000-2024 in a URL = archived content
UNDERGRAD_URL = re.compile(r"undergrad|freshm[ae]n|first-year|high-school|/admission/apply(/|\.html)", re.I)
NOISE = re.compile(r"/(news|events?|calendar|stories|blog|people|directory|faculty-profiles?|login|sso|cas|"
                   r"give|giving|donate|athletics|map|jobs|employment-opportunities|category|tag|course)(/|$)|mailto:|tel:", re.I)


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.casefold().replace("-", " ").replace("_", " ")))


PROGRAM_SEGMENT = re.compile(r"/(programs?|degrees?|majors?|masters?|graduate-programs|academics)/", re.I)
GENERIC_WORDS = {"online", "master", "masters", "science", "program", "degree", "off", "campus", "mba"}


def program_words(program: str) -> set[str]:
    return {w[:5] for w in tokens(program) - GENERIC_WORDS if len(w) >= 4}


def foreign_program(url: str, words: set[str], start: str = "") -> bool:
    """True for a page under a program path (/program/<slug>, /degrees/...) that never names the target program.
    The start link and pages on its own path (its parents and children) always belong to the program."""
    if not words:
        return False
    parsed = urlparse(url)
    if start:
        a, b = parsed.netloc + parsed.path.rstrip("/"), urlparse(start).netloc + urlparse(start).path.rstrip("/")
        if a.startswith(b) or (b.startswith(a) and len(parsed.path.strip("/")) > 0):
            return False
    m = PROGRAM_SEGMENT.search(parsed.path)
    if not m:
        return False
    rest = (parsed.netloc + parsed.path[m.end():]).casefold().replace("-", "").replace("_", "")
    return bool(parsed.path[m.end():].strip("/")) and not any(w in rest for w in words)


# First path segments that are university-wide offices (fine to visit from any program); any other first segment
# that differs from the start link's is probably another school's section (e.g. /wcl/ = law school).
SHARED_SECTIONS = re.compile(r"^(admissions?|graduate|grad|financial|finaid|financial-aid|tuition|bursar|student-?accounts|"
                             r"careers?|career-center|students?|student-affairs|student-life|international|provost|"
                             r"registrar|academics|online|about|diversity|inclusion|belonging|cost|aid)$", re.I)


def section_bonus(url: str, start: str) -> int:
    u, s = urlparse(url), urlparse(start)
    if u.netloc != s.netloc:
        return 0
    first = lambda p: (p.path.strip("/").split("/") or [""])[0]
    if first(u) == first(s):
        return 3
    return 0 if not first(u) or SHARED_SECTIONS.match(first(u)) else -6


def score(url: str, anchor: str, group: str, words: set[str], start: str) -> float:
    if NOISE.search(url) or UNDERGRAD_URL.search(f"{url} {anchor}") or foreign_program(url, words, start):
        return -10
    seen = tokens(f"{anchor} {urlparse(url).path}")
    s = 3 * sum(1 for w in GROUPS[group].url_words if any(t.startswith(w) for t in seen))
    s += 2 * sum(1 for w in words if w in urlparse(url).path.casefold().replace("-", ""))
    if urlparse(url).netloc == urlparse(start).netloc:
        s += 2  # program's own (sub)site
    s += section_bonus(url, start)
    s -= 4 * len(OLD_YEAR.findall(url))
    if url.lower().endswith(".pdf"):
        s += 1 if group in ("cost", "admissions", "profile", "outcomes") else -2
    return s


def sitemap_urls(start: str, limit: int = 5000) -> list[str]:
    """URLs from robots.txt sitemaps or /sitemap.xml of the start link's host (index files followed one level)."""
    root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
    maps = []
    try:
        robots = requests.get(f"{root}/robots.txt", headers=HEADERS, timeout=10)
        if robots.ok:
            maps = re.findall(r"(?im)^sitemap:\s*(\S+)", robots.text)
    except requests.RequestException:
        pass
    maps = maps or [f"{root}/sitemap.xml"]
    urls: list[str] = []
    for i, sm in enumerate(maps[:5]):
        urls += _read_sitemap(sm, limit - len(urls), depth=0)
        if len(urls) >= limit:
            break
    log.info("sitemap %s: %d urls", root, len(urls))
    return urls


def _read_sitemap(url: str, limit: int, depth: int) -> list[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if not resp.ok:
            return []
        tree = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError):
        return []
    locs = [el.text.strip() for el in tree.iter() if el.tag.endswith("loc") and el.text]
    if tree.tag.endswith("sitemapindex"):
        if depth > 0:
            return []
        # prefer page sitemaps over post/news/media ones
        locs.sort(key=lambda u: bool(re.search(r"post|news|event|media|image|video|tag|author", u, re.I)))
        out: list[str] = []
        for child in locs[:8]:
            out += _read_sitemap(child, limit - len(out), depth + 1)
            if len(out) >= limit:
                break
        return out
    return locs[:limit]


class Discoverer:
    """Per row: 1) search (DuckDuckGo/Bing with site:<domain>, and the school's own search box) for each field
    group, opening the top hits; 2) crawl outward from the start link and the found pages, choosing links per group
    by keyword score; the school's sitemap adds more candidate URLs. No LLM involved."""

    def __init__(self, store: Store, fetcher: Fetcher, per_group: int = 4, second_round: int = 2,
                 use_search: bool = True, search_hits: int = 3):
        from .search import Searcher
        self.store, self.fetcher = store, fetcher
        self.per_group, self.second_round = per_group, second_round
        self.use_search, self.search_hits = use_search, search_hits
        self.searcher = Searcher(fetcher)
        self._sitemaps: dict[str, list[str]] = {}

    def get(self, url: str) -> dict:
        url = clean_url(url)
        page = self.store.page(url)
        if page is not None and page["status"] != "ok" and page.get("via") != "browser" and self.fetcher.use_browser:
            page = None  # failed over plain HTTP before; give the browser a try
        if page is None:
            page = self.fetcher.fetch(url)
            self.store.save_page(page)
            log.info("fetched [%s/%s] %s (%d chars)", page.get("via", "-"), page["status"], url, len(page.get("text") or ""))
        return page

    def run(self, row_key: str, start: str, program: str, groups: list[str], university: str = "") -> list[dict]:
        words = program_words(program)
        pool: dict[str, str] = {}  # url -> anchor text
        done: set[str] = set()

        def add(url: str, anchor: str = "") -> None:
            url = clean_url(url)
            if is_same_site(url, start) and url not in pool and not urlparse(url).path.lower().endswith(SKIP_EXT):
                pool[url] = anchor

        def open_page(url: str, group: str, s: float, source: str, follow: bool = True) -> None:
            self.store.add_candidate(row_key, url, group, s, source)
            if url in done:
                return
            done.add(url)
            page = self.get(url)
            if follow:  # outside pages (ranking sites) are read but their links are not crawled
                for a, link in page.get("links") or []:
                    add(link, a)

        open_page(clean_url(start), "*", 100, "start")
        host = urlparse(start).netloc
        if host not in self._sitemaps:
            self._sitemaps[host] = sitemap_urls(start)
        for u in self._sitemaps[host]:
            add(u)

        if self.use_search:
            for group in groups:
                g = GROUPS[group]
                if not g.search:
                    continue
                hits = self.searcher.find(start, university.title(), program.title(), g.search, g.sites)
                kept = [(src, t, clean_url(u)) for src, t, u in hits
                        if g.sites or score(clean_url(u), t, group, words, start) > -10][:self.search_hits * 2]
                log.info("search %-16s -> %d hit(s): %s", group, len(kept), [u for _, _, u in kept][:6])
                for rank, (src, title, url) in enumerate(kept):
                    if not g.sites:
                        add(url, title)
                    if rank < self.search_hits:
                        open_page(url, group, 50 - rank, f"search:{src}", follow=not g.sites)

        for k in (self.per_group, self.second_round):
            for group in [g for g in groups if not GROUPS[g].sites]:
                ranked = sorted(((score(u, a, group, words, start), u) for u, a in pool.items() if u not in done),
                                reverse=True)
                for s, u in ranked[:k]:
                    if s > 0:
                        open_page(u, group, s, "crawl")
        return self.store.row_pages(row_key)
