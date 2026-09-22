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
  const lectureNameInput = document.getElementById("lectureName");
  const sidebarList = document.getElementById("sidebarList");
  const lectureModePill = document.getElementById("lectureModePill");
  const lectureModeToggle = document.getElementById("lectureModeToggle");
  const noticeBanner = document.getElementById("noticeBanner");

  let latestDocument = null;   // full generated .tex, for the Overleaf button
  let lastRecorderState = null;
  let lastNotesStatus = null;
  let lastNotesResultPath = null;   // detects switching between two already-"done" sessions
  let stopReminderShown = false;    // resets each time a new recording starts

  const STOP_REMINDER_SECONDS = 50 * 60;

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.classList.remove("hidden");
    setTimeout(() => errorBanner.classList.add("hidden"), 8000);
  }

  function showNotice(msg) {
    noticeBanner.textContent = msg + "  (click to dismiss)";
    noticeBanner.classList.remove("hidden");
  }

  function hideNotice() {
    noticeBanner.classList.add("hidden");
  }

  noticeBanner.addEventListener("click", hideNotice);

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
      statusEl.textContent = data.active_transcript_filename
        ? `Viewing "${data.lecture_name}" (transcripts\\${data.active_transcript_filename}).`
        : "Ready. Click Start.";
      startBtn.disabled = false;
      stopBtn.disabled = true;
    } else if (rec.state === "recording") {
      statusEl.textContent = `Recording chunk ${rec.chunks_emitted + 1}. ` +
        `Transcribed ${rec.chunks_transcribed}/${rec.chunks_emitted} chunks. You can switch apps.`;
      elapsedEl.textContent = rec.elapsed;
      startBtn.disabled = true;
      stopBtn.disabled = false;
      generateBtn.disabled = true;

      if (!stopReminderShown && rec.elapsed_seconds >= STOP_REMINDER_SECONDS) {
        stopReminderShown = true;
        const msg = "50 minutes recorded — stop if the lecture is over.";
        showNotice(msg);
        if (typeof Notification !== "undefined" && Notification.permission === "granted") {
          try {
            new Notification("Lecture Transcriber", { body: msg, requireInteraction: true });
          } catch (e) { /* in-page banner already covers it */ }
        }
      }
    } else if (rec.state === "finalizing") {
      statusEl.textContent = `Finishing — transcribing remaining audio ` +
        `(${rec.chunks_transcribed}/${rec.chunks_emitted}). Keep the lid open a few more seconds.`;
      startBtn.disabled = true;
      stopBtn.disabled = true;
    } else if (rec.state === "done") {
      if (data.active_transcript_filename && data.active_transcript_filename !== rec.output_filename) {
        statusEl.textContent = `Viewing "${data.lecture_name}" (transcripts\\${data.active_transcript_filename}).`;
      } else if (rec.auto_stopped) {
        statusEl.textContent = `Auto-stopped at 90 minutes. Saved to transcripts\\${rec.output_filename} — safe to close.`;
      } else {
        statusEl.textContent = `Saved to transcripts\\${rec.output_filename} — safe to close.`;
      }
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
      loadSidebar();   // a new transcript just appeared
    }
    lastRecorderState = rec ? rec.state : null;

    applyNotesState(data);
    highlightSidebar(data.active_transcript_filename);

    // Don't clobber the field while the user is actively typing in it.
    if (document.activeElement !== lectureNameInput) {
      lectureNameInput.value = data.lecture_name || "";
    }
  }

  // Notes generation runs server-side and survives a browser refresh --
  // this restores the UI to match whatever the server is actually doing,
  // whether this tab started the generation or a previous one did.
  function applyNotesState(data) {
    const status = data.notes_status;
    const resultPath = data.notes_result ? data.notes_result.notes_path : null;
    const changed = status !== lastNotesStatus || resultPath !== lastNotesResultPath;
    lastNotesStatus = status;
    lastNotesResultPath = resultPath;

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
        loadSidebar();   // that session now has notes -- refresh its badge
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
    stopReminderShown = false;
    hideNotice();
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission();   // so the 50-minute reminder can reach you in another window
    }
    const res = await fetch("/api/start", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error);
      startBtn.disabled = false;
    } else {
      if (data.lecture_mode_warning) showError(data.lecture_mode_warning);
      refreshLectureMode();
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    hideNotice();
    const res = await fetch("/api/stop", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error);
    } else {
      if (data.lecture_mode_warning) showError(data.lecture_mode_warning);
      refreshLectureMode();
    }
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

  async function sendLectureName() {
    try {
      await fetch("/api/lecture_name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: lectureNameInput.value }),
      });
    } catch (e) { /* next poll/blur retries */ }
  }
  lectureNameInput.addEventListener("blur", sendLectureName);
  lectureNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); lectureNameInput.blur(); }
  });

  // ---- past sessions sidebar ----
  function fmtWhen(epochSeconds) {
    const d = new Date(epochSeconds * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function renderSidebar(items) {
    sidebarList.innerHTML = "";
    if (items.length === 0) {
      sidebarList.innerHTML = '<div class="sidebar-empty">No past sessions yet.</div>';
      return;
    }
    for (const item of items) {
      const btn = document.createElement("button");
      btn.className = "sidebar-item";
      btn.dataset.filename = item.filename;
      const notesBadge = item.has_notes ? " ✓ notes" : "";
      btn.innerHTML =
        `<span class="name">${item.lecture_name}</span>` +
        `<span class="meta">${fmtWhen(item.modified)}${notesBadge}</span>`;
      btn.addEventListener("click", () => selectTranscript(item.filename));
      sidebarList.appendChild(btn);
    }
  }

  async function loadSidebar() {
    try {
      const res = await fetch("/api/transcripts");
      const data = await res.json();
      renderSidebar(data.items || []);
      // highlightSidebar() runs on the next poll tick and re-marks the active item
    } catch (e) { /* sidebar just stays as-is */ }
  }

  function highlightSidebar(activeFilename) {
    for (const el of sidebarList.querySelectorAll(".sidebar-item")) {
      el.classList.toggle("active", el.dataset.filename === activeFilename);
    }
  }

  async function selectTranscript(filename) {
    try {
      const res = await fetch("/api/select_transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
      });
      const data = await res.json();
      if (!data.ok) { showError(data.error); return; }
      transcriptBox.value = data.transcript_text;
      transcriptBox.scrollTop = 0;
      notesBox.value = "";
      latestDocument = null;
      const stateRes = await fetch("/api/state");
      applyState(await stateRes.json());
    } catch (e) {
      showError("Failed to load that transcript: " + e);
    }
  }

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

  // ---- lecture mode (lid-close sleep behavior) ----
  function renderLectureMode(on) {
    lectureModePill.textContent = on ? "Lecture Mode: ON" : "Lecture Mode: OFF";
    lectureModePill.classList.toggle("on", on);
    lectureModeToggle.textContent = on ? "Turn off" : "Turn on";
    lectureModeToggle.disabled = false;
    lectureModeToggle.dataset.on = on ? "1" : "0";
  }

  async function refreshLectureMode() {
    try {
      const res = await fetch("/api/lecture_mode");
      const data = await res.json();
      renderLectureMode(data.on);
    } catch (e) { /* leave last known state showing */ }
  }

  lectureModeToggle.addEventListener("click", async () => {
    const turnOn = lectureModeToggle.dataset.on !== "1";
    lectureModeToggle.disabled = true;
    lectureModeToggle.textContent = "…";
    try {
      const res = await fetch("/api/lecture_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: turnOn }),
      });
      const data = await res.json();
      if (!data.ok) {
        showError(data.error);
        refreshLectureMode();
        return;
      }
      renderLectureMode(data.on);
    } catch (e) {
      showError("Failed to change lid-close setting: " + e);
      refreshLectureMode();
    }
  });

  // ---- settings ----
  settingsToggle.addEventListener("click", () => {
    settingsPanel.classList.toggle("hidden");
  });

  async function loadSettings() {
    const res = await fetch("/api/settings");
    const s = await res.json();
    document.getElementById("apiKey").value = s.anthropic_api_key || "";
    document.getElementById("authorName").value = s.author_name || "";
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
      author_name: document.getElementById("authorName").value,
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
  loadSidebar();
  refreshLectureMode();
  setInterval(refreshLectureMode, 5000);   // catches changes from the desktop shortcuts too
  poll();
})();
