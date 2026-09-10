# Lecture Transcriber

Records lecture audio on Windows, transcribes it locally on the CPU with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(`distil-large-v3`, `int8`), and saves one clean `.txt` transcript per session.

It does **not** summarize. It produces an accurate transcript that you paste
into Claude separately for notes and diagrams.

## How it works

- Audio is captured in 60-second chunks at 16 kHz mono (no resampling).
- Each chunk is transcribed on a background thread **while the next chunk is
  still recording**, so transcription stays caught up with the live lecture.
- Each chunk's audio file (`audio_chunks/`) is deleted as soon as it is
  transcribed. Only text is kept.
- The transcript is autosaved after every chunk to
  `transcripts/transcript_<date>_<start-time>.txt` and finalized on Stop.
- On **Stop**, it flushes the final partial chunk, waits for the transcription
  queue to drain, concatenates all chunk transcripts in order, and writes the
  final file. The wait is short because transcription was keeping up during the
  lecture.

## Setup

Requires Python 3.10+ (tested on 3.14). No GPU, no ffmpeg needed.

```powershell
cd path\to\transcriber
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The `distil-large-v3` model (~1.5 GB) downloads automatically from Hugging Face
on the **first run** and is cached in `%USERPROFILE%\.cache\huggingface`.
First launch and the first chunk transcription are slower than steady state.

If Windows blocks the venv activation script:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Run

```powershell
.venv\Scripts\Activate.ps1
python transcriber.py
```

1. Wait for the status to say **Ready**.
2. Click **Start**, then switch to whatever you're doing. No need to watch it.
3. Click **Stop** at the end. Wait ~15-30 s for the status to read
   `Saved to transcripts\... — safe to close`, then close the laptop.

The status label shows the current state (recording chunk N / transcribed
N/M / finishing / saved) and elapsed time.

## Microphone permission

If Start fails, allow desktop apps to use the microphone:
**Settings > Privacy & security > Microphone**.

## Capturing Zoom / remote meeting audio (secondary use case)

Edit the config block near the top of `transcriber.py`:

```python
INPUT_DEVICE = "Speakers (Realtek...)"   # your OUTPUT device, name or index
LOOPBACK = True
```

Run `python -m sounddevice` to list device names and indices. Loopback records
exactly what you hear, so keep meeting audio playing through that device.

## Tuning (top of `transcriber.py`)

| Setting | Default | Notes |
| --- | --- | --- |
| `BEAM_SIZE` | `5` | Lower to `1` for roughly 2x speed if transcription falls behind the lecture. |
| `LANGUAGE` | `"en"` | Set to `None` to auto-detect per chunk. |
| `CPU_THREADS` | `0` | `0` = use all cores. |
| `VAD_FILTER` | `True` | Skips silence. |
| `CHUNK_SECONDS` | `60` | Chunk length. |

### Keeping up with real time

`distil-large-v3` + `int8` on CPU typically runs faster than real time, but on a
slow CPU a 60 s chunk could take longer than 60 s to transcribe, and the queue
would grow through the lecture (making the post-Stop wait long). If the status
shows the transcribed count falling steadily behind, set `BEAM_SIZE = 1`.
