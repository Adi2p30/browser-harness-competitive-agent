"""Source health check (field_pipeline.health): judging stored quotes against freshly fetched pages."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
pytest.importorskip("openpyxl")

from field_pipeline import health  # noqa: E402
from field_pipeline.__main__ import read_recheck  # noqa: E402
from field_pipeline.fetch import OCR_MARK  # noqa: E402
from field_pipeline.health import (dead_reason, judge, judge_link, near_match, quote_key, same_url,  # noqa: E402
                                   write_outputs)
from field_pipeline.store import Store  # noqa: E402

URL = "https://krannert.purdue.edu/masters/programs/hrm/tuition"
FILLER = "Krannert School of Management graduate programs prepare leaders for a changing world. " * 20
QUOTE = "Tuition for the 2026-2027 academic year is $1,250 per credit hour for all students."


def page(text, final=URL, status="ok", title="Tuition | Krannert", code=200):
    return {"url": URL, "final_url": final, "status": status, "text": text, "title": title, "code": code}


def test_quote_still_on_page_is_ok():
    text = FILLER + "Tuition for the 2026–2027 academic year is $1,250 per   credit hour for all students. " + FILLER
    assert judge(QUOTE, page(text), URL)["result"] == "ok"  # spacing / dash / punctuation ignored, like validate


def test_redirected_page_with_quote_is_moved():
    r = judge(QUOTE, page(FILLER + QUOTE, final="https://krannert.purdue.edu/hrm/cost"), URL)
    assert r["result"] == "moved" and r["final_url"].endswith("/hrm/cost")
    assert judge(QUOTE, page(FILLER + QUOTE, final=URL.replace("https://", "https://www.") + "/"), URL)["result"] == "ok"


def test_updated_number_is_changed_with_snippet():
    text = FILLER + "Tuition for the 2026-2027 academic year is $1,310 per credit hour for all students. " + FILLER
    r = judge(QUOTE, page(text), URL)
    assert r["result"] == "changed"
    assert "1,310" in r["snippet"] and r["score"] >= health.NEAR
    assert "1,310" in r["detail"] and "1,250" in r["detail"]


def test_new_cycle_is_changed():
    text = FILLER + "Tuition for the 2027-2028 academic year is $1,250 per credit hour for all students. " + FILLER
    assert judge(QUOTE, page(text), URL)["result"] == "changed"


def test_rewritten_page_is_missing():
    text = FILLER + "Contact the graduate office for current program costs and funding opportunities. " + FILLER
    assert judge(QUOTE, page(text), URL)["result"] == "missing"


def test_short_quote_never_near_matches():
    assert near_match("$1,250", FILLER + "$1,310 per credit") == (0.0, "")
    assert judge("$1,250", page(FILLER + "$1,310"), URL)["result"] == "missing"


def test_ellipsis_quote_parts():
    quote = "Tuition for the 2026-2027 academic year is $1,250 ... deposit of $500 is due within two weeks of admission"
    text = (FILLER + "Tuition for the 2026-2027 academic year is $1,250 per credit. " + FILLER
            + "A deposit of $750 is due within two weeks of admission. ")
    r = judge(quote, page(text), URL)
    assert r["result"] == "changed" and "750" in r["snippet"] and "1,250" not in r["snippet"]


@pytest.mark.parametrize("pg, why", [
    (page("", status="unreadable", code=404), "HTTP 404"),
    (page(FILLER, title="Page Not Found | Purdue"), "not found"),
    (page(FILLER, final="https://krannert.purdue.edu/"), "home page"),
])
def test_dead_pages(pg, why):
    r = judge(QUOTE, pg, URL)
    assert r["result"] == "dead" and why in r["detail"]
    assert judge_link(pg, URL)["result"] == "dead"


def test_unreadable_is_not_evidence():
    assert judge(QUOTE, page("Checking your browser...", status="empty", code=403), URL)["result"] == "unreadable"
    assert judge(QUOTE, {"url": URL, "status": "unreadable"}, URL)["result"] == "unreadable"


def test_quote_from_ocr_is_unverifiable():
    old = FILLER + f"\n{OCR_MARK}\n[image text: Tuition for the 2026-2027 academic year is $1,250 per credit hour]"
    quote = "Tuition for the 2026-2027 academic year is $1,250 per credit hour"
    r = judge(quote, page(FILLER + "Our class profile"), URL, old_text=old)
    assert r["result"] == "unreadable" and "OCR" in r["detail"]
    old_style = FILLER + "\n[image text: Tuition for the 2026-2027 academic year is $1,250 per credit hour]"
    assert judge(quote, page(FILLER), URL, old_text=old_style)["result"] == "unreadable"
    dom = FILLER + quote  # a quote that was in the page's own text is judged normally
    assert judge(quote, page(FILLER), URL, old_text=dom)["result"] == "missing"


def test_link_only_facts():
    assert judge_link(page(FILLER), URL)["result"] == "live"
    assert judge_link(page(FILLER, final="https://krannert.purdue.edu/new/tuition"), URL)["result"] == "moved"
    assert judge_link(page("", status="unreadable", code=None), URL)["result"] == "unreadable"


def test_helpers():
    assert same_url("http://www.purdue.edu/a/", "https://purdue.edu/a")
    assert not same_url("https://purdue.edu/a", "https://purdue.edu/b")
    assert quote_key("Tuition: $1,250.") == quote_key("tuition $1250") != quote_key("tuition $1310")
    assert quote_key("") == quote_key(None) == ""
    assert dead_reason(page("Call 765-494-0404 for admissions. " + FILLER), URL) == ""


class FakeFetcher:
    def __init__(self, pages, http=None):
        self.pages, self.http = pages, http or {}

    def fetch(self, url, http_ok=False):
        return dict(self.pages[url])

    def _via_http(self, url, html_ok=True):
        return self.http.get(url)


def test_check_url_saves_page_and_retries_over_http(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "probe", lambda url: 200)
    store = Store(tmp_path / "p.sqlite")
    fact = {"sheet": "MS HRM", "row": 3, "university": "PURDUE", "program": "HRM", "field": "Tuition",
            "status": "found", "value": "$1,250", "url": URL, "quote": QUOTE}
    browser_copy = page(FILLER + "Tuition for the 2026-2027 academic year is $1,310 per credit hour for all students.")
    got = health.check_url(URL, [fact], FakeFetcher({URL: browser_copy}), store, "t1")
    assert got[0]["result"] == "changed"
    assert store.page(URL)["text"] == browser_copy["text"]  # refreshed cache
    got = health.check_url(URL, [fact], FakeFetcher({URL: browser_copy}, {URL: {"text": FILLER + QUOTE}}), store, "t2")
    assert got[0]["result"] == "ok" and "plain HTTP" in got[0]["detail"]

    store.save_health(got)
    latest = store.latest_health()
    assert latest[(URL, quote_key(QUOTE))]["result"] == "ok"
    assert store.last_checked(URL) is not None


def test_dead_page_does_not_replace_cached_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "probe", lambda url: 404)
    store = Store(tmp_path / "p.sqlite")
    store.save_page(page(FILLER + QUOTE))
    fact = {"sheet": "S", "row": 2, "university": "U", "program": "", "field": "F", "status": "found",
            "value": "v", "url": URL, "quote": QUOTE}
    got = health.check_url(URL, [fact], FakeFetcher({URL: page(FILLER, title="404 Not Found")}), store, "t")
    assert got[0]["result"] == "dead"
    assert QUOTE in store.page(URL)["text"]


def test_recheck_csv_round_trip(tmp_path):
    base = {"university": "U", "program": "P", "url": URL, "final_url": URL, "quote": "q", "snippet": "",
            "score": None, "detail": "", "value": "v"}
    rows = [{**base, "sheet": "MS HRM", "row": 3, "field": "Tuition", "status": "found", "result": "changed"},
            {**base, "sheet": "MS HRM", "row": 3, "field": "Deadline", "status": "found", "result": "ok"},
            {**base, "sheet": "MS HRM", "row": 4, "field": "GPA", "status": "prior-year", "result": "dead"},
            {**base, "sheet": "MS HRM", "row": None, "field": "GPA", "status": "entered", "result": "dead"},
            {**base, "sheet": "MS HRM", "row": 5, "field": "Credits", "status": "entered", "result": "missing"}]
    recheck = write_outputs(tmp_path, rows)
    assert [(r["row"], r["field"]) for r in recheck] == [(3, "Tuition"), (5, "Credits")]
    assert read_recheck(tmp_path / "recheck.csv") == {"MS HRM:3": {"Tuition"}, "MS HRM:5": {"Credits"}}
    assert (tmp_path / "health.csv").read_text().count("\n") == 6


def test_knowledge_base_attaches_latest_check(tmp_path):
    from knowledge_base.__main__ import attach_health, write_sqlite
    store = Store(tmp_path / "p.sqlite")
    row = {"url": URL, "quote_key": quote_key(QUOTE), "result": "changed", "snippet": "$1,310", "checked_at": 1.0}
    store.save_health([row, {**row, "result": "ok", "snippet": "", "checked_at": 2.0}])
    store.save_health([{**row, "url": URL + "/other", "checked_at": 3.0}])
    kb = {"programs": [], "sheets": [], "facts": [
        {"p": 0, "f": "Tuition", "v": "$1,250", "n": 1250, "c": "2026-2027", "s": "found", "u": URL + "?utm_source=x",
         "q": QUOTE},
        {"p": 0, "f": "Other", "v": "x", "n": None, "c": "2026-2027", "s": "found", "u": URL + "/other", "q": QUOTE},
        {"p": 0, "f": "None", "v": "y", "n": None, "c": "2026-2027", "s": "entered", "u": "", "q": ""}]}
    assert attach_health(kb, tmp_path / "p.sqlite") == 2
    assert kb["facts"][0]["h"] == "ok" and "hn" not in kb["facts"][0]  # newest check wins
    assert kb["facts"][1]["h"] == "changed" and kb["facts"][1]["hn"] == "$1,310"
    assert "h" not in kb["facts"][2]
    write_sqlite(kb, tmp_path / "kb.sqlite")  # facts with and without health columns both insert
