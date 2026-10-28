"""Renders the small markdown subset okf_export.py itself generates (# / ## headings, | tables |,
- bullet lists, [text](url) links, plain paragraphs) — not a general markdown parser. All text
is escaped before a link substitution reopens just the `<a>` tags for our own [..](..) syntax."""
import html
import re

LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")


def _inline(text: str) -> str:
    return LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', html.escape(text))


def render(body: str) -> str:
    out, lines, i = [], body.strip("\n").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            i -= 1
            header, *body_rows = rows
            if body_rows and all(re.fullmatch(r"-+", c) for c in body_rows[0]):
                body_rows = body_rows[1:]
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body_rows)
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            i -= 1
            out.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(out)
