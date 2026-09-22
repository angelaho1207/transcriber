"""Lecture Mode: whether closing the laptop lid sleeps the machine.

Wraps the same `powercfg` lid-close-action setting the desktop shortcuts
(lecture_mode_on.cmd / lecture_mode_off.cmd) toggle, so the web UI and those
shortcuts stay in sync -- there's only one underlying Windows setting.
"""

import re
import subprocess

_CREATE_NO_WINDOW = 0x08000000  # avoid a console flash when spawning powercfg

_QUERY_ARGS = ["powercfg", "/qh", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION"]
_AC_INDEX_RE = re.compile(r"Current AC Power Setting Index:\s*0x([0-9A-Fa-f]+)")


def get_lecture_mode() -> bool:
    """True if lid-close currently does nothing (Lecture Mode ON)."""
    try:
        result = subprocess.run(
            _QUERY_ARGS, capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        match = _AC_INDEX_RE.search(result.stdout)
        if match:
            return int(match.group(1), 16) == 0
    except (OSError, subprocess.SubprocessError):
        pass
    return False  # can't tell -- report as off/normal rather than guess


def set_lecture_mode(on: bool) -> None:
    index = "0" if on else "1"
    for args in (
        ["powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", index],
        ["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", index],
        ["powercfg", "/setactive", "SCHEME_CURRENT"],
    ):
        subprocess.run(args, check=True, timeout=5, creationflags=_CREATE_NO_WINDOW)
