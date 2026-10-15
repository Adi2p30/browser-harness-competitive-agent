"""Stage 6: code checks on every extracted value. Nothing reaches the workbook without passing all of them.

Statuses: found (passed every check) | off-cycle | other-program | bad-quote | bad-value | out-of-range | not-found
"""
import re

from .fields import Field

TARGET = (2026, 2027)  # 2026-2027 academic year: Fall 2026, Spring 2027, Summer 2027
AY = re.compile(r"(?<!\d)(20\d\d)\s*(?:-|–|—|/|to)\s*(20\d\d|\d\d)(?!\d)")
TERM = re.compile(r"\b(fall|autumn|spring|summer|winter)\s+(?:semester\s+|term\s+)?(20\d\d)\b", re.I)
CLASS_OF = re.compile(r"\bclass\s+of\s+(20\d\d)\b", re.I)
YEAR = re.compile(r"(?<![\d$,.])(?<!\d-)(20\d\d)(?![\d,])")  # a bare year, not part of a number like $2,025 or 12025
NUMBER = re.compile(r"\d+(?:\.\d+)?")
NUMERIC = {"number", "percent", "money", "months", "gmat", "gre", "gpa"}
# Groups whose numbers describe one program's students/curriculum: a school- or university-wide figure is wrong.
PROGRAM_SPECIFIC = {"profile", "outcomes", "structure"}
UNDERGRAD = re.compile(r"\b(undergraduate|freshm[ae]n|first-year|first year class|high school|sat|act scores?)\b", re.I)


def squash(s: str) -> str:
    return re.sub(r"\W+", "", s.casefold())


def term_ay(name: str, year: int) -> tuple[int, int]:
    return (year, year + 1) if name.casefold() in ("fall", "autumn") else (year - 1, year)


def cycle_problem(quote: str, kind: str) -> str | None:
    """Why the quote is tied to an academic year other than 2026-2027, or None."""
    for a, b in AY.findall(quote):
        end = int(b) if len(b) == 4 else int(a[:2] + b)
        if end == int(a) + 1 and (int(a), end) != TARGET:
            return f"names {a}-{end}"
    terms = TERM.findall(quote)
    for name, y in terms:
        if term_ay(name, int(y)) != TARGET:
            return f"names {name.title()} {y}"
    for y in CLASS_OF.findall(quote):
        if int(y) not in (2027, 2028):  # graduating class of the cohort entering in 2026-27
            return f"names class of {y}"
    allowed = {2026, 2027} | ({2025} if kind == "date" or terms else set())
    for y in YEAR.findall(CLASS_OF.sub("", AY.sub("", quote))):
        if int(y) not in allowed:
            return f"names {y}"
    return None


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
    if applies_to != "target" and field.group in PROGRAM_SPECIFIC:
        return "other-program", "school-wide figure for a program-specific field"
    if UNDERGRAD.search(quote) or UNDERGRAD.search(source):
        return "other-program", "undergraduate source"
    if added := set(YEAR.findall(value)) - set(YEAR.findall(quote)):
        return "bad-value", f"value adds year {sorted(added)[0]} that the quote does not state"
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
    if why := cycle_problem(quote, field.kind):
        return "off-cycle", why
    return "found", ""
