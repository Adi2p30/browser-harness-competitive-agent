"""Renders the small markdown subset okf_export.py itself generates (# / ## headings, | tables |,
- bullet lists, [text](url) links, `code`, ``` fenced blocks, plain paragraphs) — not a general
markdown parser. All text is escaped before the link / code substitutions reopen just our own tags.
Tables are wrapped in a scroll box and every cell's content in a `.cell` div (clamped by CSS, full
text in its title)."""
import html
import re

LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    out = LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', html.escape(text))
    return CODE.sub(r"<code>\1</code>", out)


def _plain(text: str) -> str:
    """Cell text without markdown link syntax, for the title tooltip."""
    return html.escape(LINK.sub(r"\1", text).replace("`", ""), quote=True)


def _cell(tag: str, text: str) -> str:
    if not text:
        return f"<{tag}></{tag}>"
    return f'<{tag}><div class="cell" title="{_plain(text)}">{_inline(text)}</div></{tag}>'


def render(body: str) -> str:
    out, lines, i = [], body.strip("\n").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append(f"<pre class=\"code\"><code>{html.escape(chr(10).join(block))}</code></pre>")
        elif line.startswith("## "):
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
            if body_rows and all(re.fullmatch(r":?-+:?", c) for c in body_rows[0]):
                body_rows = body_rows[1:]
            thead = "".join(_cell("th", c) for c in header)
            trs = "".join("<tr>" + "".join(_cell("td", c) for c in r) + "</tr>" for r in body_rows)
            out.append(f'<div class="okf-table"><table><thead><tr>{thead}</tr></thead>'
                       f"<tbody>{trs}</tbody></table></div>")
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


def drop_title(body: str, title: str) -> str:
    """The body without a leading '# <title>' heading that only repeats the page title."""
    lines = body.lstrip("\n").split("\n")
    if lines and lines[0].strip() == f"# {title}".strip():
        return "\n".join(lines[1:])
    return body
