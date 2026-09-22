(() => {
  const statusEl = document.getElementById("status");
  const elapsedEl = document.getElementById("elapsed");
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const generateBtn = document.getElementById("generateBtn");
  const overleafBtn = document.getElementById("overleafBtn");
  const transcriptBox = document.getElementById("transcriptBox");
  const notesBox = document.getElementById("notesBox");
  const errorBanner = document.getElementById("errorBanner");
  const settingsToggle = document.getElementById("settingsToggle");
  const settingsPanel = document.getElementById("settingsPanel");
  const settingsForm = document.getElementById("settingsForm");
  const settingsSaved = document.getElementById("settingsSaved");

  let latestDocument = null;   // full generated .tex, for the Overleaf button
  let lastRecorderState = null;
  let lastNotesStatus = null;

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.classList.remove("hidden");
    setTimeout(() => errorBanner.classList.add("hidden"), 8000);
  }

  async function poll() {
    try {
      const res = await fetch("/api/state");
      const data = await res.json();
      applyState(data);
    } catch (e) {
      statusEl.textContent = "Lost connection to the local server.";
    }
    setTimeout(poll, 1000);
  }

  function applyState(data) {
    const rec = data.recorder;

    if (data.model_status === "loading") {
      statusEl.textContent = "Loading model (first run downloads ~1.5 GB)…";
      startBtn.disabled = true;
    } else if (data.model_status === "error") {
      statusEl.textContent = "Model failed to load: " + data.model_error;
      startBtn.disabled = true;
    } else if (!rec) {
      statusEl.textContent = "Ready. Click Start.";
      startBtn.disabled = false;
      stopBtn.disabled = true;
    } else if (rec.state === "recording") {
      statusEl.textContent = `Recording chunk ${rec.chunks_emitted + 1}. ` +
        `Transcribed ${rec.chunks_transcribed}/${rec.chunks_emitted} chunks. You can switch apps.`;
      elapsedEl.textContent = rec.elapsed;
      startBtn.disabled = true;
      stopBtn.disabled = false;
      generateBtn.disabled = true;
    } else if (rec.state === "finalizing") {
      statusEl.textContent = `Finishing — transcribing remaining audio ` +
        `(${rec.chunks_transcribed}/${rec.chunks_emitted}). Keep the lid open a few more seconds.`;
      startBtn.disabled = true;
      stopBtn.disabled = true;
    } else if (rec.state === "done") {
      statusEl.textContent = `Saved to transcripts\\${rec.output_filename} — safe to close.`;
      elapsedEl.textContent = rec.elapsed;
      startBtn.disabled = false;
      stopBtn.disabled = true;
    } else if (rec.state === "error") {
      statusEl.textContent = "Error: " + rec.error;
      startBtn.disabled = false;
      stopBtn.disabled = true;
    }

    if (rec && (rec.state === "recording" || rec.state === "finalizing")) {
      refreshTranscript();
    }
    if (rec && rec.state === "done" && lastRecorderState !== "done") {
      refreshTranscript();
    }
    lastRecorderState = rec ? rec.state : null;

    applyNotesState(data);
  }

  // Notes generation runs server-side and survives a browser refresh --
  // this restores the UI to match whatever the server is actually doing,
  // whether this tab started the generation or a previous one did.
  function applyNotesState(data) {
    const status = data.notes_status;
    const changed = status !== lastNotesStatus;
    lastNotesStatus = status;

    if (status === "generating") {
      generateBtn.disabled = true;
      generateBtn.textContent = "Generating…";
      overleafBtn.disabled = true;
    } else if (status === "done") {
      generateBtn.disabled = false;
      generateBtn.textContent = "Generate Notes";
      if (changed && data.notes_result) {
        notesBox.value = data.notes_result.full_document;
        latestDocument = data.notes_result.full_document;
        if (data.notes_result.truncated) {
          showError("Notes may be incomplete: the model hit its output limit. Consider a shorter lecture or splitting it.");
        }
      }
      overleafBtn.disabled = !latestDocument;
    } else if (status === "error") {
      generateBtn.disabled = false;
      generateBtn.textContent = "Generate Notes";
      if (changed) showError(data.notes_error);
    } else {
      generateBtn.textContent = "Generate Notes";
      if (lastRecorderState === "done") generateBtn.disabled = false;
    }
  }

  async function refreshTranscript() {
    try {
      const res = await fetch("/api/transcript");
      const data = await res.json();
      const atBottom = Math.abs(transcriptBox.scrollHeight - transcriptBox.scrollTop - transcriptBox.clientHeight) < 20;
      transcriptBox.value = data.text;
      if (atBottom) transcriptBox.scrollTop = transcriptBox.scrollHeight;
    } catch (e) { /* ignore, next poll retries */ }
  }

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    transcriptBox.value = "";
    notesBox.value = "";
    latestDocument = null;
    overleafBtn.disabled = true;
    const res = await fetch("/api/start", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error);
      startBtn.disabled = false;
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    const res = await fetch("/api/stop", { method: "POST" });
    const data = await res.json();
    if (!data.ok) showError(data.error);
  });

  generateBtn.addEventListener("click", async () => {
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating…";
    notesBox.value = "";
    latestDocument = null;
    overleafBtn.disabled = true;
    try {
      const res = await fetch("/api/generate_notes", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showError(data.error);
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate Notes";
      }
      // On success, the next poll() tick picks up notes_status "generating"
      // -> "done" from the server -- this keeps working even if this tab
      // (or another one) is refreshed before generation finishes.
    } catch (e) {
      showError("Failed to start note generation: " + e);
      generateBtn.disabled = false;
      generateBtn.textContent = "Generate Notes";
    }
  });

  overleafBtn.addEventListener("click", () => {
    if (!latestDocument) return;
    const form = document.createElement("form");
    form.method = "POST";
    form.action = "https://www.overleaf.com/docs";
    form.target = "_blank";
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "snip";
    input.value = latestDocument;
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    document.body.removeChild(form);
  });

  // ---- settings ----
  settingsToggle.addEventListener("click", () => {
    settingsPanel.classList.toggle("hidden");
  });

  async function loadSettings() {
    const res = await fetch("/api/settings");
    const s = await res.json();
    document.getElementById("apiKey").value = s.anthropic_api_key || "";
    document.getElementById("notesModel").value = s.notes_model;
    document.getElementById("language").value = s.language || "";
    document.getElementById("initialPrompt").value = s.initial_prompt || "";
    document.getElementById("chunkSeconds").value = s.chunk_seconds;
    document.getElementById("beamSize").value = s.beam_size;
    document.getElementById("vadFilter").checked = !!s.vad_filter;
  }

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      anthropic_api_key: document.getElementById("apiKey").value,
      notes_model: document.getElementById("notesModel").value,
      language: document.getElementById("language").value.trim(),
      initial_prompt: document.getElementById("initialPrompt").value,
      chunk_seconds: parseInt(document.getElementById("chunkSeconds").value, 10),
      beam_size: parseInt(document.getElementById("beamSize").value, 10),
      vad_filter: document.getElementById("vadFilter").checked,
    };
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      settingsSaved.textContent = "Saved.";
      setTimeout(() => (settingsSaved.textContent = ""), 2000);
      loadSettings();
    } else {
      showError("Failed to save settings.");
    }
  });

  loadSettings();
  poll();
})();
