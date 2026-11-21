/* Narrate - client */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  job: null, deck: null, narration: null, audit: [],
  voices: [], models: [], voiceId: null, voiceName: null,
  elKey: "", connected: false,
  captions: "srt", zoom: "0.035", mode: "basic", step: 1,
  provider: "eleven", mai: { cat: null, ok: false, endpoint: "" },
};

/* When the UI is hosted apart from the renderer (e.g. static on Vercel, backend
   in a container), set window.SLIDETOVIDEO_API_BASE in config.js. Empty means
   same-origin, which is how the local app runs. */
const API = String(window.SLIDETOVIDEO_API_BASE || "").replace(/\/+$/, "");
const TOKEN = String(window.SLIDETOVIDEO_TOKEN || "");
const u = p => API + p + (TOKEN ? (p.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : "");

const esc = t => String(t ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function withToken(opts = {}) {
  if (!TOKEN) return opts;
  return { ...opts, headers: { ...(opts.headers || {}), "x-app-token": TOKEN } };
}

async function api(url, opts = {}) {
  const r = await fetch(url.startsWith("/api") ? API + url : url, withToken(opts));
  const ct = r.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r;
  }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}
const notice = (el, msg, kind = "err") => {
  el.innerHTML = msg ? `<div class="notice ${kind}">${esc(msg)}</div>` : "";
};
const fmtTime = s => {
  s = Math.round(s || 0);
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};
const fmtSize = b => b >= 1e9 ? (b / 1e9).toFixed(1) + " GB"
  : b >= 1e6 ? Math.round(b / 1e6) + " MB" : Math.round(b / 1e3) + " KB";
const words = t => (t || "").trim() ? t.trim().split(/\s+/).length : 0;
/* Narration runs long; a fixed-height box would hide the end of it. */
const autosize = ta => {
  ta.style.height = "auto";
  ta.style.height = Math.max(112, ta.scrollHeight + 2) + "px";
};
const wpmNow = () => +($("#wpm")?.value || 150);

/* ───────── steps ───────── */
function go(n) {
  S.step = n;
  [1, 2, 3, 4].forEach(i => $(`#p${i}`).classList.toggle("hidden", i !== n));
  $$("#steps .step").forEach(el => {
    const i = +el.dataset.step;
    el.classList.toggle("active", i === n);
    el.classList.toggle("done", i < n);
    el.classList.toggle("clickable", i < n);
  });
  // scrollHeight is 0 inside a display:none panel, so size textareas on reveal.
  $$(`#p${n} textarea`).forEach(autosize);
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$$("#steps .step").forEach(el => el.addEventListener("click", () => {
  const i = +el.dataset.step;
  if (i < S.step) go(i);
}));
$$("[data-goto]").forEach(b => b.addEventListener("click", () => go(+b.dataset.goto)));

/* ───────── health ───────── */
async function checkHealth() {
  try {
    const h = await api("/api/health");
    const missing = ["ffmpeg", "libreoffice", "pdftoppm"].filter(k => !h[k]);
    $("#healthDot").classList.toggle("bad", missing.length > 0);
    $("#healthText").textContent = missing.length
      ? `missing: ${missing.join(", ")}` : "all tools ready";
    $("#fmtSel").innerHTML = (h.output_formats || [])
      .map(f => `<option value="${f.id}">${esc(f.label)}</option>`).join("");
    $("#serverDown").style.display = "none";
    return true;
  } catch {
    $("#healthDot").classList.add("bad");
    $("#healthText").textContent = "renderer offline";
    $("#serverDown").style.display = "block";
    return false;
  }
}
checkHealth();
$("#retryBtn").addEventListener("click", async () => {
  const b = $("#retryBtn");
  b.disabled = true; b.innerHTML = `<span class="spin"></span>`;
  await checkHealth();
  b.disabled = false; b.textContent = "Retry";
});
// Launched from the dock before the server finished booting? Try again shortly.
window.addEventListener("online", checkHealth);

// Show the right way to start the renderer for whatever OS this is.
(() => {
  const ua = (navigator.userAgent || "");
  const cmd = /Windows/i.test(ua) ? "slidetovideo.bat"
            : /Mac OS X|Macintosh/i.test(ua) ? "./slidetovideo.command"
            : "python3 slidetovideo.py";
  const el = $("#startCmd");
  if (el) el.textContent = cmd;
})();

/* ───────── installable app ───────── */
let deferredInstall = null;
window.addEventListener("beforeinstallprompt", e => {
  e.preventDefault();
  deferredInstall = e;
  $("#installBtn").style.display = "inline-block";
});
$("#installBtn").addEventListener("click", async () => {
  if (!deferredInstall) return;
  $("#installBtn").style.display = "none";
  deferredInstall.prompt();
  await deferredInstall.userChoice;
  deferredInstall = null;
});
window.addEventListener("appinstalled", () => {
  $("#installBtn").style.display = "none";
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

/* Opening a .pptx from the OS, once installed as a file handler. */
if ("launchQueue" in window && "files" in LaunchParams.prototype) {
  window.launchQueue.setConsumer(async params => {
    if (!params.files || !params.files.length) return;
    try {
      const file = await params.files[0].getFile();
      if (await checkHealth()) upload(file);
    } catch { /* handle denied or file unreadable */ }
  });
}

/* ───────── 1 · upload ───────── */
const drop = $("#drop"), fileInput = $("#file");
$("#pickBtn").addEventListener("click", () => fileInput.click());
drop.addEventListener("click", e => { if (e.target === drop) fileInput.click(); });
fileInput.addEventListener("change", () => fileInput.files[0] && upload(fileInput.files[0]));
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0]; if (f) upload(f);
});

async function upload(file) {
  notice($("#uploadError"), "");
  $("#deckSummary").style.display = "none";
  $("#uploadProgress").style.display = "block";
  $("#upMsg").textContent = `Uploading ${file.name}…`;
  const fd = new FormData(); fd.append("file", file);
  try {
    const { job_id } = await api("/api/decks", { method: "POST", body: fd });
    S.job = job_id;
    await pollExtract();
  } catch (e) {
    $("#uploadProgress").style.display = "none";
    notice($("#uploadError"), e.message);
  }
}

async function pollExtract() {
  for (;;) {
    const j = await api(`/api/jobs/${S.job}`);
    $("#upMsg").textContent = j.message || "Working…";
    if (j.stage === "error") {
      $("#uploadProgress").style.display = "none";
      notice($("#uploadError"), j.error); return;
    }
    if (j.stage === "extracted") {
      $("#uploadProgress").style.display = "none";
      S.deck = j.deck; showDeck(j); return;
    }
    await new Promise(r => setTimeout(r, 700));
  }
}

function showDeck(j) {
  const d = S.deck, noNotes = d.slide_count - d.slides_with_notes;
  $("#deckSummary").style.display = "block";
  $("#deckSummary").innerHTML = `
    <div class="notice ${d.slides_with_notes ? "ok" : "err"}">
      <b>${esc(d.deck_title)}</b> — ${d.slide_count} slides,
      ${d.slides_with_notes} with speaker notes${noNotes ? `, ${noNotes} without (skipped)` : ""}.
      ${d.slides_with_notes ? "" : " There is nothing to narrate — add speaker notes and upload again."}
    </div>`;
  if (d.slides_with_notes) { buildSlides(); go(2); }
}

/* ───────── 2 · narration ───────── */
$$("#writerSeg button").forEach(b => b.addEventListener("click", () => {
  $$("#writerSeg button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); S.mode = b.dataset.mode;
  $("#llmKeyWrap").style.display = S.mode === "llm" ? "block" : "none";
}));

function buildSlides() {
  const nar = S.narration;
  $("#slides").innerHTML = S.deck.slides.map(s => {
    const w = nar ? (nar.slides.find(x => x.n === s.n) || {}) : {};
    const skip = nar ? !!w.skip : !s.notes || s.hidden;
    const text = w.narration || "";
    return `
    <div class="slide-card ${skip ? "skipped" : ""}" data-n="${s.n}">
      <div>
        <img class="thumb" src="${u(`/api/jobs/${S.job}/slides/${s.n}.png`)}" alt="Slide ${s.n}" loading="lazy">
        <div class="card-foot" style="margin-top:9px">
          <label style="display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--muted);cursor:pointer">
            <input type="checkbox" class="skipBox" ${skip ? "checked" : ""} style="width:auto"> Skip this slide
          </label>
        </div>
      </div>
      <div style="min-width:0">
        <div class="slide-meta">
          <span class="slide-n">${String(s.n).padStart(2, "0")}</span>
          <span class="slide-title">${esc(s.title || "Untitled")}</span>
        </div>
        ${s.notes ? `<div class="notes-peek"><b style="color:var(--faint)">NOTES</b>  ${esc(s.notes)}</div>`
                  : `<div class="notes-peek" style="color:var(--faint)">No speaker notes on this slide.</div>`}
        <textarea class="narr" placeholder="${s.notes ? "Draft the narration, or write it yourself…" : "No notes — write narration manually if you want this slide included."}">${esc(text)}</textarea>
        <div class="card-foot">
          <span class="count cnt"></span>
          <div class="flags" style="flex:1"></div>
        </div>
      </div>
    </div>`;
  }).join("");

  $$(".slide-card").forEach(card => {
    const ta = $(".narr", card), box = $(".skipBox", card);
    autosize(ta);
    ta.addEventListener("input", () => { autosize(ta); updateCounts(); queueSave(); });
    box.addEventListener("change", () => {
      card.classList.toggle("skipped", box.checked);
      updateCounts(); queueSave();
    });
  });
  updateCounts();
}

function collect() {
  return {
    deck_title: S.deck.deck_title,
    voice: null, intro: null,
    outro: (S.narration && S.narration.outro) || "Thanks for listening.",
    outro_title: "Thank you",
    slides: $$(".slide-card").map(c => ({
      n: +c.dataset.n,
      skip: $(".skipBox", c).checked,
      narration: $(".narr", c).value,
    })),
  };
}

function updateCounts() {
  let total = 0, active = 0;
  $$(".slide-card").forEach(c => {
    const w = words($(".narr", c).value);
    const skipped = $(".skipBox", c).checked;
    $(".cnt", c).textContent = w ? `${w} words · ~${Math.round(w / wpmNow() * 60)}s` : "empty";
    if (!skipped && w) { total += w; active++; }
  });
  const secs = total / wpmNow() * 60 + active * 1.6;
  $("#totalEstimate").textContent = total
    ? `${active} slides · ~${fmtTime(secs)}` : "nothing to narrate yet";
  $("#toVoice").disabled = total === 0;
}

let saveTimer = null;
function queueSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    S.narration = collect();
    try {
      const r = await api(`/api/jobs/${S.job}/narration`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ narration: S.narration }),
      });
      paintAudit(r.audit || []);
    } catch { /* keep typing; save retries on next edit */ }
  }, 650);
}

function paintAudit(list) {
  S.audit = list;
  $$(".slide-card").forEach(c => $(".flags", c).innerHTML = "");
  list.forEach(f => {
    const c = $(`.slide-card[data-n="${f.n}"]`);
    if (!c) return;
    $(".flags", c).insertAdjacentHTML("beforeend",
      `<div class="flag ${f.level === "info" ? "info" : ""}"><b>${f.level === "info" ? "Note" : "Check"}</b><span>${esc(f.message)}</span></div>`);
  });
  const n = list.filter(f => f.level === "warn").length;
  $("#draftStatus").textContent = list.length
    ? `${n} thing${n === 1 ? "" : "s"} worth checking` : "no issues found";
}

$("#draftBtn").addEventListener("click", async () => {
  const btn = $("#draftBtn");
  notice($("#narrationError"), "");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> Writing…`;
  try {
    const body = { mode: S.mode, tone: $("#tone").value };
    if (S.mode === "llm") body.anthropic_key = $("#anthropicKey").value;
    const r = await api(`/api/jobs/${S.job}/narration`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    S.narration = r.narration;
    buildSlides();
    paintAudit(r.audit || []);
  } catch (e) {
    notice($("#narrationError"), e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Draft narration";
  }
});

$("#auditBtn").addEventListener("click", async () => {
  S.narration = collect();
  const r = await api(`/api/jobs/${S.job}/narration`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ narration: S.narration }),
  });
  paintAudit(r.audit || []);
});

$("#toVoice").addEventListener("click", () => { queueSave(); go(3); });

/* ───────── 3 · voice ───────── */
$("#connectBtn").addEventListener("click", async () => {
  const btn = $("#connectBtn");
  notice($("#elError"), "");
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>`;
  try {
    const r = await api("/api/elevenlabs/connect", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ api_key: $("#elKey").value }),
    });
    S.elKey = $("#elKey").value; S.connected = true;
    S.voices = r.voices; S.models = r.models;
    $("#elPanel").style.display = "block";
    const q = r.quota || {};
    $("#elQuota").textContent = (q.remaining != null)
      ? `${q.remaining.toLocaleString()} characters left${q.tier ? ` · ${q.tier}` : ""}`
      : (q.tier ? q.tier : "");
    $("#modelSel").innerHTML = S.models
      .map(m => `<option value="${esc(m.model_id)}">${esc(m.name)}</option>`).join("");
    const pref = S.models.find(m => m.model_id === "eleven_multilingual_v2");
    if (pref) $("#modelSel").value = pref.model_id;
    showModelNote();
    paintVoices();
  } catch (e) {
    notice($("#elError"), e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Connect";
  }
});

function showModelNote() {
  const m = S.models.find(x => x.model_id === $("#modelSel").value);
  $("#modelNote").textContent = m && m.note ? m.note : "";
}
$("#modelSel").addEventListener("change", showModelNote);

function paintVoices() {
  const q = $("#voiceSearch").value.trim().toLowerCase();
  const list = S.voices.filter(v =>
    !q || (v.name || "").toLowerCase().includes(q) ||
    (v.labels || []).join(" ").toLowerCase().includes(q));
  $("#voiceList").innerHTML = list.length ? list.map(v => `
    <button class="voice ${v.voice_id === S.voiceId ? "on" : ""}" data-id="${esc(v.voice_id)}" data-name="${esc(v.name)}">
      <span class="vn">${esc(v.name)}</span>
      <span class="vl">${esc((v.labels || []).join(" · ") || v.category || "")}</span>
    </button>`).join("")
    : `<div class="hint">No voices match “${esc(q)}”.</div>`;
  $$("#voiceList .voice").forEach(b => b.addEventListener("click", () => {
    S.voiceId = b.dataset.id; S.voiceName = b.dataset.name; paintVoices();
  }));
}
$("#voiceSearch").addEventListener("input", paintVoices);

[["stability", "vStability", 2], ["similarity", "vSimilarity", 2],
 ["style", "vStyle", 2], ["speed", "vSpeed", 2]].forEach(([id, out, dp]) => {
  $("#" + id).addEventListener("input", e => {
    $("#" + out).textContent = (+e.target.value).toFixed(dp);
  });
});

const voiceSettings = () => ({
  stability: +$("#stability").value,
  similarity_boost: +$("#similarity").value,
  style: +$("#style").value,
  use_speaker_boost: $("#speakerBoost").checked,
  speed: +$("#speed").value,
});

/* ───────── provider switching ───────── */
$$("#providerSeg button").forEach(b => b.addEventListener("click", async () => {
  $$("#providerSeg button").forEach(x => x.classList.remove("on"));
  b.classList.add("on");
  S.provider = b.dataset.prov;
  $("#provEleven").style.display = S.provider === "eleven" ? "block" : "none";
  $("#provMai").style.display    = S.provider === "mai"    ? "block" : "none";
  $("#provLocal").style.display  = S.provider === "local"  ? "block" : "none";
  $("#previewBlock").style.display = S.provider === "local" ? "none" : "block";
  if (S.provider === "mai" && !S.mai.cat) await loadMaiCatalog();
}));

/* ───────── MAI-Voice ───────── */
async function loadMaiCatalog() {
  try {
    const c = await api("/api/maivoice/catalog");
    S.mai.cat = c;
    $("#maiRegions").innerHTML = c.regions.map(r => `<option value="${r}">`).join("");
    $("#maiModel").innerHTML = c.models
      .map(m => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
    $("#maiModel").value = c.default_model;
    $("#maiFmt").innerHTML = c.output_formats
      .map(f => `<option value="${esc(f.id)}">${esc(f.label)}</option>`).join("");
    $("#maiLocale").innerHTML = c.locales
      .map(l => `<option value="${esc(l.locale)}">${esc(l.locale)} · ${l.voices.length} ${l.voices.length === 1 ? "voice" : "voices"}</option>`).join("");
    $("#maiLocale").value = "en-US";
    paintMaiVoices();
    maiModelNote();
  } catch (e) {
    notice($("#maiError"), e.message);
  }
}

function paintMaiVoices() {
  const c = S.mai.cat; if (!c) return;
  const loc = $("#maiLocale").value;
  const entry = c.locales.find(l => l.locale === loc) || { voices: [] };
  $("#maiVoice").innerHTML = entry.voices
    .map(v => `<option value="${esc(loc)}-${esc(v)}">${esc(v)}</option>`).join("");
  if (loc === "en-US") $("#maiVoice").value = c.default_voice;
  paintMaiStyles();
  maiVoiceLabel();
}

function paintMaiStyles() {
  const c = S.mai.cat; if (!c) return;
  const v = $("#maiVoice").value;
  const list = c.styles[v];
  if (list && list.length) {
    $("#maiStyle").innerHTML = `<option value="">Default (none)</option>` +
      list.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    $("#maiStyleNote").textContent = `${list.length} styles documented for ${v}.`;
  } else {
    $("#maiStyle").innerHTML =
      `<option value="">Default (none)</option><option value="__free">Custom…</option>`;
    $("#maiStyleNote").textContent =
      "Styles differ per voice and Microsoft documents them only for some. " +
      "An unsupported style is rejected outright, so verify before rendering.";
  }
  $("#maiStyleFree").style.display = "none";
}

function maiStyleValue() {
  const v = $("#maiStyle").value;
  return v === "__free" ? ($("#maiStyleFree").value.trim() || null) : (v || null);
}

function maiVoiceLabel() {
  const v = $("#maiVoice").value || "en-US-Harper";
  $("#maiVoiceName").textContent = `${v}:${$("#maiModel").value || "MAI-Voice-2.1"}`;
}

function maiModelNote() {
  const m = (S.mai.cat && S.mai.cat.models || []).find(x => x.id === $("#maiModel").value);
  $("#maiModelNote").textContent = (m && m.note) || "";
}

function maiPayload() {
  return {
    api_key: $("#maiKey").value,
    endpoint: $("#maiEndpoint").value,
    model_id: $("#maiModel").value,
    voice_id: $("#maiVoice").value,
    style: maiStyleValue(),
    output_format: $("#maiFmt").value,
    use_prosody: $("#maiProsody").checked,
  };
}

$("#maiLocale").addEventListener("change", paintMaiVoices);
$("#maiVoice").addEventListener("change", () => { paintMaiStyles(); maiVoiceLabel(); });
$("#maiModel").addEventListener("change", () => { maiModelNote(); maiVoiceLabel(); S.mai.ok = false; });
$("#maiStyle").addEventListener("change", () => {
  $("#maiStyleFree").style.display = $("#maiStyle").value === "__free" ? "block" : "none";
});

$("#maiCheckBtn").addEventListener("click", async () => {
  const btn = $("#maiCheckBtn");
  notice($("#maiError"), "");
  $("#maiStatus").style.display = "none";
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>`;
  try {
    const r = await api("/api/maivoice/check", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(maiPayload()),
    });
    S.mai.ok = true; S.mai.endpoint = r.endpoint;
    $("#maiStatus").style.display = "block";
    $("#maiEndpointChip").textContent =
      `${r.model} · ${String(r.endpoint).replace(/^https:\/\//, "")}`;
  } catch (e) {
    S.mai.ok = false;
    notice($("#maiError"), e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Verify";
  }
});

$("#previewBtn").addEventListener("click", async () => {
  const btn = $("#previewBtn"), st = $("#previewStatus");
  let url, body;
  if (S.provider === "eleven") {
    if (!S.voiceId) { st.textContent = "Pick a voice first."; return; }
    url = "/api/elevenlabs/preview";
    body = { api_key: S.elKey, voice_id: S.voiceId,
             model_id: $("#modelSel").value, voice_settings: voiceSettings(),
             text: $("#previewText").value };
  } else {
    url = "/api/maivoice/preview";
    body = { ...maiPayload(), text: $("#previewText").value,
             rate_pct: $("#maiProsody").checked
               ? Math.max(-40, Math.min(40, Math.round((wpmNow() / 180 - 1) * 100)))
               : null };
  }
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Synthesizing…`;
  st.textContent = "";
  try {
    const r = await fetch(u(url), withToken({
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }));
    if (!r.ok) throw new Error((await r.json()).error || "Preview failed.");
    const a = $("#previewAudio");
    a.src = URL.createObjectURL(await r.blob());
    a.style.display = "block"; a.play().catch(() => {});
  } catch (e) {
    st.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "▶ Preview voice";
  }
});

$("#toRender").addEventListener("click", () => go(4));

/* ───────── resume an existing job from the URL ───────── */
async function resumeFromHash() {
  const m = /job=([a-f0-9]+)/.exec(location.hash || "");
  if (!m) return;
  try {
    const j = await api(`/api/jobs/${m[1]}`);
    if (!j.deck) return;
    S.job = m[1]; S.deck = j.deck;
    $("#deckSummary").style.display = "block";
    $("#deckSummary").innerHTML =
      `<div class="notice ok"><b>${esc(j.deck.deck_title)}</b> — resumed session.</div>`;
    try {
      const r = await api(`/api/jobs/${S.job}/narration`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "basic" }),
      });
      S.narration = r.narration;
      buildSlides();
      paintAudit(r.audit || []);
    } catch { buildSlides(); }
    const st = /step=(\d)/.exec(location.hash);
    go(st ? +st[1] : 2);
  } catch { /* stale link: stay on upload */ }
}
window.addEventListener("DOMContentLoaded", resumeFromHash);

/* ───────── 4 · render ───────── */
$$("#capSeg button").forEach(b => b.addEventListener("click", () => {
  $$("#capSeg button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); S.captions = b.dataset.cap;
}));
$$("#zoomSeg button").forEach(b => b.addEventListener("click", () => {
  $$("#zoomSeg button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); S.zoom = b.dataset.zoom;
}));
$("#wpm").addEventListener("input", e => {
  $("#vWpm").textContent = e.target.value; updateCounts();
});
$("#accentPick").addEventListener("input", e => $("#accent").value = e.target.value);
$("#againBtn").addEventListener("click", () => {
  $("#result").style.display = "none"; $("#renderBtn").disabled = false;
});

$("#renderBtn").addEventListener("click", async () => {
  const btn = $("#renderBtn");
  notice($("#renderError"), "");
  if (S.provider === "eleven" && S.connected && !S.voiceId) {
    notice($("#renderError"), "Pick an ElevenLabs voice, or switch provider.");
    return;
  }
  if (S.provider === "mai" && !$("#maiKey").value.trim()) {
    notice($("#renderError"), "Paste a MAI-Voice key and endpoint, or switch provider.");
    return;
  }
  S.narration = collect();
  await api(`/api/jobs/${S.job}/narration`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ narration: S.narration }),
  });

  const engine = S.provider === "mai" ? "mai"
    : (S.provider === "eleven" && S.connected) ? "eleven"
    : ($("#engineSel").value || "auto");
  const opt = {
    engine, wpm: wpmNow(), captions: S.captions, zoom: +S.zoom,
    accent: $("#accent").value.trim() || null,
  };
  if (engine === "eleven") Object.assign(opt, {
    api_key: S.elKey, voice_id: S.voiceId, voice_name: S.voiceName,
    model_id: $("#modelSel").value, voice_settings: voiceSettings(),
    output_format: $("#fmtSel").value,
  });
  if (engine === "mai") Object.assign(opt, maiPayload());

  btn.disabled = true;
  $("#result").style.display = "none";
  $("#renderProgress").style.display = "block";
  $("#rBar").style.width = "0%"; $("#rPct").textContent = "0%";
  $("#rMsg").textContent = "Starting…";
  try {
    await api(`/api/jobs/${S.job}/render`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(opt),
    });
    await pollRender();
  } catch (e) {
    notice($("#renderError"), e.message);
    $("#renderProgress").style.display = "none";
    btn.disabled = false;
  }
});

async function pollRender() {
  for (;;) {
    const j = await api(`/api/jobs/${S.job}`);
    if (j.stage === "error") {
      notice($("#renderError"), j.error);
      $("#renderProgress").style.display = "none";
      $("#renderBtn").disabled = false; return;
    }
    const pct = Math.round((j.progress || 0) * 100);
    $("#rBar").style.width = pct + "%";
    $("#rPct").textContent = pct + "%";
    $("#rMsg").textContent = j.message || j.stage;
    if (j.stage === "done") {
      $("#renderProgress").style.display = "none";
      $("#result").style.display = "block";
      const v = $("#resultVideo");
      v.src = u(`/api/jobs/${S.job}/video?t=${Date.now()}`);
      $("#dlVideo").href = u(`/api/jobs/${S.job}/video`);
      $("#dlSrt").href = u(`/api/jobs/${S.job}/captions.srt`);
      $("#sDur").textContent = fmtTime(j.duration);
      $("#sSize").textContent = fmtSize(j.video_bytes || 0);
      $("#sVoice").textContent =
        S.provider === "mai" ? ($("#maiVoice").value || "MAI-Voice")
        : (S.provider === "eleven" && S.connected) ? (S.voiceName || "ElevenLabs")
        : "local engine";
      $("#renderBtn").disabled = false;
      return;
    }
    await new Promise(r => setTimeout(r, 800));
  }
}
