"""One text form for every value stored in the production database, so the importer and the admin client's diff
format the same workbook or pipeline value identically (otherwise every publish would show phantom changes)."""
from knowledge_base.__main__ import text


def to_text(v) -> str | None:
    """None for blanks and placeholders; otherwise knowledge_base's text form (dates as YYYY-MM-DD, 36.0 as "36",
    whitespace collapsed)."""
    if v is None:
        return None
    return text(v) or None
