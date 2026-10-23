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
    query: str                   # BM25 query terms for retrieval
    url_words: tuple             # words that make a URL / link text worth opening for this group
    search: str = ""             # short web/site-search phrase ("" = no search for this group)
    boost: str = ""              # regex: chunks matching it rank a bit higher (e.g. dollar amounts for tuition)
    program_specific: bool = False  # a school-/university-wide value is NOT acceptable (class stats, outcomes)
    sites: tuple = ()            # outside domains to search and accept (ranking publishers)
    source: str = ""             # regex the quote or source URL must match (e.g. the ranking publisher's name)


MONEY, PCT = r"\$\s?\d", r"\d+(\.\d+)?\s?%"
MONTH_DAY = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"

# Narrow groups = more, smaller LLM calls, each given only the text about its topic (about 16 calls per school).
GROUPS = {g.name: g for g in (
    Group("basics", "school college business located address city state campus contact",
          ("about", "contact", "school", "college")),
    Group("scores", "class profile average gmat gre gpa undergraduate grade point work experience years entering",
          ("profile", "class", "students", "cohort", "statistics", "facts"), "class profile average GPA GMAT",
          r"\bgpa\b|\bgmat\b|\bgre\b", True),
    Group("class", "class profile cohort students age average international women female percent class size "
          "enrolled acceptance rate admitted applicants",
          ("profile", "class", "students", "cohort", "statistics", "facts"), "class profile students women international",
          PCT, True),
    Group("structure", "credit hours credits duration months years complete program format online hybrid campus "
          "curriculum courses electives specializations tracks concentrations stem",
          ("curriculum", "courses", "program", "format", "overview", "structure", "requirements"),
          "curriculum credit hours", r"\d+\s*(credit|month|year)", True),
    Group("campus", "on-campus residency immersion in-person campus visit orientation online students attend",
          ("residency", "immersion", "campus", "format", "online"), "on-campus residency immersion"),
    Group("deadlines", "application deadline deadlines priority final round fall spring summer start term "
          "apply by date", ("admission", "apply", "deadline", "dates", "how-to-apply"), "application deadlines",
          MONTH_DAY),
    Group("requirements", "admission requirements gmat gre waiver test optional international applicants "
          "three-year bachelor degree india equivalent wes evaluation",
          ("admission", "requirements", "international", "apply"), "admission requirements international applicants"),
    Group("tuition", "tuition per credit hour cost total program fees resident nonresident in-state out-of-state "
          "international rate", ("tuition", "cost", "fees", "bursar", "rates", "financial"), "tuition per credit hour",
          MONEY),
    Group("aid", "scholarship scholarships fellowship assistantship financial aid enrollment deposit seat confirm",
          ("scholarship", "financial", "aid", "deposit", "admitted"), "scholarships enrollment deposit", MONEY),
    Group("experiential", "experiential learning capstone consulting project practicum internship co-op study "
          "abroad trek case competition conference certification hands-on real-world",
          ("experiential", "capstone", "practicum", "internship", "abroad", "projects"),
          "experiential learning capstone project"),
    Group("community", "student organizations clubs affinity groups women veterans black latino lgbtq diversity "
          "inclusion belonging association marketing center institute",
          ("clubs", "organizations", "diversity", "inclusion", "student-life", "belonging", "community"),
          "student organizations affinity groups"),
    Group("career", "career coaching services advising counseling career center one-on-one resume interview "
          "online students", ("career", "coaching", "advising", "services"), "career services coaching"),
    Group("outcomes", "employment report placement employed salary median mean average base bonus signing "
          "graduates outcomes months after graduation", ("outcomes", "employment", "placement", "salary", "report"),
          "employment outcomes salary", MONEY + "|" + PCT, True),
    Group("rank_usnews", "u.s. news us news world report ranked ranking no. # best programs",
          ("ranking", "rankings", "news", "recognition"), "U.S. News ranking", r"#\s?\d|no\.\s?\d",
          sites=("usnews.com",), source=r"u\.?\s?s\.?\s?news|usnews"),
    Group("rank_bestmasters", "best-masters eduniversal best masters ranking ranked",
          ("ranking", "rankings"), "Best Masters ranking", r"#\s?\d", sites=("best-masters.com",),
          source=r"best[- ]?masters|eduniversal"),
    Group("rank_qs", "qs world university rankings topuniversities ranked",
          ("ranking", "rankings"), "QS ranking", r"#\s?\d", sites=("topuniversities.com",),
          source=r"\bqs\b|topuniversities"),
)}


@dataclass(frozen=True)
class Field:
    pattern: str
    group: str
    kind: str          # text | number | percent | money | months | yesno | gmat | gre | gpa | date
    hint: str
    lo: float | None = None
    hi: float | None = None
    max_lag: int = 2   # oldest academic year accepted when 2026-2027 is not published: 1 = 2025-26, 2 = 2024-25


# Order matters: specific patterns before general ones.
FIELDS = [
    Field(r"^school\s*/\s*college", "basics", "text", "Name of the school/college inside the university that runs "
          "the program (e.g. 'Kelley School of Business'), not the university name."),
    Field(r"location\s*-\s*city", "basics", "text", "City of the school's campus/address. City only."),
    Field(r"location\s*-\s*state", "basics", "text", "US state of the school's address, two-letter code (e.g. IN)."),
    Field(r"gmat/gre required", "requirements", "yesno", "Is a GMAT/GRE score required? Yes / No / Waivable (+condition)."),
    Field(r"(avg|average) gmat", "scores", "gmat", "Average (mean) GMAT of the entering class. Not a minimum.", 200, 805),
    Field(r"(avg|average) gre", "scores", "gre", "Average GRE of the entering class (total, or Verbal/Quant).", 130, 340),
    Field(r"undergraduate gpa", "scores", "gpa", "Average undergraduate GPA of the entering class. Not a minimum.", 2, 4.3),
    Field(r"work experience", "scores", "months", "Average professional work experience of the entering class, in "
          "MONTHS (convert years x 12 only if the page states years).", 0, 400),
    Field(r"student age", "class", "number", "Average age of entering students.", 18, 65),
    Field(r"class size|total enrollment", "class", "number", "Number of students in the entering class / cohort.", 1, 5000),
    Field(r"^international\s*(\(%\)|%)", "class", "percent", "Percent of the class who are international students.", 0, 100),
    Field(r"^(female|women)\s*(\(%\)|%)", "class", "percent", "Percent of the class who are women.", 0, 100),
    Field(r"accept rate", "class", "percent", "Admission/acceptance rate (admitted / applied).", 0, 100),
    Field(r"duration", "structure", "months", "Typical time to complete the program, in MONTHS.", 1, 120),
    Field(r"credit hours", "structure", "number", "Total credit hours required to graduate.", 1, 120),
    Field(r"on-campus options", "campus", "yesno", "Yes if online students can or must do any part on campus (classes, residencies, "
          "immersions, orientation); No only if the page says it is fully online with no campus option. Add a few words."),
    Field(r"delivery model.*(faculty|adjunct)", "structure", "text", "Who teaches: Full-time faculty / Part-time "
          "adjuncts / Mix, only if the page says so."),
    Field(r"delivery model", "structure", "text", "100% online / Hybrid / Online with required residency."),
    Field(r"program start", "deadlines", "text", "Month(s) the program starts (e.g. 'August, January')."),
    Field(r"deadline", "deadlines", "date", "Application deadlines for the 2026-2027 entry terms, all rounds, "
          "e.g. 'Fall 2026: Priority Feb 1, 2026; Final Jun 1, 2026'.", max_lag=0),
    Field(r"3yr-degree|india", "requirements", "yesno", "Does the program accept a three-year bachelor's degree from "
          "India (Y/N)? Only if the page addresses three-year degrees."),
    Field(r"in-state tuition per credit", "tuition", "money", "Tuition per credit hour for in-state residents.", 50, 10000, max_lag=1),
    Field(r"out-of-state tuition per credit", "tuition", "money", "Tuition per credit hour for out-of-state students.", 50, 10000, max_lag=1),
    Field(r"international tuition per credit", "tuition", "money", "Tuition per credit hour for international students.", 50, 10000, max_lag=1),
    Field(r"in-state tuition total", "tuition", "money", "Total program tuition for in-state residents, as stated.", 1000, 400000, max_lag=1),
    Field(r"out-of-state tuition total", "tuition", "money", "Total program tuition for out-of-state students, as stated.", 1000, 400000, max_lag=1),
    Field(r"international tuition total", "tuition", "money", "Total program tuition for international students, as stated.", 1000, 400000, max_lag=1),
    Field(r"scholarship", "aid", "text", "Scholarships offered to students of this program (Yes/No + names/amounts).", max_lag=1),
    Field(r"deposit", "aid", "text", "Enrollment deposit required to hold a seat (amount, or Yes/No).", max_lag=1),
    Field(r"experiential learning.*brief|experiential learning.*description", "experiential", "text",
          "One-sentence description of experiential learning (capstone, consulting projects, practicum)."),
    Field(r"experiential learning", "experiential", "yesno", "Does the program offer experiential learning courses or "
          "opportunities? Yes/No."),
    Field(r"affinity groups.*brief|affinity groups.*description", "community", "text",
          "One-sentence description of affinity groups available to the program's students."),
    Field(r"affinity groups", "community", "yesno", "Are affinity / identity-based student groups (Women in Business, "
          "veterans, Black/Latino/LGBTQ+ business associations) available? Yes/No."),
    Field(r"career coaching", "career", "yesno", "Are career coaching/consulting services offered to this "
          "program's students? Yes/No."),
    Field(r"(co-op|internship).*brief", "experiential", "text", "One-sentence description of co-op/internship opportunities."),
    Field(r"co-op|internship", "experiential", "yesno", "Are co-ops or internships part of / available to the program? Yes/No."),
    Field(r"study abroad.*brief|study abroad.*expla", "experiential", "text", "One-sentence description of study abroad options."),
    Field(r"study abroad", "experiential", "yesno", "Are study abroad opportunities offered to the program's students? Yes/No."),
    Field(r"city trek.*brief", "experiential", "text", "One-sentence description of city/company treks."),
    Field(r"city trek", "experiential", "yesno", "Does the program run city/company treks? Yes/No."),
    Field(r"co-curricular.*brief|co-curricular.*expla", "community", "text", "One-sentence description of co-curricular activities."),
    Field(r"co-curricular", "community", "yesno", "Are co-curricular activities (case competitions, clubs) offered? Yes/No."),
    Field(r"paid conference", "experiential", "yesno", "Does the program pay for students to attend conferences? Yes/No."),
    Field(r"paid industry|certificate", "experiential", "yesno", "Does the program pay for an industry certification? Yes/No."),
    Field(r"marketing center", "community", "yesno", "Does the school have a marketing center or institute? Yes/No."),
    Field(r"stem designation", "structure", "yesno", "Is the program STEM-designated (CIP code)? Yes/No."),
    Field(r"specializations|tracks", "structure", "text", "Specializations / tracks / concentrations offered, comma-separated."),
    Field(r"elective credits", "structure", "number", "Number of elective credits in the plan of study.", 0, 120),
    Field(r"total electives", "structure", "number", "Number of elective courses in the plan of study.", 0, 60),
    Field(r"online/hybrid option", "structure", "yesno", "Is an online or hybrid version of the program available? Yes/No."),
    Field(r"job placement", "outcomes", "percent", "Percent of graduates employed (within the period the report states).", 0, 100),
    Field(r"post-grad salary", "outcomes", "money", "Average (mean or median as labelled) base salary of graduates.", 10000, 500000),
    Field(r"post-grad bonus", "outcomes", "money", "Average signing/annual bonus of graduates.", 0, 200000),
    Field(r"ranking.*(u\.?s\.?\s*news|usnews)", "rank_usnews", "text", "U.S. News rank for this program/category, with edition year."),
    Field(r"ranking.*best-masters", "rank_bestmasters", "text", "Best-Masters.com (Eduniversal) rank, with edition year."),
    Field(r"ranking.*(qs|topuniversities)", "rank_qs", "text", "QS / TopUniversities rank, with edition year."),
]

FILLED = ("found", "found-prior", "not-required", "computed", "carried-forward", "ipeds", "ipeds-proxy")  # statuses whose value goes into the workbook

# U.S. News program profiles: the page that describes the row's program category, by sheet type. Online MBA and
# online non-MBA business master's have their own profile pages (class stats, rank); residential programs only have
# the business-school page, whose free stats are MBA-level but which lists the specialty ranks (Finance, Marketing...).
USNEWS_PROFILE = {
    "online-mba": r"usnews\.com/education/online-education/[a-z0-9-]+-[A-Z]+\d+/mba\b",
    "online-business": r"usnews\.com/education/online-education/[a-z0-9-]+-[A-Z]+\d+/business\b",
    "mba": r"usnews\.com/best-graduate-schools/top-business-schools/[a-z0-9-]+-\d+/?$",
    "business-school": r"usnews\.com/best-graduate-schools/top-business-schools/[a-z0-9-]+-\d+/?$",
}
USNEWS_LABEL = {"online-mba": "U.S. News online MBA profile",
                "online-business": "U.S. News online business master's (non-MBA) profile, covers all the school's programs",
                "mba": "U.S. News business school profile (MBA figures)",
                "business-school": "U.S. News business school profile"}
USNEWS_STATS_GROUPS = ("scores", "class", "outcomes")


def usnews_kind(sheet: str, program: str) -> str:
    s = f"{sheet} {program}".casefold()
    mba = bool(re.search(r"\bmba\b", s))
    if "online" in s:
        return "online-mba" if mba else "online-business"
    return "mba" if mba else "business-school"


def usnews_program_level(kind: str, url: str) -> bool:
    """True if U.S. News figures on `url` describe the row's program (category), so they count for class stats.
    The business-school page's stats are MBA figures: program-level only for MBA rows."""
    return kind != "business-school" and bool(re.search(USNEWS_PROFILE[kind], url or ""))


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
