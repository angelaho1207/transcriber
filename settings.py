"""Load/save user-editable settings (config/settings.json, gitignored)."""

import json
import os
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(APP_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "anthropic_api_key": "",
    "notes_model": "claude-sonnet-5",
    "language": "en",          # set to null/None to auto-detect per chunk
    "initial_prompt": "",      # course jargon / names to bias transcription toward
    "chunk_seconds": 25,
    "beam_size": 5,
    "vad_filter": True,
    "cpu_threads": 0,          # 0 = auto (all cores)
}

_lock = threading.Lock()


def load_settings() -> dict:
    with _lock:
        if not os.path.exists(SETTINGS_PATH):
            return dict(DEFAULTS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULTS)
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged


def save_settings(partial: dict) -> dict:
    with _lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        current = dict(DEFAULTS)
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    current.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
            except (json.JSONDecodeError, OSError):
                pass
        current.update({k: v for k, v in partial.items() if k in DEFAULTS})
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return current


def masked(settings: dict) -> dict:
    """Copy of settings safe to send to the browser (API key partially hidden)."""
    out = dict(settings)
    key = out.get("anthropic_api_key", "")
    out["anthropic_api_key_set"] = bool(key)
    out["anthropic_api_key"] = ("*" * max(0, len(key) - 4)) + key[-4:] if key else ""
    return out
