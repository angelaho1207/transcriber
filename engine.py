"""Recording + background transcription engine.

Records mic audio in short chunks (16 kHz mono), transcribes each chunk on a
background thread with faster-whisper while the next chunk is still
recording, deletes each chunk's audio the moment it's transcribed, and
autosaves the growing transcript. This module has no GUI/web dependency —
app.py drives it and reports its state over SSE.
"""

import os
import queue
import re
import threading
import time
import wave
import datetime as dt

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000     # matches Whisper's expected input, no resampling
CHANNELS = 1

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPTS_DIR = os.path.join(APP_DIR, "transcripts")
CHUNK_DIR = os.path.join(APP_DIR, "audio_chunks")

# Optional: capture meeting/system audio (e.g. Zoom) instead of the mic.
# Run `python -m sounddevice` to list devices. For loopback on Windows, set
# INPUT_DEVICE to your speakers/headphones (an *output* device) and LOOPBACK=True.
INPUT_DEVICE = None
LOOPBACK = False


def _hms(seconds) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _write_wav(path: str, float_data: np.ndarray) -> None:
    d = np.clip(float_data.reshape(-1, CHANNELS), -1.0, 1.0)
    pcm = (d * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def _clear_dir(path: str) -> None:
    for name in os.listdir(path):
        try:
            os.remove(os.path.join(path, name))
        except OSError:
            pass


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_SLUG_MAX_LEN = 60


def slugify_lecture_name(name: str) -> str:
    """A lecture name made safe as a Windows filename component. Empty if
    the name has no usable characters (e.g. it was blank or pure emoji)."""
    name = _INVALID_FILENAME_CHARS.sub("", name).strip()
    name = _WHITESPACE_RE.sub(" ", name).strip()
    name = name.replace(" ", "_")
    name = name.strip("._ ")
    return name[:_SLUG_MAX_LEN].rstrip("._ ")


def load_model(settings: dict) -> WhisperModel:
    return WhisperModel(
        "distil-large-v3",
        device="cpu",
        compute_type="int8",
        cpu_threads=int(settings.get("cpu_threads", 0) or 0),
    )


class Recorder:
    def __init__(self, model: WhisperModel, settings: dict):
        self.model = model
        self.chunk_seconds = int(settings.get("chunk_seconds", 25))
        self.beam_size = int(settings.get("beam_size", 5))
        self.language = settings.get("language") or None
        self.vad_filter = bool(settings.get("vad_filter", True))
        self.initial_prompt = settings.get("initial_prompt") or None

        self.audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self.transcribe_q: "queue.Queue" = queue.Queue()
        self.stop_event = threading.Event()

        self.results: dict[int, str] = {}
        self.results_lock = threading.Lock()

        self.chunks_emitted = 0
        self.chunks_transcribed = 0

        self.state = "idle"          # idle | recording | finalizing | done | error
        self.error: str | None = None
        self.output_path: str | None = None
        self.output_lock = threading.Lock()   # guards output_path + the file itself,
                                               # since the transcriber thread writes it
                                               # while a rename can arrive from a Flask
                                               # request thread at any moment
        self.start_time: float | None = None
        self.stop_time: float | None = None
        self.lecture_name: str = ""
        self.auto_stopped: bool = False   # set by app.py's 90-minute watchdog

        self.stream: sd.InputStream | None = None
        self.collector_thread: threading.Thread | None = None
        self.transcriber_thread: threading.Thread | None = None

    # ---- audio capture ----
    def _on_audio(self, indata, frames, time_info, status):  # PortAudio thread
        self.audio_q.put(indata.copy())

    def start(self) -> None:
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        os.makedirs(CHUNK_DIR, exist_ok=True)
        _clear_dir(CHUNK_DIR)

        extra = sd.WasapiSettings(loopback=True) if LOOPBACK else None
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=INPUT_DEVICE,
            callback=self._on_audio,
            extra_settings=extra,
        )

        self.start_time = time.time()
        self.state = "recording"
        self.stream.start()

        self.collector_thread = threading.Thread(target=self._collect, daemon=True)
        self.transcriber_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self.collector_thread.start()
        self.transcriber_thread.start()

    # ---- chunking ----
    def _collect(self) -> None:
        samples_per_chunk = SAMPLE_RATE * self.chunk_seconds
        buf = np.empty((0, CHANNELS), dtype=np.float32)

        while not self.stop_event.is_set():
            try:
                buf = np.concatenate([buf, self.audio_q.get(timeout=0.5)])
            except queue.Empty:
                continue
            while len(buf) >= samples_per_chunk:
                self._emit_chunk(buf[:samples_per_chunk])
                buf = buf[samples_per_chunk:]

        while True:
            try:
                buf = np.concatenate([buf, self.audio_q.get_nowait()])
            except queue.Empty:
                break
        while len(buf) >= samples_per_chunk:
            self._emit_chunk(buf[:samples_per_chunk])
            buf = buf[samples_per_chunk:]
        if len(buf) > SAMPLE_RATE * 0.2:
            self._emit_chunk(buf)

        self.transcribe_q.put(None)   # sentinel

    def _emit_chunk(self, chunk: np.ndarray) -> None:
        idx = self.chunks_emitted
        self.chunks_emitted += 1
        path = os.path.join(CHUNK_DIR, f"chunk_{idx:05d}.wav")
        _write_wav(path, chunk)
        self.transcribe_q.put((idx, path))

    # ---- transcription ----
    def _transcribe_loop(self) -> None:
        while True:
            item = self.transcribe_q.get()
            if item is None:
                break
            idx, path = item
            try:
                segments, _ = self.model.transcribe(
                    path,
                    language=self.language,
                    vad_filter=self.vad_filter,
                    beam_size=self.beam_size,
                    initial_prompt=self.initial_prompt,
                    condition_on_previous_text=False,
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
            except Exception as e:
                text = f"[[chunk {idx + 1} failed to transcribe: {e}]]"

            with self.results_lock:
                self.results[idx] = text
                self.chunks_transcribed += 1

            try:
                os.remove(path)
            except OSError:
                pass

            self._write_output()

    # ---- lecture naming ----
    def _build_filename_base(self) -> str:
        stamp = dt.datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d_%H-%M-%S")
        slug = slugify_lecture_name(self.lecture_name) if self.lecture_name else ""
        return f"{slug}_{stamp}" if slug else f"transcript_{stamp}"

    @staticmethod
    def _sidecar_path(output_path: str) -> str:
        if output_path.endswith(".txt"):
            return output_path[:-4] + ".name.txt"
        return output_path + ".name.txt"

    def set_lecture_name(self, name: str) -> None:
        """Safe to call any time -- before the file exists, while it's being
        actively written by the transcriber thread, or long after Stop."""
        with self.output_lock:
            changed = name != self.lecture_name
            self.lecture_name = name
            if self.output_path is None:
                return   # baked into the filename whenever _write_output first runs
            if changed:
                self._rename_output_locked()
            else:
                self._write_name_sidecar_locked()

    def _rename_output_locked(self) -> None:
        """Rename the transcript (and its sidecar) to match the current lecture
        name. Caller must hold output_lock. Renaming just the display name is
        not enough -- the filename itself should reflect it too."""
        new_path = os.path.join(TRANSCRIPTS_DIR, f"{self._build_filename_base()}.txt")
        if new_path != self.output_path:
            old_path, old_sidecar = self.output_path, self._sidecar_path(self.output_path)
            try:
                if os.path.exists(old_path):
                    os.replace(old_path, new_path)
                self.output_path = new_path
            except OSError:
                pass   # keep the old path if the rename fails; name is still tracked
            else:
                try:
                    os.remove(old_sidecar)
                except OSError:
                    pass
        self._write_name_sidecar_locked()

    def _write_name_sidecar_locked(self) -> None:
        if not self.lecture_name or not self.output_path:
            return
        try:
            with open(self._sidecar_path(self.output_path), "w", encoding="utf-8") as f:
                f.write(self.lecture_name)
        except OSError:
            pass

    # ---- output ----
    def _write_output(self) -> None:
        if self.start_time is None:
            return
        with self.output_lock:
            if self.output_path is None:
                self.output_path = os.path.join(TRANSCRIPTS_DIR, f"{self._build_filename_base()}.txt")
                self._write_name_sidecar_locked()  # flush a name set before the first chunk finished

            with self.results_lock:
                parts = [self.results[i] for i in sorted(self.results)]
            body = "\n\n".join(p for p in parts if p)

            tmp = self.output_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body + "\n" if body else "")
            os.replace(tmp, self.output_path)

    def current_output_path(self) -> str | None:
        with self.output_lock:
            return self.output_path

    def transcript_text(self) -> str:
        with self.results_lock:
            parts = [self.results[i] for i in sorted(self.results)]
        return "\n\n".join(p for p in parts if p)

    # ---- shutdown ----
    def finalize(self) -> None:
        if self.state != "recording":
            return
        self.state = "finalizing"
        self.stop_time = time.time()

        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

        self.stop_event.set()
        if self.collector_thread:
            self.collector_thread.join()
        if self.transcriber_thread:
            self.transcriber_thread.join()

        self._write_output()
        self.state = "done"

    # ---- status snapshot for the web UI ----
    def snapshot(self) -> dict:
        now = time.time()
        if self.state == "recording":
            elapsed = now - self.start_time
        elif self.stop_time is not None and self.start_time is not None:
            elapsed = self.stop_time - self.start_time
        else:
            elapsed = 0
        with self.output_lock:
            output_path = self.output_path
        return {
            "state": self.state,
            "error": self.error,
            "chunks_emitted": self.chunks_emitted,
            "chunks_transcribed": self.chunks_transcribed,
            "elapsed": _hms(elapsed),
            "elapsed_seconds": int(elapsed),
            "output_path": output_path,
            "output_filename": os.path.basename(output_path) if output_path else None,
            "lecture_name": self.lecture_name,
            "auto_stopped": self.auto_stopped,
        }
