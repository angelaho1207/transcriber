# Lecture Transcriber

Records lecture audio on Windows, transcribes it locally on the CPU with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`distil-large-v3`,
`int8`), and turns the transcript into organized LaTeX study notes with one
click via the Claude API, which you can then open directly in Overleaf.

Runs as a small local web app (Flask backend, one page in your browser) —
nothing leaves your machine except the one Claude API call for note
generation, and the "Open in Overleaf" step (which goes straight from your
browser to Overleaf, not through this app).

## How it works

- Audio is captured in short chunks (default 25 s) at 16 kHz mono.
- Each chunk is transcribed on a background thread **while the next chunk is
  still recording**, so transcription stays caught up with the live lecture.
- Each chunk's audio file (`audio_chunks/`) is deleted as soon as it's
  transcribed. Only text is kept.
- The transcript is autosaved after every chunk to
  `transcripts/transcript_<date>_<start-time>.txt` and finalized on Stop.
- **Generate Notes** sends the finished transcript to Claude (Sonnet 5 by
  default) with a system prompt that organizes it into LaTeX and corrects
  likely mishearings using context, and saves the result to
  `notes/notes_<date>_<start-time>.tex`.
- **Open in Overleaf** submits that document straight from your browser to
  Overleaf's project-creation endpoint — it opens in whichever Overleaf
  account you're logged into, with no credentials passed through this app.

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

If Windows blocks the venv activation script:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Run

```powershell
.venv\Scripts\Activate.ps1
python app.py
```

or just double-click the **Start Transcriber** desktop shortcut — it launches
the server and opens your browser to it automatically.

1. Wait for the status to say **Ready**.
2. Click **Start**, then switch to whatever you're doing. No need to watch it.
3. Click **Stop** at the end. Wait a few seconds for
   `Saved to transcripts\... — safe to close`.
4. Click **Generate Notes** (needs an Anthropic API key — see below).
5. Click **Open in Overleaf** to open the notes as a new Overleaf project.

## Anthropic API key (required for Generate Notes only — recording/transcribing is free)

Recording and transcription are fully local and free. Turning a transcript
into LaTeX notes calls the Claude API, which needs its own key — **not** your
claude.ai Pro/Max subscription; subscriptions don't expose a programmatic API.

1. Create a key at [console.anthropic.com](https://console.anthropic.com) (a
   separate account/billing from claude.ai).
2. Open the app, click the gear icon, paste the key into **Anthropic API
   key**, click **Save Settings**. It's stored locally in
   `config/settings.json` (gitignored) and only ever sent directly to
   Anthropic's API from your machine.

Estimated cost at ~20 lectures/month of 90 min each: **roughly $1–5/month**
depending on the model you pick in Settings (Sonnet 5 is the default and
recommended balance; Haiku 4.5 is cheaper/weaker, Opus 5 is pricier/stronger).

## Microphone permission

If Start fails, allow desktop apps to use the microphone:
**Settings > Privacy & security > Microphone**.

## Capturing Zoom / remote meeting audio (secondary use case)

Edit the config block near the top of `engine.py`:

```python
INPUT_DEVICE = "Speakers (Realtek...)"   # your OUTPUT device, name or index
LOOPBACK = True
```

Run `python -m sounddevice` to list device names and indices. Loopback records
exactly what you hear, so keep meeting audio playing through that device.
(Requires restarting the app after editing.)

## Tuning (Settings panel in the app, or `config/settings.json` directly)

| Setting | Default | Notes |
| --- | --- | --- |
| `beam_size` | `5` | Lower to `1` for roughly 2x transcription speed if it falls behind the lecture. |
| `language` | `en` | Blank to auto-detect per chunk. |
| `initial_prompt` | *(empty)* | Course jargon / names, to bias transcription toward correct spelling. |
| `vad_filter` | `true` | Skips silence. |
| `chunk_seconds` | `25` | Shorter = less audio left over to transcribe after you hit Stop. |
| `notes_model` | `claude-sonnet-5` | `claude-opus-5` for higher quality notes, `claude-haiku-4-5` for cheapest. |
| `cpu_threads` | `0` | `0` = use all cores. |

### Keeping up with real time

`distil-large-v3` + `int8` on CPU typically runs faster than real time, but on
a slow CPU a chunk could take longer than its own length to transcribe, and
the queue would grow through the lecture (making the post-Stop wait long). If
the status shows the transcribed-chunk count falling steadily behind the
recording-chunk count, lower `beam_size` to `1`.

## Customizing the generated notes

- `config/template_preamble.tex` — your LaTeX preamble (packages, custom
  commands, title). Edit freely; it's prepended to every generated document.
- `config/notes_system_prompt.txt` — the instructions Claude follows when
  turning a transcript into notes. Edit freely to change structure,
  verbosity, or how aggressively it should correct likely transcription
  errors.

## Advanced: prevent sleep on lid-close during a lecture

Two desktop shortcuts, **Lecture Mode ON** / **Lecture Mode OFF**, toggle
whether closing the laptop lid sleeps it. Run ON before class if you want to
close the lid while recording, OFF afterward to restore normal battery-saving
behavior — see the scripts (`lecture_mode_on.cmd` / `lecture_mode_off.cmd`)
for details. With the lid closed the machine stays fully awake (no sleep),
so battery drains faster than normal; turn it back OFF when you're done.
