"""Field registry: every workbook column mapped to a field group, a value kind, search keywords and a definition.

A field group is a set of columns that schools publish on the same kind of page (class profile, tuition table,
admissions page, ...). Retrieval and extraction run once per (row, group), not once per cell.
Column names differ across sheets ("Female (%)", "Female %"), so columns are matched by regex, first match wins.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    name: str
    query: str              # BM25 query terms for retrieval
    url_words: tuple        # words that make a URL / link text worth opening for this group
    search: str             # web-search phrase (appended to site:<domain> "<program>")


GROUPS = {g.name: g for g in (
    Group("basics", "school college business located address city state campus",
          ("about", "contact", "school", "college"), "school of business address"),
    Group("profile", "class profile average gmat gre gpa undergraduate work experience years age students "
          "class size cohort international women female percent acceptance admitted enrolled",
          ("profile", "class", "students", "cohort", "statistics", "facts", "admission"), "class profile average GPA GMAT"),
    Group("structure", "credit hours credits duration months years complete program format online campus "
          "residency immersion curriculum courses",
          ("curriculum", "courses", "program", "format", "overview", "structure"), "credit hours duration curriculum"),
    Group("admissions", "application deadline deadlines apply priority final round fall spring summer "
          "international applicants three-year bachelor degree india equivalent requirements",
          ("admission", "apply", "deadline", "international", "requirements", "how-to-apply"),
          "application deadlines international applicants three-year degree"),
    Group("cost", "tuition per credit hour cost total program fees resident nonresident in-state out-of-state "
          "international scholarship scholarships deposit enrollment financial aid",
          ("tuition", "cost", "fees", "financial", "aid", "scholarship", "bursar", "rates"),
          "tuition per credit hour cost scholarship deposit"),
    Group("experience", "experiential learning capstone consulting project internship practicum clubs student "
          "organizations affinity groups women diversity career coaching services advising",
          ("experiential", "capstone", "career", "clubs", "organizations", "diversity", "inclusion",
           "student-life", "coaching"),
          "experiential learning capstone career coaching student organizations"),
    Group("outcomes", "employment report placement employed salary median mean average base bonus signing "
          "graduates outcomes months after graduation",
          ("outcomes", "employment", "career", "placement", "salary", "report"), "employment report salary placement"),
    Group("rankings", "ranked ranking rankings no. # u.s. news best qs best-masters top",
          ("ranking", "rankings", "news", "recognition", "accolades"), "ranking U.S. News"),
)}


@dataclass(frozen=True)
class Field:
    pattern: str
    group: str
    kind: str          # text | number | percent | money | months | yesno | gmat | gre | gpa | date
    hint: str
    lo: float | None = None
    hi: float | None = None


# Order matters: specific patterns before general ones.
FIELDS = [
    Field(r"^school\s*/\s*college", "basics", "text", "Name of the school/college inside the university that runs "
          "the program (e.g. 'Kelley School of Business'), not the university name."),
    Field(r"location\s*-\s*city", "basics", "text", "City of the school's campus/address. City only."),
    Field(r"location\s*-\s*state", "basics", "text", "US state of the school's address, two-letter code (e.g. IN)."),
    Field(r"gmat/gre required", "admissions", "yesno", "Is a GMAT/GRE score required? Yes / No / Waivable (+condition)."),
    Field(r"(avg|average) gmat", "profile", "gmat", "Average (mean) GMAT of the entering class. Not a minimum.", 200, 805),
    Field(r"(avg|average) gre", "profile", "gre", "Average GRE of the entering class (total, or Verbal/Quant).", 130, 340),
    Field(r"undergraduate gpa", "profile", "gpa", "Average undergraduate GPA of the entering class. Not a minimum.", 2, 4.3),
    Field(r"work experience", "profile", "months", "Average professional work experience of the entering class, in "
          "MONTHS (convert years x 12 only if the page states years).", 0, 400),
    Field(r"student age", "profile", "number", "Average age of entering students.", 18, 65),
    Field(r"class size|total enrollment", "profile", "number", "Number of students in the entering class / cohort.", 1, 5000),
    Field(r"^international\s*(\(%\)|%)", "profile", "percent", "Percent of the class who are international students.", 0, 100),
    Field(r"^(female|women)\s*(\(%\)|%)", "profile", "percent", "Percent of the class who are women.", 0, 100),
    Field(r"accept rate", "profile", "percent", "Admission/acceptance rate (admitted / applied).", 0, 100),
    Field(r"duration", "structure", "months", "Typical time to complete the program, in MONTHS.", 1, 120),
    Field(r"credit hours", "structure", "number", "Total credit hours required to graduate.", 1, 120),
    Field(r"on-campus options", "structure", "yesno", "Yes if online students can or must do any part on campus (classes, residencies, "
          "immersions, orientation); No only if the page says it is fully online with no campus option. Add a few words."),
    Field(r"delivery model.*(faculty|adjunct)", "structure", "text", "Who teaches: Full-time faculty / Part-time "
          "adjuncts / Mix, only if the page says so."),
    Field(r"delivery model", "structure", "text", "100% online / Hybrid / Online with required residency."),
    Field(r"program start", "admissions", "text", "Month(s) the program starts (e.g. 'August, January')."),
    Field(r"deadline", "admissions", "date", "Application deadlines for the 2026-2027 entry terms, all rounds, "
          "e.g. 'Fall 2026: Priority Feb 1, 2026; Final Jun 1, 2026'."),
    Field(r"3yr-degree|india", "admissions", "yesno", "Does the program accept a three-year bachelor's degree from "
          "India (Y/N)? Only if the page addresses three-year degrees."),
    Field(r"in-state tuition per credit", "cost", "money", "Tuition per credit hour for in-state residents.", 50, 10000),
    Field(r"out-of-state tuition per credit", "cost", "money", "Tuition per credit hour for out-of-state students.", 50, 10000),
    Field(r"international tuition per credit", "cost", "money", "Tuition per credit hour for international students.", 50, 10000),
    Field(r"in-state tuition total", "cost", "money", "Total program tuition for in-state residents, as stated.", 1000, 400000),
    Field(r"out-of-state tuition total", "cost", "money", "Total program tuition for out-of-state students, as stated.", 1000, 400000),
    Field(r"international tuition total", "cost", "money", "Total program tuition for international students, as stated.", 1000, 400000),
    Field(r"scholarship", "cost", "text", "Scholarships offered to students of this program (Yes/No + names/amounts)."),
    Field(r"deposit", "cost", "text", "Enrollment deposit required to hold a seat (amount, or Yes/No)."),
    Field(r"experiential learning.*brief|experiential learning.*description", "experience", "text",
          "One-sentence description of experiential learning (capstone, consulting projects, practicum)."),
    Field(r"experiential learning", "experience", "yesno", "Does the program offer experiential learning courses or "
          "opportunities? Yes/No."),
    Field(r"affinity groups.*brief|affinity groups.*description", "experience", "text",
          "One-sentence description of affinity groups available to the program's students."),
    Field(r"affinity groups", "experience", "yesno", "Are affinity / identity-based student groups (Women in Business, "
          "veterans, Black/Latino/LGBTQ+ business associations) available? Yes/No."),
    Field(r"career coaching", "experience", "yesno", "Are career coaching/consulting services offered to this "
          "program's students? Yes/No."),
    Field(r"(co-op|internship).*brief", "experience", "text", "One-sentence description of co-op/internship opportunities."),
    Field(r"co-op|internship", "experience", "yesno", "Are co-ops or internships part of / available to the program? Yes/No."),
    Field(r"study abroad.*brief|study abroad.*expla", "experience", "text", "One-sentence description of study abroad options."),
    Field(r"study abroad", "experience", "yesno", "Are study abroad opportunities offered to the program's students? Yes/No."),
    Field(r"city trek.*brief", "experience", "text", "One-sentence description of city/company treks."),
    Field(r"city trek", "experience", "yesno", "Does the program run city/company treks? Yes/No."),
    Field(r"co-curricular.*brief|co-curricular.*expla", "experience", "text", "One-sentence description of co-curricular activities."),
    Field(r"co-curricular", "experience", "yesno", "Are co-curricular activities (case competitions, clubs) offered? Yes/No."),
    Field(r"paid conference", "experience", "yesno", "Does the program pay for students to attend conferences? Yes/No."),
    Field(r"paid industry|certificate", "experience", "yesno", "Does the program pay for an industry certification? Yes/No."),
    Field(r"marketing center", "experience", "yesno", "Does the school have a marketing center or institute? Yes/No."),
    Field(r"stem designation", "structure", "yesno", "Is the program STEM-designated (CIP code)? Yes/No."),
    Field(r"specializations|tracks", "structure", "text", "Specializations / tracks / concentrations offered, comma-separated."),
    Field(r"elective credits", "structure", "number", "Number of elective credits in the plan of study.", 0, 120),
    Field(r"total electives", "structure", "number", "Number of elective courses in the plan of study.", 0, 60),
    Field(r"online/hybrid option", "structure", "yesno", "Is an online or hybrid version of the program available? Yes/No."),
    Field(r"job placement", "outcomes", "percent", "Percent of graduates employed (within the period the report states).", 0, 100),
    Field(r"post-grad salary", "outcomes", "money", "Average (mean or median as labelled) base salary of graduates.", 10000, 500000),
    Field(r"post-grad bonus", "outcomes", "money", "Average signing/annual bonus of graduates.", 0, 200000),
    Field(r"ranking.*(u\.?s\.?\s*news|usnews)", "rankings", "text", "U.S. News rank for this program/category, with edition year."),
    Field(r"ranking.*best-masters", "rankings", "text", "Best-Masters.com (Eduniversal) rank, with edition year."),
    Field(r"ranking.*(qs|topuniversities)", "rankings", "text", "QS / TopUniversities rank, with edition year."),
]

# Columns the pipeline never extracts: inputs, our own bookkeeping, or numbers computed from other columns.
SKIP = re.compile(r"^(university( name)?|program name|website link|online|residential|curriculum)$|conferral|roi|"
                  r"breakeven|cost of living|confidence|tuition source url|5yr salary|long-term compensation", re.I)
LINK = re.compile(r"\blink\b|\(link\)", re.I)


def field_for(column: str) -> Field | None:
    col = " ".join(str(column).split())
    if SKIP.search(col) or LINK.search(col):
        return None
    return next((f for f in FIELDS if re.search(f.pattern, col, re.I)), None)


def link_target(column: str, columns: list[str]) -> str | None:
    """The researched column whose source URL fills this link column ("Affinity Groups Link" -> "Affinity Groups (Yes/No)")."""
    col = " ".join(str(column).split())
    if not LINK.search(col):
        return None
    stem = re.split(r"\s*(\(|\blink\b)", col, maxsplit=1, flags=re.I)[0].strip().casefold()
    candidates = [c for c in columns if c != column and not LINK.search(str(c)) and str(c).casefold().startswith(stem)]
    yesno = [c for c in candidates if re.search(r"yes/no|y/n", str(c), re.I)]
    return (yesno or candidates or [None])[0]


def plan(columns: list[str]) -> tuple[dict[str, Field], dict[str, str], list[str]]:
    """({column: Field}, {link column: paired column}, [unmapped columns])."""
    fields, links, unmapped = {}, {}, []
    for col in columns:
        if col is None or str(col).startswith("Unnamed"):
            continue
        if f := field_for(col):
            fields[col] = f
        elif pair := link_target(col, columns):
            links[col] = pair
        elif not SKIP.search(" ".join(str(col).split())):
            unmapped.append(col)
    return fields, links, unmapped
