"""IPEDS Completions (NCES): master's degrees awarded per school and CIP code, read from the federal bulk files.

No browser and no LLM. Gives "Average Conferrals (past 3 years)" directly, and the share of women and of
nonresident (international) graduates as labelled proxies for Female (%) / International (%) -- graduates, not
the entering class. A school's online and on-campus programs under the same CIP code are counted together.

Files are downloaded once into field_pipeline/data/ipeds/ and reduced to a small CSV per year.
"""
import csv
import io
import logging
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from .fetch import site

log = logging.getLogger(__name__)
DIR = Path(__file__).resolve().parent / "data" / "ipeds"
BASE = "https://nces.ed.gov/ipeds/datacenter/data/"
YEARS = 3
MASTERS = 7  # AWLEVEL 7 = master's degree

# CIP codes (prefixes) per sheet, matched against the sheet name. A school's programs that share a code are counted
# together, so the codes are kept narrow; sheets without a confident mapping get no IPEDS values.
CIP = (
    (r"\bmba\b", ("52.0201",), "Business Administration and Management, General: every MBA format combined"),
    (r"business analytics|baim", ("30.7102", "52.1301", "52.1399"), "Business Analytics / Management Science"),
    (r"econom", ("45.06",), "Economics"),
    (r"supply chain|gscm", ("52.0203",), "Logistics, Materials, and Supply Chain Management"),
    (r"\bhrm\b|human resource", ("52.1001",), "Human Resources Management"),
    (r"accounting", ("52.0301", "52.0304", "52.0305"), "Accounting"),
    (r"finance", ("52.08",), "Finance"),
    (r"marketing", ("52.14",), "Marketing"),
)
DROP = {"the", "of", "at", "in", "and", "main", "campus"}
ALIASES = {"st": "saint", "mt": "mount", "univ": "university"}
PAGE_CIP = re.compile(r"\bCIP\b[^0-9\n]{0,40}(\d{2}\.\d{4})")
ONLINE_UNIT = re.compile(r"online|world campus|digital immersion|global campus", re.I)

@dataclass
class Completions:
    unitid: str
    school: str          # IPEDS institution name
    cips: str            # label of the CIP codes counted
    years: list[int]     # IPEDS award years (year Y = July Y-1 .. June Y)
    totals: list[int]    # master's degrees per year
    women: int           # summed over the years
    nonresident: int

    @property
    def average(self) -> float:
        return sum(self.totals) / len(self.totals)

    @property
    def span(self) -> str:
        return f"{self.years[0] - 1}-{self.years[0] % 100:02d} to {self.years[-1] - 1}-{self.years[-1] % 100:02d}"

    def share(self, n: int) -> float | None:
        return round(100 * n / sum(self.totals), 1) if sum(self.totals) else None


def cip_for(sheet: str, program: str = "") -> tuple[tuple[str, ...], str] | None:
    """CIP prefixes for a sheet (the program name decides only when the sheet name does not)."""
    for text in (sheet, program):
        for pattern, codes, label in CIP:
            if re.search(pattern, text or "", re.I):
                return codes, label
    return None


def page_cip(texts: list[str]) -> str | None:
    """The CIP code the program's own pages state (STEM programs print it for OPT), most mentioned first."""
    found = Counter(c for t in texts for c in PAGE_CIP.findall(t or ""))
    return found.most_common(1)[0][0] if found else None


def words(name: str) -> list[str]:
    name = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold().replace("&", " and "))
    return [ALIASES.get(w, w) for w in name.split() if w not in DROP]


def name_score(a: str, b: str) -> float:
    """3 = same name ('THE OHIO STATE UNIVERSITY' = 'Ohio State University-Main Campus'); about 2 = one name starts
    the other ('PURDUE UNIVERSITY - WEST LAFAYETTE' ~ 'Purdue University'), fewer extra words first; below 1 =
    share of words in common."""
    x, y = words(a), words(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 3.0
    short, long_ = sorted((x, y), key=len)
    if long_[:len(short)] == short:
        return 2.0 - 0.01 * (len(long_) - len(short))
    return 0.9 * len(set(x) & set(y)) / len(set(x) | set(y))


def _download(name: str) -> Path | None:
    path = DIR / f"{name}.zip"
    if path.exists():
        return path
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(f"{BASE}{name}.zip", timeout=300)
    except requests.RequestException as exc:
        log.warning("IPEDS download failed %s: %s", name, exc)
        return None
    if resp.status_code != 200 or not resp.content.startswith(b"PK"):
        return None
    path.write_bytes(resp.content)
    log.info("IPEDS: downloaded %s.zip (%.1f MB)", name, len(resp.content) / 1e6)
    return path


def _read_zip_csv(path: Path):
    """Rows of the zip's CSV; the revised file (_rv) wins when NCES ships both."""
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if n.lower().endswith(".csv")), key=lambda n: "_rv" not in n.lower())
        with z.open(names[0]) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace"))


def _masters(year: int) -> dict[str, list[tuple[str, int, int, int]]] | None:
    """{unitid: [(cip, total, women, nonresident)]} for master's degrees (first majors), cached as a small CSV."""
    small = DIR / f"masters_{year}.csv"
    if not small.exists():
        path = _download(f"C{year}_A")
        if path is None:
            return None
        DIR.mkdir(parents=True, exist_ok=True)
        with open(small, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["UNITID", "CIPCODE", "CTOTALT", "CTOTALW", "CNRALT"])
            for r in _read_zip_csv(path):
                if (int(r["AWLEVEL"] or 0) == MASTERS and int(r["MAJORNUM"] or 0) == 1  # older files pad: "07"
                        and r["CIPCODE"][:2] in ("30", "45", "52")):
                    w.writerow([r["UNITID"], r["CIPCODE"], r["CTOTALT"], r["CTOTALW"], r["CNRALT"]])
    out = {}
    with open(small, newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["UNITID"], []).append(
                (r["CIPCODE"], int(r["CTOTALT"] or 0), int(r["CTOTALW"] or 0), int(r["CNRALT"] or 0)))
    return out


class Ipeds:
    """Load once per run: the latest YEARS completions years NCES has published, and its institution directory."""

    def __init__(self, newest: int | None = None):
        self.years, self.data = [], {}
        for year in range(newest or date.today().year, 2009, -1):
            if (got := _masters(year)) is not None:
                self.years.insert(0, year)
                self.data[year] = got
                if len(self.years) == YEARS:
                    break
        self.schools = {}
        if self.years and (hd := _download(f"HD{self.years[-1]}")):
            for r in _read_zip_csv(hd):
                if r.get("CYACTIVE", "1") == "1":  # skip closed institutions
                    self.schools[r["UNITID"]] = (r["INSTNM"], r["IALIAS"], r["WEBADDR"], r["STABBR"])
        log.info("IPEDS completions years %s, %d institutions", self.years, len(self.schools))

    def match(self, university: str, url: str = "", online: bool = False, cips: tuple[str, ...] = ()) -> str | None:
        """UNITID of the school, or None when nothing matches clearly. Schools on the program page's web domain are
        tried first. Systems that report online programs as their own unit (Penn State World Campus, ASU Digital
        Immersion) get that unit for online sheets when it awarded degrees under the sheet's CIP codes."""
        dom = site(url) if url else ""
        same = [u for u, (_, _, web, _) in self.schools.items()
                if dom and web and site(web if "//" in web else f"https://{web}") == dom]
        for pool in ([same] if same else []) + [list(self.schools)]:
            scored = sorted(((max(name_score(university, n), name_score(university, a) if a else 0), u)
                             for u in pool for n, a, _, _ in [self.schools[u]]), reverse=True)
            if not scored or scored[0][0] <= 0:
                continue
            if len(pool) == 1:  # the only school on that domain, unless another school has this exact name
                exact = [u for u in self.schools if name_score(university, self.schools[u][0]) >= 1.9]
                return pool[0] if scored[0][0] >= 1.9 or len(exact) != 1 else exact[0]
            near = [u for s, u in scored if s >= 1.9]  # this name or one of its campuses
            units = [u for u in near if ONLINE_UNIT.search(self.schools[u][0])]
            if online and cips and (awarded := [u for u in units if self._awarded(u, cips)]):
                return awarded[0]
            if not online:
                scored = [(s, u) for s, u in scored if u not in units] or scored
            best = scored[0][0]
            if best >= 0.6 and (len(scored) == 1 or best - scored[1][0] >= 0.005):
                return scored[0][1]
        return None

    def _awarded(self, unitid: str, cips: tuple[str, ...]) -> bool:
        return any(r[0].startswith(cips) and r[1] for y in self.years for r in self.data[y].get(unitid, []))

    def completions(self, unitid: str, cips: tuple[str, ...], label: str) -> Completions | None:
        totals, women, nonres = [], 0, 0
        for year in self.years:
            rows = [r for r in self.data[year].get(unitid, []) if r[0].startswith(cips)]
            totals.append(sum(r[1] for r in rows))
            women += sum(r[2] for r in rows)
            nonres += sum(r[3] for r in rows)
        if not any(totals):
            return None
        return Completions(unitid, self.schools[unitid][0], f"{label} (CIP {', '.join(cips)})", list(self.years),
                           totals, women, nonres)

    def lookup(self, university: str, sheet: str, program: str = "", url: str = "",
               stated_cip: str | None = None) -> tuple[Completions | None, str]:
        """(completions, why there are none). stated_cip: the code the program's pages give; it replaces the
        sheet's codes."""
        cip = ((stated_cip,), "as stated on the program's pages") if stated_cip else cip_for(sheet, program)
        if not self.years:
            return None, "IPEDS files unavailable"
        if not cip:
            return None, f"no CIP code mapped for sheet '{sheet}'"
        unitid = self.match(university, url, "online" in f"{sheet} {program}".casefold(), cip[0])
        if unitid is None:
            log.info("IPEDS: no clear institution match for %r", university)
            return None, "no clear IPEDS institution match"
        got = self.completions(unitid, *cip)
        return got, "" if got else f"{self.schools[unitid][0]} reported no master's degrees under CIP {', '.join(cip[0])}"
