import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("openpyxl")

from field_pipeline.discover import category, foreign_program, program_words, same_school, score  # noqa: E402
from field_pipeline.extract import better, candidates  # noqa: E402
from field_pipeline.fetch import html_to_page, site  # noqa: E402
from field_pipeline.fields import field_for, link_target, plan, usnews_kind, usnews_program_level  # noqa: E402
from field_pipeline.retrieve import chunk_pages, top_chunks  # noqa: E402
from field_pipeline.validate import check, cycle_lag  # noqa: E402


def f(col):
    return field_for(col)


@pytest.mark.parametrize("col,group,kind", [
    ("International (%)", "class", "percent"),
    ("International %", "class", "percent"),
    ("International Tuition Per Credit", "tuition", "money"),
    ("Female (%)", "class", "percent"),
    ("Avg GMAT", "scores", "gmat"),
    ("Accept 3yr-degree from India? (Y/N)", "requirements", "yesno"),
    ("Affinity Groups (brief description)", "community", "text"),
    ("Affinity Groups (Yes/No)", "community", "yesno"),
    ("Co-op / Internship Opportunities (Brief Description)", "experiential", "text"),
    ("Co-op / Internship Opportunities (Yes/No)", "experiential", "yesno"),
    ("Delivery Model (Led by Full Time faculty or Part Time Adjuncts?)", "structure", "text"),
    ("Ranking (USNews)", "rank_usnews", "text"),
])
def test_field_mapping(col, group, kind):
    assert (f(col).group, f(col).kind) == (group, kind)


@pytest.mark.parametrize("col", ["Website Link", "University Name", "In-State ROI - Breakeven Point in Months",
                                 "Average Conferrals (past 3 years)", "Affinity Groups Link"])
def test_skipped_columns(col):
    assert f(col) is None


def test_link_pairing():
    cols = ["Affinity Groups (Yes/No)", "Affinity Groups Link", "Affinity Groups (brief description)",
            "Career Coaching/Consulting Services Offered? (Yes/No)", "Career Coaching/Consulting Services Offered? (Link)"]
    assert link_target("Affinity Groups Link", cols) == "Affinity Groups (Yes/No)"
    assert link_target("Career Coaching/Consulting Services Offered? (Link)", cols).endswith("(Yes/No)")
    fields, links, unmapped = plan(cols)
    assert len(fields) == 3 and len(links) == 2 and not unmapped


@pytest.mark.parametrize("quote,kind,bad", [
    ("Tuition for 2026-2027 is $1,250 per credit hour.", "money", False),
    ("Tuition for 2025-26 is $1,250 per credit hour.", "money", True),
    ("Fall 2026 priority deadline: February 1, 2026", "date", False),
    ("Fall 2027 application opens", "date", True),
    ("Spring 2026 cohort", "text", True),
    ("Spring 2027 start", "text", False),
    ("Class of 2024 average GMAT 640", "gmat", True),
    ("Average GMAT 640", "gmat", False),
    ("Our offices are at 403 W. State St, West Lafayette, IN 47907-2056", "text", False),
    ("Total cost $2,025 per course", "money", False),
])
def test_cycle(quote, kind, bad):
    assert (cycle_lag(quote, kind)[0] != 0) == bad


def test_check_statuses():
    page = "The program costs $1,250 per credit hour for 2026-2027. Average GPA 3.50. 100% online."
    tuition, gpa = f("In-State Tuition Per Credit"), f("Average Undergraduate GPA")
    assert check(tuition, "$1,250", "costs $1,250 per credit hour for 2026-2027", page)[0] == "found"
    assert check(tuition, "$1,350", "costs $1,250 per credit hour for 2026-2027", page)[0] == "bad-value"
    assert check(tuition, "$1,250", "costs $1,250 per credit, always", page)[0] == "bad-quote"
    assert check(gpa, "3.5", "Average GPA 3.50", page)[0] == "found"
    assert check(gpa, "35", "Average GPA 3.50", page)[0] in ("bad-value", "out-of-range")
    assert check(tuition, "$1,250", "costs $1,250 per credit hour for 2026-2027", page, "other")[0] == "other-program"
    assert check(tuition, "null", "", page)[0] == "not-found"
    months = f("Duration (months)")
    assert check(months, "24", "Complete in 2 years", "Complete in 2 years")[0] == "found"


def test_html_to_page_tables_and_links():
    html = """<html><head><title>Tuition</title></head><body><nav><a href="/apply">Apply</a></nav>
    <table><tr><th>Residency</th><th>Per credit</th></tr><tr><td>In-state</td><td>$900</td></tr></table>
    <a href="https://other.com/x">x</a><a href="mailto:a@b.c">m</a><a href="/img.png">i</a></body></html>"""
    title, text, links = html_to_page(html, "https://business.purdue.edu/tuition")
    assert title == "Tuition"
    assert "In-state | $900" in text
    assert ("Apply", "https://business.purdue.edu/apply") in links
    assert all(not u.endswith(".png") and u.startswith("http") for _, u in links)


def test_site():
    assert site("https://business.purdue.edu/x") == "purdue.edu"
    assert site("https://www.ox.ac.uk/") == "ox.ac.uk"


def test_score_prefers_group_pages():
    start = "https://business.purdue.edu/online/ms-economics/"
    w = program_words("Online MS Economics")
    tuition = score("https://business.purdue.edu/online/tuition/", "Tuition & Fees", "tuition", w, start)
    news = score("https://business.purdue.edu/news/2021/story", "Story", "tuition", w, start)
    old = score("https://business.purdue.edu/tuition-2019/", "Tuition", "tuition", w, start)
    assert tuition > old and news < 0


def test_chunks_and_retrieval():
    pages = [{"url": "u1", "title": "Tuition", "text": "\n".join(["Tuition is $1,000 per credit hour."] + ["filler"] * 400)},
             {"url": "u2", "title": "Profile", "text": "Average GMAT 650\nAverage GPA 3.4\nWomen 41%"}]
    chunks = chunk_pages(pages)
    assert all(len(c.text) <= 1600 for c in chunks)
    assert top_chunks(chunks, "average gmat gpa class profile", k=1)[0].url == "u2"
    assert top_chunks(chunks, "tuition per credit hour", k=1)[0].url == "u1"


def test_program_relevance():
    page = "Undergraduate admission statistics 52% Female 13.4% Acceptance Rate. Our MS class: 45% women."
    female, rate = f("Female (%)"), f("Accept Rate (%)")
    assert check(female, "52%", "52% Female", page, "school-wide")[0] == "other-program"
    assert check(rate, "13.4%", "13.4% Acceptance Rate", page, "target",
                 "https://www.bc.edu/admission/undergraduate/statistics.html Undergraduate Admission")[0] == "other-program"
    assert check(female, "45%", "Our MS class: 45% women", page, "target")[0] == "found"
    city = f("Location - City")
    assert check(city, "Boston", "Boston", "Boston", "school-wide")[0] == "found"


def test_value_cannot_add_a_year():
    deadline = f("Deadline Date(s)")
    page = "Fall | Early Deadline: May 1 | Regular Deadline: August 15"
    assert check(deadline, "Fall 2026: May 1", "Fall | Early Deadline: May 1", page)[0] == "bad-value"
    # ...unless the page states it (a "Fall 2026 deadlines" heading above the table)
    assert check(deadline, "Fall 2026: May 1", "Fall | Early Deadline: May 1", "Fall 2026 deadlines\n" + page)[0] == "found"
    assert check(deadline, "Fall: May 1; Aug 15", "Fall | Early Deadline: May 1 | Regular Deadline: August 15", page)[0] == "found"


def test_duplicate_pages_chunked_once():
    text = "Same page text " * 50
    assert len(chunk_pages([{"url": "a", "text": text}, {"url": "a/", "text": text}])) == len(chunk_pages([{"url": "a", "text": text}]))


def test_quote_with_ellipsis():
    page = "Fall | Early Deadline: May 1 | May 15\nRegular Deadline: August 15 | August 30"
    d = f("Deadline Date(s)")
    assert check(d, "Fall: May 1; Aug 15", "Early Deadline: May 1 ... Regular Deadline: August 15", page)[0] == "found"
    assert check(d, "Fall: May 1", "Early Deadline: May 1 ... Final Deadline: July 1", page)[0] == "bad-quote"


def test_foreign_program_pages():
    w = program_words("Online MS Economics")
    assert foreign_program("https://www.purdue.edu/online/program/prequalified-structures-for-the-stage/", w)
    assert not foreign_program("https://business.purdue.edu/graduate-programs/online/ms-economics/curriculum.php", w)
    assert not foreign_program("https://business.purdue.edu/online/tuition/", w)
    assert not foreign_program("https://appliedeconomics.bc.edu/programs/cost/", program_words("APPLIED ECONOMICS"))


def test_start_path_never_foreign():
    w = program_words("QUANT FINANCE ECONOMICS")
    start = "https://www.gradcollege.txst.edu/programs/msqfe.html"
    assert not foreign_program(start, w, start)
    assert foreign_program("https://www.gradcollege.txst.edu/programs/msw.html", w, start)
    lib = "https://www.liberty.edu/online/business/masters/mba/economics"
    assert not foreign_program("https://www.liberty.edu/online/business/masters/mba/", program_words("MBA ECONOMICS"), lib)


def test_section_scoring_avoids_other_schools():
    from field_pipeline.discover import section_bonus
    start = "https://www.american.edu/cas/economics/ms/online.cfm"
    assert section_bonus("https://www.american.edu/cas/economics/ms/tuition.cfm", start) > 0
    assert section_bonus("https://www.american.edu/wcl/career/", start) < 0
    assert section_bonus("https://www.american.edu/admissions/graduate-admissions/index.cfm", start) == 0
    w = program_words("APPLIED ECONOMICS")
    assert score("https://www.american.edu/wcl/career/", "Career", "career", w, start) <= 0


def test_bing_redirect_decoding():
    from field_pipeline.search import bing_target
    href = "https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly93d3cuYXBwbGllZC5jb20v&ntb=1"
    assert bing_target(href) == "https://www.applied.com/"
    assert bing_target("https://www.bc.edu/x") == "https://www.bc.edu/x"


def test_ranking_needs_publisher_source():
    us = f("Ranking (U.S. News)")
    page = "Ranked No. 25 in Best Online Master's in Business Programs by U.S. News & World Report. Ranked #3 by Fortune."
    assert check(us, "No. 25", "Ranked No. 25 in Best Online Master's in Business Programs by U.S. News & World Report",
                 page)[0] == "found"
    assert check(us, "#3", "Ranked #3 by Fortune.", page)[0] == "bad-source"
    assert check(us, "#3", "Ranked #3 by Fortune.", page, "target",
                 "https://www.usnews.com/education/online-education/x Online Programs")[0] == "found"
    assert check(us, "#40", "#40 National Universities", "#40 National Universities", "target",
                 "https://www.usnews.com/best-colleges/x")[0] == "other-program"


def test_every_field_group_exists():
    from field_pipeline.fields import FIELDS, GROUPS
    assert all(fl.group in GROUPS for fl in FIELDS)
    assert {g.name for g in GROUPS.values() if g.sites} == {"rank_usnews", "rank_bestmasters", "rank_qs"}


@pytest.mark.parametrize("quote,kind,lag", [
    ("Tuition for 2026-2027 is $1,250", "money", 0),
    ("Tuition for 2025-26 is $1,250", "money", 1),
    ("Fall 2025 entering class: average GPA 3.4", "gpa", 1),
    ("Class of 2026 average GMAT 640", "gmat", 1),
    ("2024-2025 class profile: 40% women", "percent", 2),
    ("Fall 2027 application opens", "date", -1),
    ("Average GMAT 640", "gmat", 0),
])
def test_cycle_lag(quote, kind, lag):
    assert cycle_lag(quote, kind)[0] == lag


def test_year_fallback_ladder():
    gpa, deadline, tuition = f("Average Undergraduate GPA"), f("Deadline Date(s)"), f("In-State Tuition Per Credit")
    q = "Fall 2025 entering class average GPA 3.4"
    assert check(gpa, "3.4", q, q) == ("found-prior", "2025-2026")
    q = "2024-2025 class profile average GPA 3.4"
    assert check(gpa, "3.4", q, q) == ("found-prior", "2024-2025")
    q = "2023-2024 class profile average GPA 3.4"
    assert check(gpa, "3.4", q, q)[0] == "off-cycle"
    q = "2025-2026 tuition: $1,250 per credit hour"
    assert check(tuition, "$1,250", q, q) == ("found-prior", "2025-2026")
    q = "2024-2025 tuition: $1,250 per credit hour"
    assert check(tuition, "$1,250", q, q)[0] == "off-cycle"  # tuition falls back one year only
    q = "Fall 2025 priority deadline: February 1, 2025"
    assert check(deadline, "Fall 2025: Feb 1, 2025", q, q)[0] == "off-cycle"  # an old deadline is never used


def test_best_candidate():
    cur = {"status": "found", "cycle": "2026-2027"}
    one = {"status": "found-prior", "cycle": "2025-2026"}
    two = {"status": "found-prior", "cycle": "2024-2025"}
    assert better(cur, one) and better(one, two) and not better(two, one)
    assert better(two, {"status": "not-found"}) and better({"status": "not-found"}, {"status": "llm-error"})
    assert candidates([{"value": "3.4"}, {"value": None}]) == [{"value": "3.4"}]
    assert candidates({"value": "3.4"}) == [{"value": "3.4"}] and candidates({"value": None}) == [{}]


def test_usnews_profile():
    online = "https://www.usnews.com/education/online-education/liberty-university-OBUS0955/business"
    assert usnews_kind("Online MS Economics", "") == "online-business"
    assert usnews_kind("Online MBA", "") == "online-mba"
    assert usnews_kind("1yr MBA", "") == "mba" and usnews_kind("MS Finance - Residential", "") == "business-school"
    assert usnews_program_level("online-business", online)
    assert not usnews_program_level("online-mba", online)
    assert not usnews_program_level("online-business", "https://www.usnews.com/best-colleges/liberty-university-10392")
    bschool = "https://www.usnews.com/best-graduate-schools/top-business-schools/boston-college-01107"
    assert usnews_program_level("mba", bschool) and not usnews_program_level("business-school", bschool)
    assert same_school("boston-college", "BOSTON COLLEGE") and not same_school("boston-university", "Boston College")
    assert same_school("university-of-cincinnati", "University of Cincinnati")
    assert same_school("purdue-university-main-campus", "PURDUE UNIVERSITY - WEST LAFAYETTE")
    from field_pipeline.discover import USNEWS_ONLINE
    m = USNEWS_ONLINE.match("https://www.usnews.com/education/online-education/purdue-university-main-campus-243780")
    assert m.group(1) == "purdue-university-main-campus"
    m = USNEWS_ONLINE.match("https://www.usnews.com/education/online-education/liberty-university-OBUS0955/mba")
    assert m.group(1) == "liberty-university"
    assert category("MS Finance - Residential", "") == "MS Finance"


def test_usnews_profile_counts_as_program_level():
    from field_pipeline.extract import judge
    from field_pipeline.retrieve import Chunk
    url = "https://www.usnews.com/education/online-education/liberty-university-OBUS0955/business"
    c = Chunk("C1", url, "Liberty University - Online Graduate Business Program", "International students\n0%")
    full = lambda ch: ch.text
    item = {"value": "0%", "quote": "International students 0%", "excerpt": "C1", "applies_to": "school-wide"}
    intl = f("International (%)")
    assert judge(intl, "class", item, [c], {"C1": c}, full, "online-business")["status"] == "found"
    assert judge(intl, "class", item, [c], {"C1": c}, full, "")["status"] == "other-program"


def test_prior_record_match():
    from field_pipeline.__main__ import prior_record
    from field_pipeline.workbook import Row
    prior = {("online mba", "boston college"): [{"Program Name": "Online MBA", "Average GMAT": 640, "_row": 4}]}
    assert prior_record(prior, Row("Online MBA", 5, "BOSTON COLLEGE", "online  mba", "", []))["_row"] == 4
    assert prior_record(prior, Row("Online MBA", 5, "BOSTON COLLEGE", "Hybrid MBA", "", [])) is None
