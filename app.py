"""Local web app: record -> transcribe -> generate LaTeX notes -> Overleaf.

Runs entirely on localhost. Launch with `python app.py` (or the desktop
shortcut), then use the page it opens in your browser.
"""

import os
import sys
import threading
import webbrowser

# pythonw.exe has no console, so sys.stdout/stderr are None. Flask/Werkzeug
# (and any library that prints/logs) crash the instant they try to write to
# that -- silently, since there's no console to show the error. Give them
# somewhere harmless to write instead, before anything else runs.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from flask import Flask, jsonify, render_template, request

import engine
import notes
from settings import load_settings, save_settings, masked

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 5175

app = Flask(__name__)

model_state = {"status": "loading", "error": None, "model": None}
recorder_lock = threading.Lock()
current_recorder: engine.Recorder | None = None
# Name typed in before Start is pressed -- attached to the Recorder once one
# exists (see api_start), so naming works at any point before or during a
# recording.
pending_lecture_name = ""

notes_lock = threading.Lock()
# status: idle | generating | done | error -- survives a browser refresh so the
# notes pane and Overleaf button can be restored even if the tab was reloaded
# while a generation was in flight.
notes_state = {"status": "idle", "error": None, "result": None}

active_lock = threading.Lock()
# The transcript Generate Notes / the transcript pane act on -- either the
# just-finished live recording, or a past session picked from the sidebar.
active_transcript_path: str | None = None


def _notes_path_for(transcript_path: str) -> str:
    return os.path.join(notes.NOTES_DIR, notes.notes_filename_for_transcript(transcript_path))


def _load_model_background():
    try:
        model_state["model"] = engine.load_model(load_settings())
        model_state["status"] = "ready"
    except Exception as e:
        model_state["status"] = "error"
        model_state["error"] = str(e)


threading.Thread(target=_load_model_background, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    with recorder_lock:
        snap = current_recorder.snapshot() if current_recorder else None
    with notes_lock:
        n_status, n_error, n_result = notes_state["status"], notes_state["error"], notes_state["result"]
    with active_lock:
        active_path = active_transcript_path

    if snap and snap["state"] in ("recording", "finalizing"):
        lecture_name = snap["lecture_name"]
    elif active_path:
        lecture_name = notes.read_lecture_name(active_path)
    elif snap:
        lecture_name = snap["lecture_name"]
    else:
        lecture_name = pending_lecture_name

    return jsonify({
        "model_status": model_state["status"],
        "model_error": model_state["error"],
        "recorder": snap,
        "notes_status": n_status,
        "notes_error": n_error,
        "notes_result": n_result,
        "lecture_name": lecture_name,
        "active_transcript_filename": os.path.basename(active_path) if active_path else None,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    global current_recorder, pending_lecture_name, active_transcript_path
    if model_state["status"] != "ready":
        return jsonify({"ok": False, "error": "Model isn't ready yet."}), 409
    with recorder_lock:
        if current_recorder is not None and current_recorder.state in ("recording", "finalizing"):
            return jsonify({"ok": False, "error": "Already recording."}), 409
        rec = engine.Recorder(model_state["model"], load_settings())
        try:
            rec.start()
        except Exception as e:
            return jsonify({"ok": False, "error": (
                f"{e}. Check Windows mic permissions (Settings > Privacy & "
                f"security > Microphone)."
            )}), 500
        rec.set_lecture_name(pending_lecture_name)
        pending_lecture_name = ""
        current_recorder = rec
    with active_lock:
        active_transcript_path = None   # a fresh recording takes over the view
    with notes_lock:
        notes_state["status"] = "idle"
        notes_state["error"] = None
        notes_state["result"] = None
    return jsonify({"ok": True})


def _finalize_and_activate(rec: engine.Recorder):
    global active_transcript_path
    rec.finalize()
    with active_lock:
        active_transcript_path = rec.current_output_path()


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with recorder_lock:
        rec = current_recorder
    if rec is None or rec.state != "recording":
        return jsonify({"ok": False, "error": "Not recording."}), 409
    threading.Thread(target=_finalize_and_activate, args=(rec,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/lecture_name", methods=["POST"])
def api_lecture_name():
    global pending_lecture_name
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    with recorder_lock:
        rec = current_recorder
        if rec is not None:
            rec.set_lecture_name(name)
        else:
            pending_lecture_name = name
    return jsonify({"ok": True})


@app.route("/api/transcript")
def api_transcript():
    with recorder_lock:
        rec = current_recorder
    if rec is not None and rec.state in ("recording", "finalizing"):
        return jsonify({"text": rec.transcript_text()})
    with active_lock:
        path = active_transcript_path
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jsonify({"text": f.read()})
    if rec is not None:
        return jsonify({"text": rec.transcript_text()})
    return jsonify({"text": ""})


@app.route("/api/transcripts")
def api_transcripts_list():
    items = []
    if os.path.isdir(engine.TRANSCRIPTS_DIR):
        for fname in os.listdir(engine.TRANSCRIPTS_DIR):
            if not fname.endswith(".txt") or fname.endswith(".name.txt"):
                continue
            path = os.path.join(engine.TRANSCRIPTS_DIR, fname)
            lecture_name = notes.read_sidecar_name(path)
            items.append({
                "filename": fname,
                "lecture_name": lecture_name or fname,
                "modified": os.path.getmtime(path),
                "has_notes": os.path.exists(_notes_path_for(path)),
            })
    items.sort(key=lambda it: it["modified"], reverse=True)
    return jsonify({"items": items})


@app.route("/api/select_transcript", methods=["POST"])
def api_select_transcript():
    global active_transcript_path
    data = request.get_json(force=True, silent=True) or {}
    filename = data.get("filename") or ""
    # filename only, no path traversal -- must resolve to a direct child of transcripts/
    if not filename or os.path.basename(filename) != filename:
        return jsonify({"ok": False, "error": "Invalid filename."}), 400
    path = os.path.join(engine.TRANSCRIPTS_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "Transcript not found."}), 404

    with recorder_lock:
        rec = current_recorder
    if rec is not None and rec.state in ("recording", "finalizing"):
        return jsonify({"ok": False, "error": "Stop the current recording first."}), 409

    with active_lock:
        active_transcript_path = path
    with open(path, "r", encoding="utf-8") as f:
        transcript_text = f.read()

    notes_path = _notes_path_for(path)
    with notes_lock:
        if os.path.exists(notes_path):
            with open(notes_path, "r", encoding="utf-8") as f:
                full_document = f.read()
            notes_state["status"] = "done"
            notes_state["error"] = None
            notes_state["result"] = {
                "full_document": full_document,
                "notes_path": notes_path,
                "notes_filename": os.path.basename(notes_path),
                "truncated": False,
            }
        else:
            notes_state["status"] = "idle"
            notes_state["error"] = None
            notes_state["result"] = None

    return jsonify({"ok": True, "transcript_text": transcript_text})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(masked(load_settings()))


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.get_json(force=True, silent=True) or {}
    # Don't overwrite a stored key with the masked placeholder the browser echoes back.
    if "anthropic_api_key" in data and "*" in data["anthropic_api_key"]:
        data.pop("anthropic_api_key")
    updated = save_settings(data)
    return jsonify(masked(updated))


def _run_generate_notes(transcript_path: str):
    try:
        result = notes.generate_notes(transcript_path)
    except notes.NotesError as e:
        with notes_lock:
            notes_state["status"] = "error"
            notes_state["error"] = str(e)
        return
    except Exception as e:
        with notes_lock:
            notes_state["status"] = "error"
            notes_state["error"] = f"Unexpected error: {e}"
        return
    with notes_lock:
        notes_state["status"] = "done"
        notes_state["result"] = result


@app.route("/api/generate_notes", methods=["POST"])
def api_generate_notes():
    with active_lock:
        path = active_transcript_path
    if not path:
        with recorder_lock:
            rec = current_recorder
        if rec is not None and rec.state == "done":
            path = rec.current_output_path()
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "Finish a recording, or pick a past one from the sidebar, first."}), 409
    with notes_lock:
        if notes_state["status"] == "generating":
            return jsonify({"ok": False, "error": "Already generating notes."}), 409
        notes_state["status"] = "generating"
        notes_state["error"] = None
        notes_state["result"] = None
    threading.Thread(target=_run_generate_notes, args=(path,), daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
