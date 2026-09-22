"""Turn a saved transcript into organized LaTeX notes via the Claude API."""

import datetime as dt
import os
import re

import anthropic

from latex_sanitize import sanitize_latex_body
from settings import load_settings

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(APP_DIR, "config")
NOTES_DIR = os.path.join(APP_DIR, "notes")

MAX_OUTPUT_TOKENS = 16000

_TRANSCRIPT_STAMP_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$")
_LATEX_ESCAPE_RE = re.compile(r"([#%&_$])")


class NotesError(Exception):
    pass


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _escape_latex(s: str) -> str:
    return _LATEX_ESCAPE_RE.sub(r"\\\1", s)


def sidecar_name_path(transcript_path: str) -> str:
    """Path to the small sidecar file that stores this session's lecture name."""
    if transcript_path.endswith(".txt"):
        return transcript_path[:-4] + ".name.txt"
    return transcript_path + ".name.txt"


def read_sidecar_name(transcript_path: str) -> str | None:
    """The lecture name the user set for this session, or None if unset."""
    sidecar = sidecar_name_path(transcript_path)
    if os.path.exists(sidecar):
        name = _read(sidecar).strip()
        if name:
            return name
    return None


def read_lecture_name(transcript_path: str) -> str:
    """Like read_sidecar_name, but with a generic fallback for document titles."""
    return read_sidecar_name(transcript_path) or "Lecture Notes"


def _format_lecture_date(transcript_path: str) -> str:
    base = os.path.splitext(os.path.basename(transcript_path))[0]
    match = _TRANSCRIPT_STAMP_RE.search(base)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        d = dt.date(year, month, day)
    else:
        d = dt.date.today()
    return f"{d.day} {d.strftime('%B %Y')}"


def notes_filename_for_transcript(transcript_path: str) -> str:
    """notes/ filename for a given transcript. Handles both the original
    "transcript_<stamp>.txt" naming and newer custom-named files (e.g.
    "Linear_Algebra_Lecture_5_<stamp>.txt") without colliding with each
    other or duplicating the prefix."""
    base = os.path.splitext(os.path.basename(transcript_path))[0]
    if base.startswith("transcript_"):
        base = base[len("transcript_"):]
    return f"notes_{base}.tex"


def load_system_prompt() -> str:
    return _read(os.path.join(CONFIG_DIR, "notes_system_prompt.txt"))


def load_template_preamble() -> str:
    return _read(os.path.join(CONFIG_DIR, "template_preamble.tex"))


def build_full_document(body: str, lecture_name: str, author_name: str, date_str: str) -> str:
    preamble = load_template_preamble().rstrip()
    preamble = (
        preamble
        .replace("<<LECTURE_NAME>>", _escape_latex(lecture_name))
        .replace("<<AUTHOR_NAME>>", _escape_latex(author_name))
        .replace("<<DATE>>", _escape_latex(date_str))
    )
    return (
        f"{preamble}\n\n\\begin{{document}}\n\n\\maketitle\n\n"
        f"{body.strip()}\n\n\\end{{document}}\n"
    )


def generate_notes(transcript_path: str) -> dict:
    """Reads the transcript at transcript_path, returns {body, full_document,
    notes_path, truncated}. Raises NotesError with a user-facing message on
    any failure (missing key, empty transcript, API error)."""
    settings = load_settings()
    api_key = settings.get("anthropic_api_key")
    if not api_key:
        raise NotesError("No Anthropic API key set. Add one in Settings first.")

    if not os.path.exists(transcript_path):
        raise NotesError(f"Transcript file not found: {transcript_path}")
    transcript_text = _read(transcript_path).strip()
    if not transcript_text:
        raise NotesError("Transcript is empty -- nothing to generate notes from.")

    model = settings.get("notes_model") or "claude-sonnet-5"
    client = anthropic.Anthropic(api_key=api_key).with_options(timeout=300.0)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=load_system_prompt(),
            messages=[{"role": "user", "content": transcript_text}],
        )
    except anthropic.AuthenticationError:
        raise NotesError("Anthropic API key was rejected. Check it in Settings.")
    except anthropic.APIStatusError as e:
        raise NotesError(f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise NotesError("Couldn't reach the Claude API. Check your internet connection.")

    if response.stop_reason == "refusal":
        raise NotesError("Claude declined to process this transcript.")

    body = "".join(b.text for b in response.content if b.type == "text").strip()
    if not body:
        raise NotesError("Claude returned an empty response.")

    body = sanitize_latex_body(body)
    truncated = response.stop_reason == "max_tokens"
    lecture_name = read_lecture_name(transcript_path)
    author_name = settings.get("author_name") or ""
    date_str = _format_lecture_date(transcript_path)
    full_document = build_full_document(body, lecture_name, author_name, date_str)

    os.makedirs(NOTES_DIR, exist_ok=True)
    notes_path = os.path.join(NOTES_DIR, notes_filename_for_transcript(transcript_path))
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(full_document)

    return {
        "body": body,
        "full_document": full_document,
        "notes_path": notes_path,
        "notes_filename": os.path.basename(notes_path),
        "truncated": truncated,
    }
