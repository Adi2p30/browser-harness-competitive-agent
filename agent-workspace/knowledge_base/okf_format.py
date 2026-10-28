"""Read/write a single Open Knowledge Format (OKF v0.2) concept file: YAML frontmatter
between `---` lines, then a markdown body. https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
"""
import re
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)", re.S)


def write_concept(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    path.write_text(f"---\n{fm}\n---\n\n{body.strip()}\n", encoding="utf-8")


def read_concept(path: Path) -> tuple[dict, str]:
    """{} frontmatter for a file with none (e.g. a hand-written index.md); OKF makes frontmatter
    required only on non-reserved concept files."""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2).lstrip("\n")
