"""Stages 3-4: split a row's pages into chunks and rank them per field group with BM25 (no LLM)."""
import math
import re
from collections import Counter
from dataclasses import dataclass

CHUNK_CHARS, OVERLAP = 1500, 200
STOP = set("the and for are with you your our this that from will can all has have not but its was were per".split())


@dataclass
class Chunk:
    id: str
    url: str
    title: str
    text: str


def chunk_pages(pages: list[dict]) -> list[Chunk]:
    """Line-aligned windows of about CHUNK_CHARS characters with OVERLAP characters of carry-over."""
    chunks, seen = [], set()
    for p in pages:
        key = hash(p.get("text") or "")
        if key in seen:  # same page under another URL
            continue
        seen.add(key)
        for text in _windows((p.get("text") or "").splitlines()):
            chunks.append(Chunk(f"C{len(chunks) + 1}", p["url"], p.get("title") or "", text))
    return chunks


def _windows(lines: list[str]) -> list[str]:
    out, buf, n = [], [], 0
    for line in lines:
        for part in [line[i:i + CHUNK_CHARS] for i in range(0, len(line), CHUNK_CHARS)] or [""]:
            buf.append(part)
            n += len(part) + 1
            if n >= CHUNK_CHARS:
                out.append("\n".join(buf))
                carry = []
                for prev in reversed(buf):
                    if sum(map(len, carry)) + len(prev) > OVERLAP:
                        break
                    carry.insert(0, prev)
                buf, n = carry, sum(len(c) + 1 for c in carry)
    tail = "\n".join(buf).strip()
    if tail and (not out or not out[-1].endswith(tail)):
        out.append(tail)
    return out


def terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.casefold()) if t not in STOP and len(t) > 1]


def bm25(chunks: list[Chunk], query: str, k1: float = 1.4, b: float = 0.75) -> list[tuple[float, Chunk]]:
    docs = [terms(f"{c.title} {c.text}") for c in chunks]
    if not docs:
        return []
    avg = sum(map(len, docs)) / len(docs)
    df = Counter(t for d in docs for t in set(d))
    q = set(terms(query))
    scored = []
    for c, d in zip(chunks, docs):
        tf = Counter(d)
        s = sum(math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * (k1 + 1)
                / (tf[t] + k1 * (1 - b + b * len(d) / avg)) for t in q if tf[t])
        scored.append((s, c))
    return sorted(scored, key=lambda x: -x[0])


def top_chunks(chunks: list[Chunk], query: str, k: int = 6, max_per_url: int = 3, boost: str = "") -> list[Chunk]:
    """Best k chunks for the query, at most max_per_url from one page so several sources get a say.
    `boost` is a regex; chunks matching it (e.g. dollar amounts for the cost group) get a small bonus."""
    ranked = bm25(chunks, query)
    if boost:
        ranked = sorted(((s * (1.3 if re.search(boost, c.text) else 1), c) for s, c in ranked), key=lambda x: -x[0])
    out, per = [], Counter()
    for s, c in ranked:
        if s <= 0 or len(out) >= k:
            break
        if per[c.url] < max_per_url:
            out.append(c)
            per[c.url] += 1
    return out


BOOST = {"cost": r"\$\s?\d", "profile": r"\d+(\.\d+)?\s?%|\bgpa\b|\bgmat\b", "outcomes": r"\$\s?\d|\d+\s?%",
         "admissions": r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}",
         "structure": r"\d+\s*(credit|month|year)"}
