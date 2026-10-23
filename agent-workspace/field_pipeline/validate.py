"""Stage 6: code checks on every extracted value. Nothing reaches the workbook without passing all of them.

Statuses: found (passed every check, 2026-2027 or undated) | found-prior (passed, but for an earlier academic year
the field allows: detail = that year) | not-required (Average GMAT/GRE: the program says the test is not required,
optional or waivable) | school-wide (passed every check, but the figure is for the whole school/university's
graduate students: the fallback for program-specific fields; detail = earlier year, if any) | off-cycle |
other-program | bad-quote | bad-value | out-of-range | bad-source | not-found
"""
import re

from .fields import GROUPS, Field

TARGET = (2026, 2027)  # 2026-2027 academic year: Fall 2026, Spring 2027, Summer 2027
AY = re.compile(r"(?<!\d)(20\d\d)\s*(?:-|–|—|/|to)\s*(20\d\d|\d\d)(?!\d)")
TERM = re.compile(r"\b(fall|autumn|spring|summer|winter)\s+(?:semester\s+|term\s+)?(20\d\d)\b", re.I)
CLASS_OF = re.compile(r"\bclass\s+of\s+(20\d\d)\b", re.I)
YEAR = re.compile(r"(?<![\d$,.])(?<!\d-)(20\d\d)(?![\d,])")  # a bare year, not part of a number like $2,025 or 12025
NUMBER = re.compile(r"\d+(?:\.\d+)?")
NUMERIC = {"number", "percent", "money", "months", "gmat", "gre", "gpa"}
CREDITS = re.compile(r"credit|\bhours?\b|\bsch\b", re.I)
UNITS = re.compile(r"\bunits?\b|\bcourses\b|\bmodules?\b", re.I)
YES_NO = re.compile(r"^\s*(yes|no|y|n)\b", re.I)
# a "No" must be stated: a negation, "only ..." or a fully-online statement (On-Campus Options: No)
NO_EVIDENCE = re.compile(r"\bnot?\b|n't\b|\bnone\b|\bwithout\b|\bnever\b|\bno longer\b|\bunavailable\b|"
                         r"\bonly\b|\bexclusively\b|(100%|fully|entirely|completely) online", re.I)
# a "Yes" is contradicted by "not offered / not STEM / does not accept ..."
# 3-year Indian degrees: a four-year requirement or an extra condition ("expected to complete ...") also says No
NO_EVIDENCE_3YR = re.compile(r"four-year|4-year|four year|\brequire|\bmust\b|expected to|equivalen", re.I)
YES_DENIED = re.compile(r"\b(not|no longer|isn't|aren't|doesn't|don't|does not|do not|is not|are not|cannot)\s+"
                        r"(\w+\s+){0,3}(offer|offered|available|provide|provided|stem|designated|accept|accepted|"
                        r"eligible|required|include|included)\b", re.I)
NOT_REQUIRED_VALUE = re.compile(r"not required|optional|waiv|no (gmat|gre)", re.I)
NOT_REQUIRED_QUOTE = re.compile(r"not (be )?required|optional|waive|waiver|do(es)? not require|no (gmat|gre)\b|not needed",
                                re.I)
UNDERGRAD = re.compile(r"\b(undergraduate|freshm[ae]n|first-year|first year class|high school|sat|act scores?)\b|best-colleges", re.I)


def squash(s: str) -> str:
    return re.sub(r"\W+", "", s.casefold())


def term_ay(name: str, year: int) -> tuple[int, int]:
    return (year, year + 1) if name.casefold() in ("fall", "autumn") else (year - 1, year)


def cycle_lag(quote: str, kind: str) -> tuple[int, str]:
    """(years back from 2026-2027, oldest academic year named). (0, "") = 2026-2027 or undated; < 0 = a later year."""
    starts = []  # first calendar year of every academic year the quote names
    for a, b in AY.findall(quote):
        end = int(b) if len(b) == 4 else int(a[:2] + b)
        if end == int(a) + 1:
            starts.append(int(a))
    terms = TERM.findall(quote)
    starts += [term_ay(name, int(y))[0] for name, y in terms]
    for y in map(int, CLASS_OF.findall(quote)):  # graduating class of the cohort entering in 2026-27 = 2027 or 2028
        starts.append(TARGET[0] if y >= 2027 else y - 1)
    for y in map(int, YEAR.findall(CLASS_OF.sub("", AY.sub("", quote)))):
        if y in TARGET or (y == 2025 and (kind == "date" or terms)):
            continue
        starts.append(y if y < TARGET[0] else y - 1)
    if not starts:
        return 0, ""
    later = [a for a in starts if a > TARGET[0]]
    a = max(later) if later else min(starts)
    return TARGET[0] - a, f"{a}-{a + 1}"


def _trim(n: str) -> str:
    """'3.50' -> '3.5', '1250.00' -> '1250'; integers unchanged."""
    return n.rstrip("0").rstrip(".") if "." in n else n


def lead_in(page_text: str, piece: str, chars: int = 300) -> str:
    """The `chars` characters of the page just before the quote (its heading / table caption). `piece` is the
    squashed quote (letters and digits only), so word characters are matched back to their page positions."""
    pos = [k for k, ch in enumerate(page_text) if re.match(r"\w", ch)]
    i = "".join(page_text[k] for k in pos).casefold().find(piece) if piece else -1
    return page_text[max(0, pos[i] - chars):pos[i]] if i >= 0 else ""


def to_number(value: str, kind: str) -> float | None:
    """First number in the value; money ignores commas. GRE "Verbal 155, Quant 158" -> 155 (range-checked per part)."""
    nums = NUMBER.findall(value.replace(",", ""))
    return float(nums[0]) if nums else None


def check(field: Field, value: str, quote: str, page_text: str, applies_to: str = "target",
          source: str = "") -> tuple[str, str]:
    """(status, detail). applies_to: target | school-wide | other. source: URL + title of the page."""
    value, quote = (value or "").strip(), (quote or "").strip()
    if not value or value.casefold() in ("null", "none", "n/a", "not found", "unknown"):
        return "not-found", ""
    if not quote:
        return "bad-quote", "no quote"
    page_key = squash(page_text)
    pieces = [squash(q) for q in re.split(r"\.\.\.|\u2026", quote) if squash(q)]
    if not pieces or any(p not in page_key for p in pieces) or (len(pieces) > 1 and min(map(len, pieces)) < 8):
        return "bad-quote", "quote is not on the page"
    if applies_to == "other":
        return "other-program", "model says the value is for a different program/level"
    group = GROUPS[field.group]
    if group.source and not (re.search(group.source, quote, re.I) or re.search(group.source, source, re.I)):
        return "bad-source", f"neither the quote nor the page is from {group.sites[0] if group.sites else group.source}"
    school = applies_to != "target" and group.program_specific  # allowed only as the labelled school-wide fallback
    if school and UNDERGRAD.search(lead_in(page_text, pieces[0])):
        return "other-program", "school-wide figure under an undergraduate heading"
    if UNDERGRAD.search(quote) or UNDERGRAD.search(source):
        return "other-program", "undergraduate source"
    if field.pattern == r"credit hours" and UNITS.search(quote) and not CREDITS.search(quote):
        return "bad-value", "the page counts units/courses, not credit hours"
    if field.kind == "yesno" and (m := YES_NO.match(value)):
        evidence = NO_EVIDENCE.search(quote) or (re.search(r"3yr|india", field.pattern) and NO_EVIDENCE_3YR.search(quote))
        if m.group(1).casefold().startswith("n") and not evidence:
            return "bad-value", "quote does not say No"
        if m.group(1).casefold().startswith("y") and YES_DENIED.search(quote):
            return "bad-value", "quote says it is not offered / not the case"
    if field.kind in ("gmat", "gre") and NOT_REQUIRED_VALUE.search(value):
        if not NOT_REQUIRED_QUOTE.search(quote):
            return "bad-value", "quote does not say the test is not required"
        return "not-required", ""
    # A year the quote lacks is fine if the page states it (e.g. a "Fall 2026 deadlines" heading above the table).
    if added := set(YEAR.findall(value)) - set(YEAR.findall(quote)) - set(YEAR.findall(page_text)):
        return "bad-value", f"value adds year {sorted(added)[0]} that the page does not state"
    if field.kind in NUMERIC:
        quote_digits = re.sub(r"[,\s]", "", quote)
        value_nums = NUMBER.findall(value.replace(",", ""))
        if not value_nums:
            return "bad-value", "no number in value"
        # Months may be converted from years ("2 years" -> 24); everything else must appear in the quote.
        if field.kind != "months" and any(_trim(n) not in quote_digits for n in value_nums):
            return "bad-value", "value's digits are not in the quote"
        n = to_number(value, field.kind)
        if field.kind == "gre" and n is not None and n < 260 and not (130 <= n <= 170):
            return "out-of-range", f"{n} is not a GRE score"
        elif field.lo is not None and n is not None and not (field.lo <= n <= field.hi):
            if not (field.kind == "gre" and 130 <= n <= 170):
                return "out-of-range", f"{n} outside {field.lo}-{field.hi}"
    lag, label = cycle_lag(f"{quote} {value}", field.kind)
    if lag == 0:
        return ("school-wide", "") if school else ("found", "")
    if 0 < lag <= field.max_lag:
        return ("school-wide", label) if school else ("found-prior", label)
    return "off-cycle", f"names {label}"
