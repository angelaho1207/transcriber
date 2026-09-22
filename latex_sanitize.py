"""Safety net for LLM-generated LaTeX that leaks Markdown syntax.

The system prompt tells Claude never to use Markdown, but LLMs don't follow
formatting instructions with 100% reliability, especially over long outputs.
A bare '#', unwrapped '- ' bullet list, etc. breaks LaTeX compilation
outright (e.g. "You can't use macro parameter character #' in vertical
mode"), so this converts the common Markdown patterns to real LaTeX before
the document is ever written out or sent to Overleaf.

This is deliberately narrow -- it only handles the patterns actually seen
in practice (headers, bullet/numbered lists, bold, horizontal rules, code
fences, stray '#'). It is not a general Markdown parser and won't touch
anything that isn't one of these specific patterns, so hand-written LaTeX
in the response passes through unchanged.
"""

import re

_CODE_FENCE_RE = re.compile(r"^```.*$")
_HRULE_RE = re.compile(r"^(-{3,}|\*{3,})\s*$")
_HEADER_RE = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*\S)\s*$")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s+(.*\S)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

_SECTION_CMD_BY_LEVEL = {1: "section", 2: "subsection", 3: "subsubsection", 4: "paragraph"}


def sanitize_latex_body(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_stack: list[str] = []

    def close_lists():
        while list_stack:
            out.append(f"\\end{{{list_stack.pop()}}}")

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if _CODE_FENCE_RE.match(stripped):
            continue  # drop stray markdown code fences entirely

        if _HRULE_RE.match(stripped):
            close_lists()
            continue  # a new \section already separates sections

        header_match = _HEADER_RE.match(stripped)
        if header_match:
            close_lists()
            level = len(header_match.group(1))
            cmd = _SECTION_CMD_BY_LEVEL.get(level, "paragraph")
            out.append(f"\\{cmd}{{{header_match.group(2)}}}")
            continue

        bullet_match = _BULLET_RE.match(stripped)
        if bullet_match:
            if not list_stack or list_stack[-1] != "itemize":
                close_lists()
                list_stack.append("itemize")
                out.append("\\begin{itemize}")
            out.append(f"\\item {bullet_match.group(1)}")
            continue

        numbered_match = _NUMBERED_RE.match(stripped)
        if numbered_match:
            if not list_stack or list_stack[-1] != "enumerate":
                close_lists()
                list_stack.append("enumerate")
                out.append("\\begin{enumerate}")
            out.append(f"\\item {numbered_match.group(1)}")
            continue

        if stripped == "":
            close_lists()
            out.append("")
            continue

        close_lists()
        out.append(line)

    close_lists()
    body = "\n".join(out)
    body = _BOLD_RE.sub(r"\\textbf{\1}", body)
    # Any '#' surviving the header pass is a literal character, not LaTeX
    # syntax -- escape it so it can't break compilation.
    body = re.sub(r"(?<!\\)#", r"\\#", body)
    return body
