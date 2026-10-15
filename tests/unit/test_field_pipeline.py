import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("openpyxl")

from field_pipeline.discover import foreign_program, program_words, score  # noqa: E402
from field_pipeline.fetch import html_to_page, site  # noqa: E402
from field_pipeline.fields import field_for, link_target, plan  # noqa: E402
from field_pipeline.retrieve import chunk_pages, top_chunks  # noqa: E402
from field_pipeline.validate import check, cycle_problem  # noqa: E402


def f(col):
    return field_for(col)


@pytest.mark.parametrize("col,group,kind", [
    ("International (%)", "profile", "percent"),
    ("International %", "profile", "percent"),
    ("International Tuition Per Credit", "cost", "money"),
    ("Female (%)", "profile", "percent"),
    ("Avg GMAT", "profile", "gmat"),
    ("Accept 3yr-degree from India? (Y/N)", "admissions", "yesno"),
    ("Affinity Groups (brief description)", "experience", "text"),
    ("Affinity Groups (Yes/No)", "experience", "yesno"),
    ("Co-op / Internship Opportunities (Brief Description)", "experience", "text"),
    ("Co-op / Internship Opportunities (Yes/No)", "experience", "yesno"),
    ("Delivery Model (Led by Full Time faculty or Part Time Adjuncts?)", "structure", "text"),
    ("Ranking (USNews)", "rankings", "text"),
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
    assert (cycle_problem(quote, kind) is not None) == bad


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
    tuition = score("https://business.purdue.edu/online/tuition/", "Tuition & Fees", "cost", w, start)
    news = score("https://business.purdue.edu/news/2021/story", "Story", "cost", w, start)
    old = score("https://business.purdue.edu/tuition-2019/", "Tuition", "cost", w, start)
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
