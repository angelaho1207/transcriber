"""Turn a saved transcript into organized LaTeX notes via the Claude API."""

import os

import anthropic

from latex_sanitize import sanitize_latex_body
from settings import load_settings

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(APP_DIR, "config")
NOTES_DIR = os.path.join(APP_DIR, "notes")

MAX_OUTPUT_TOKENS = 16000


class NotesError(Exception):
    pass


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_system_prompt() -> str:
    return _read(os.path.join(CONFIG_DIR, "notes_system_prompt.txt"))


def load_template_preamble() -> str:
    return _read(os.path.join(CONFIG_DIR, "template_preamble.tex"))


def build_full_document(body: str) -> str:
    preamble = load_template_preamble().rstrip()
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
    full_document = build_full_document(body)

    os.makedirs(NOTES_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(transcript_path))[0]
    notes_path = os.path.join(NOTES_DIR, base.replace("transcript_", "notes_", 1) + ".tex")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(full_document)

    return {
        "body": body,
        "full_document": full_document,
        "notes_path": notes_path,
        "notes_filename": os.path.basename(notes_path),
        "truncated": truncated,
    }
