"""Lecture Transcriber — record lecture audio and save a clean local transcript.

Records in 60-second chunks (16 kHz mono), transcribes each chunk on a
background thread with faster-whisper (distil-large-v3, CPU, int8) while the
next chunk is still recording, then concatenates the chunk transcripts in
order and writes one .txt file per session into ./transcripts.

This tool does NOT summarize. It only produces a transcript.
"""

import os
import queue
import threading
import time
import wave
import datetime as dt
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# --------------------------------------------------------------------------
# Configuration (the values below were chosen for CPU-only hardware; the
# tunables in the second group are safe to adjust).
# --------------------------------------------------------------------------
SAMPLE_RATE = 16000          # matches Whisper's expected input, no resampling
CHANNELS = 1                 # mono
CHUNK_SECONDS = 60           # length of each recorded/transcribed chunk

MODEL_NAME = "distil-large-v3"
COMPUTE_DEVICE = "cpu"
COMPUTE_TYPE = "int8"

# ---- Tunables ----
LANGUAGE = "en"              # set to None to auto-detect the language per chunk
VAD_FILTER = True            # skip silence (good for lectures with pauses)
BEAM_SIZE = 5                # lower to 1 for ~2x speed if transcription falls behind
CPU_THREADS = 0             # 0 = auto (use all CPU cores)

# ---- Optional: capture meeting/system audio (e.g. Zoom) instead of the mic ----
# Run `python -m sounddevice` to list devices and their indices.
# Priority use case (in-person lecture via built-in mic): leave both as-is.
# For Zoom / remote audio on Windows: set LOOPBACK = True and set INPUT_DEVICE
# to the name or index of your speakers/headphones (an *output* device).
INPUT_DEVICE = None          # device name or index, or None for the system default
LOOPBACK = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPTS_DIR = os.path.join(APP_DIR, "transcripts")
CHUNK_DIR = os.path.join(APP_DIR, "audio_chunks")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _write_wav(path: str, float_data: np.ndarray) -> None:
    """Write float32 audio in [-1, 1] to a 16-bit PCM WAV file."""
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


# --------------------------------------------------------------------------
# Recorder / transcriber engine
# --------------------------------------------------------------------------
class Recorder:
    """Owns the audio stream and the two background worker threads.

    - audio callback    -> pushes raw frames onto audio_q
    - collector thread  -> slices audio_q into CHUNK_SECONDS chunks, writes a
                           WAV per chunk, pushes (index, path) onto transcribe_q
    - transcriber thread-> transcribes each chunk, deletes its WAV, autosaves
    """

    def __init__(self, model: WhisperModel):
        self.model = model

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
        self.start_time: float | None = None
        self.stop_time: float | None = None

        self.stream: sd.InputStream | None = None
        self.collector_thread: threading.Thread | None = None
        self.transcriber_thread: threading.Thread | None = None

    # ---- audio capture ----
    def _on_audio(self, indata, frames, time_info, status):  # runs on PortAudio thread
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
        samples_per_chunk = SAMPLE_RATE * CHUNK_SECONDS
        buf = np.empty((0, CHANNELS), dtype=np.float32)

        while not self.stop_event.is_set():
            try:
                buf = np.concatenate([buf, self.audio_q.get(timeout=0.5)])
            except queue.Empty:
                continue
            while len(buf) >= samples_per_chunk:
                self._emit_chunk(buf[:samples_per_chunk])
                buf = buf[samples_per_chunk:]

        # Stop requested: drain any audio still queued, then flush the tail.
        while True:
            try:
                buf = np.concatenate([buf, self.audio_q.get_nowait()])
            except queue.Empty:
                break
        while len(buf) >= samples_per_chunk:
            self._emit_chunk(buf[:samples_per_chunk])
            buf = buf[samples_per_chunk:]
        if len(buf) > SAMPLE_RATE * 0.2:      # ignore a sub-0.2s sliver
            self._emit_chunk(buf)

        self.transcribe_q.put(None)           # sentinel: no more chunks

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
                    language=LANGUAGE,
                    vad_filter=VAD_FILTER,
                    beam_size=BEAM_SIZE,
                    condition_on_previous_text=False,   # each chunk is independent
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
            except Exception as e:  # keep going; note the gap in the transcript
                text = f"[[chunk {idx + 1} failed to transcribe: {e}]]"

            with self.results_lock:
                self.results[idx] = text
                self.chunks_transcribed += 1

            try:
                os.remove(path)                 # keep only the text
            except OSError:
                pass

            self._write_output()                # incremental autosave

    # ---- output ----
    def _write_output(self) -> None:
        if self.start_time is None:
            return
        if self.output_path is None:
            stamp = dt.datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d_%H-%M-%S")
            self.output_path = os.path.join(TRANSCRIPTS_DIR, f"transcript_{stamp}.txt")

        with self.results_lock:
            parts = [self.results[i] for i in sorted(self.results)]
        body = "\n\n".join(p for p in parts if p)

        tmp = self.output_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body + "\n" if body else "")
        os.replace(tmp, self.output_path)       # atomic

    # ---- shutdown ----
    def finalize(self) -> None:
        """Stop recording, transcribe everything left, write the final file."""
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


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Lecture Transcriber")
        root.geometry("400x190")
        root.minsize(400, 190)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.model: WhisperModel | None = None
        self.model_error: str | None = None
        self.recorder: Recorder | None = None

        self.status = tk.StringVar(value="Loading model (first run downloads ~1.5 GB)...")
        self.elapsed = tk.StringVar(value="00:00:00")

        ttk.Label(root, textvariable=self.status, wraplength=360,
                  justify="center").pack(pady=(18, 4))
        ttk.Label(root, textvariable=self.elapsed,
                  font=("Segoe UI", 22)).pack(pady=2)

        btns = ttk.Frame(root)
        btns.pack(pady=12)
        self.start_btn = ttk.Button(btns, text="Start", width=12,
                                    command=self._start, state="disabled")
        self.stop_btn = ttk.Button(btns, text="Stop", width=12,
                                   command=self._stop, state="disabled")
        self.start_btn.grid(row=0, column=0, padx=6)
        self.stop_btn.grid(row=0, column=1, padx=6)

        threading.Thread(target=self._load_model, daemon=True).start()
        self._poll()

    def _load_model(self) -> None:
        try:
            self.model = WhisperModel(
                MODEL_NAME,
                device=COMPUTE_DEVICE,
                compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS,
            )
        except Exception as e:
            self.model_error = str(e)

    def _start(self) -> None:
        if self.model is None:
            return
        self.recorder = Recorder(self.model)
        try:
            self.recorder.start()
        except Exception as e:
            self.recorder.state = "error"
            self.recorder.error = (
                f"{e}\n\nCheck Windows mic permissions (Settings > Privacy & "
                f"security > Microphone) or set INPUT_DEVICE in transcriber.py."
            )
            return
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def _stop(self) -> None:
        if not self.recorder or self.recorder.state != "recording":
            return
        self.stop_btn.config(state="disabled")
        threading.Thread(target=self.recorder.finalize, daemon=True).start()

    def _poll(self) -> None:
        r = self.recorder

        if r is None:
            if self.model is not None:
                self.status.set("Ready. Click Start.")
                self.start_btn.config(state="normal")
            elif self.model_error:
                self.status.set(f"Model failed to load:\n{self.model_error}")
        else:
            done, total = r.chunks_transcribed, r.chunks_emitted
            if r.state == "recording":
                self.elapsed.set(_hms(time.time() - r.start_time))
                self.status.set(
                    f"Recording chunk {total + 1}. "
                    f"Transcribed {done}/{total} chunks. You can switch apps."
                )
            elif r.state == "finalizing":
                self.status.set(
                    f"Finishing — transcribing remaining audio ({done}/{total}). "
                    f"Keep the lid open a few more seconds."
                )
            elif r.state == "done":
                self.elapsed.set(_hms(r.stop_time - r.start_time))
                rel = os.path.relpath(r.output_path, APP_DIR)
                self.status.set(f"Saved to {rel} — safe to close.")
                self.start_btn.config(state="normal")
            elif r.state == "error":
                self.status.set(f"Error:\n{r.error}")
                self.start_btn.config(state="normal")

        self.root.after(400, self._poll)

    def _on_close(self) -> None:
        r = self.recorder
        if r and r.state == "recording":
            if not messagebox.askokcancel(
                "Quit",
                "Still recording. Stop, transcribe the rest, and save before quitting?",
            ):
                return
            self.status.set("Finishing before quit — please wait...")
            self.root.update()
            r.finalize()
        elif r and r.state == "finalizing":
            messagebox.showinfo("Please wait", "Still saving the transcript. Try again in a moment.")
            return
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
