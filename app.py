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

notes_lock = threading.Lock()
# status: idle | generating | done | error -- survives a browser refresh so the
# notes pane and Overleaf button can be restored even if the tab was reloaded
# while a generation was in flight.
notes_state = {"status": "idle", "error": None, "result": None}


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
    return jsonify({
        "model_status": model_state["status"],
        "model_error": model_state["error"],
        "recorder": snap,
        "notes_status": n_status,
        "notes_error": n_error,
        "notes_result": n_result,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    global current_recorder
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
        current_recorder = rec
    with notes_lock:
        notes_state["status"] = "idle"
        notes_state["error"] = None
        notes_state["result"] = None
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with recorder_lock:
        rec = current_recorder
    if rec is None or rec.state != "recording":
        return jsonify({"ok": False, "error": "Not recording."}), 409
    threading.Thread(target=rec.finalize, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/transcript")
def api_transcript():
    with recorder_lock:
        rec = current_recorder
    if rec is None:
        return jsonify({"text": ""})
    return jsonify({"text": rec.transcript_text()})


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
    with recorder_lock:
        rec = current_recorder
    if rec is None or not rec.output_path or rec.state != "done":
        return jsonify({"ok": False, "error": "Finish a recording first."}), 409
    with notes_lock:
        if notes_state["status"] == "generating":
            return jsonify({"ok": False, "error": "Already generating notes."}), 409
        notes_state["status"] = "generating"
        notes_state["error"] = None
        notes_state["result"] = None
    threading.Thread(target=_run_generate_notes, args=(rec.output_path,), daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
