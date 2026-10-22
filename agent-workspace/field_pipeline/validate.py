"""Stage 6: code checks on every extracted value. Nothing reaches the workbook without passing all of them.

Statuses: found (passed every check, 2026-2027 or undated) | found-prior (passed, but for an earlier academic year
the field allows: detail = that year) | off-cycle | other-program | bad-quote | bad-value | out-of-range |
bad-source | not-found
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
    if applies_to != "target" and group.program_specific:
        return "other-program", "school-wide figure for a program-specific field"
    if UNDERGRAD.search(quote) or UNDERGRAD.search(source):
        return "other-program", "undergraduate source"
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
        return "found", ""
    if 0 < lag <= field.max_lag:
        return "found-prior", label
    return "off-cycle", f"names {label}"
