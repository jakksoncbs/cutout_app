"""Local web UI for the Cutout app.

A tiny FastAPI server that serves a single drag-drop page and a ``/cutout``
endpoint. Everything stays on localhost — no data leaves the machine.

Run::

    python server.py              # http://127.0.0.1:8000
    uvicorn server:app --reload   # dev mode

The page shows a before/after preview on a checkerboard so transparency is
visible, and a Download button for the resulting PNG (or WebP).
"""

from __future__ import annotations

from fastapi import FastAPI, Response, UploadFile
from fastapi.responses import HTMLResponse

import cutout

app = FastAPI(title="Cutout")


@app.on_event("startup")
def _startup() -> None:
    # Warm the model so the first drop isn't slow. Fail soft — if rembg isn't
    # available yet the server still starts and /cutout returns a clear 422.
    try:
        cutout.warm()
    except Exception:  # noqa: BLE001 - never block startup on the model
        pass


@app.post("/cutout")
async def make_cutout(
    file: UploadFile,
    format: str = "png",
    mode: str = "auto",
    model: str = cutout.DEFAULT_MODEL,
    alpha_matting: bool = False,
    keep_interior: bool = False,
    despill: str = "off",
    despill_strength: float = 0.7,
) -> Response:
    fmt = "webp" if format.lower() == "webp" else "png"
    data = await file.read()
    resolved = mode
    if mode == "auto":
        import logo

        resolved = "chroma" if cutout._detect_screen(data) else logo.suggest_mode(data)
    if resolved == "logo":
        import logo

        result = logo.make_logo_cutout(data, fmt=fmt, keep_interior=keep_interior)
    else:
        result = cutout.make_cutout(
            data, mode=resolved, model=model, alpha_matting=alpha_matting,
            despill=despill, despill_strength=despill_strength, fmt=fmt,
        )
    if not result:
        return Response(
            "Cutout failed — the image could not be read, or the model is "
            "unavailable. Check the server logs.",
            status_code=422,
        )
    media = "image/webp" if fmt == "webp" else "image/png"
    # Tell the UI which mode actually ran (useful when mode=auto).
    return Response(result, media_type=media, headers={"X-Cutout-Mode": resolved})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Cutout — transparent PNG maker</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --border: #2a2f3a;
    --text: #e8eaed;
    --muted: #9aa0ad;
    --accent: #5b8cff;
    --accent-hi: #7aa2ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .wrap { max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  p.sub { color: var(--muted); margin: 0 0 28px; }

  #drop {
    border: 2px dashed var(--border);
    border-radius: 14px;
    padding: 44px 24px;
    text-align: center;
    color: var(--muted);
    background: var(--panel);
    transition: border-color .15s, background .15s, color .15s;
    cursor: pointer;
  }
  #drop.over { border-color: var(--accent); background: #1b2030; color: var(--text); }
  #drop strong { color: var(--text); }
  #drop .hint { font-size: 13px; margin-top: 8px; }

  .controls { display: flex; align-items: center; gap: 16px; margin: 18px 0 4px; flex-wrap: wrap; }
  label.fmt { color: var(--muted); font-size: 14px; display: inline-flex; gap: 6px; align-items: center; }
  label.fmt.chk { cursor: pointer; }
  label.fmt[hidden] { display: none; }
  input[type=range] { accent-color: var(--accent); width: 110px; vertical-align: middle; }
  #strengthVal { min-width: 34px; display: inline-block; text-align: right; }
  select {
    background: var(--panel); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 10px; font: inherit;
  }
  .status { color: var(--muted); font-size: 14px; min-height: 1.4em; }
  .status.err { color: #ff8c8c; }

  .grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 24px;
  }
  @media (max-width: 680px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; overflow: hidden; display: flex; flex-direction: column;
  }
  .card h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin: 0; padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .stage {
    flex: 1; min-height: 240px; display: grid; place-items: center; padding: 16px;
  }
  /* Checkerboard so transparency is visible. */
  .checker {
    background-color: #c9ccd3;
    background-image:
      linear-gradient(45deg, #9aa0ad 25%, transparent 25%),
      linear-gradient(-45deg, #9aa0ad 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, #9aa0ad 75%),
      linear-gradient(-45deg, transparent 75%, #9aa0ad 75%);
    background-size: 20px 20px;
    background-position: 0 0, 0 10px, 10px -10px, -10px 0;
  }
  .stage img { max-width: 100%; max-height: 420px; display: block; border-radius: 6px; }
  .stage .empty { color: var(--muted); font-size: 14px; }

  .btn {
    display: inline-block; margin-top: 18px; padding: 11px 20px;
    background: var(--accent); color: #fff; border: none; border-radius: 10px;
    font: inherit; font-weight: 600; cursor: pointer; text-decoration: none;
    transition: background .15s;
  }
  .btn:hover { background: var(--accent-hi); }
  .btn[disabled] { opacity: .45; cursor: not-allowed; }
  .footer { color: var(--muted); font-size: 13px; margin-top: 34px; }
  .spinner {
    width: 18px; height: 18px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    display: inline-block; vertical-align: -4px; margin-right: 8px;
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Cutout</h1>
  <p class="sub">Drop a photo — get the main subject back on a transparent background.</p>

  <div id="drop" tabindex="0" role="button" aria-label="Drop an image, or click to choose a file">
    <strong>Drop an image here</strong>, click to choose, or paste from the clipboard.
    <div class="hint">JPG, PNG, WebP — processed locally, nothing is uploaded off this machine.</div>
  </div>
  <input id="file" type="file" accept="image/*" hidden />

  <div class="controls">
    <label class="fmt">Mode
      <select id="mode">
        <option value="auto">Auto-detect</option>
        <option value="subject">Subject (photo)</option>
        <option value="logo">Logo / flat background</option>
        <option value="chroma">Green / blue screen</option>
      </select>
    </label>
    <label class="fmt" id="despillWrap">Spill
      <select id="despill">
        <option value="off">Off</option>
        <option value="auto">Auto</option>
        <option value="green">Green</option>
        <option value="blue">Blue</option>
      </select>
    </label>
    <label class="fmt" id="strengthWrap" hidden>Strength
      <input type="range" id="strength" min="0" max="100" value="80" />
      <span id="strengthVal">80%</span>
    </label>
    <label class="fmt" id="modelWrap">Quality
      <select id="model">
        <option value="fast">Fast (u2netp)</option>
        <option value="portrait">Portrait (people)</option>
        <option value="general" selected>General (recommended)</option>
        <option value="best">Best (slow)</option>
      </select>
    </label>
    <label class="fmt chk" id="amWrap"><input type="checkbox" id="am" /> Alpha matting (hair)</label>
    <label class="fmt chk" id="keepWrap" hidden><input type="checkbox" id="keep" /> Keep interior holes</label>
    <label class="fmt">Format
      <select id="fmt">
        <option value="png">PNG (lossless alpha)</option>
        <option value="webp">WebP (smaller)</option>
      </select>
    </label>
    <span id="status" class="status" aria-live="polite"></span>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Original</h2>
      <div class="stage" id="beforeStage"><span class="empty">No image yet</span></div>
    </div>
    <div class="card">
      <h2>Cutout</h2>
      <div class="stage checker" id="afterStage"><span class="empty">Result appears here</span></div>
    </div>
  </div>

  <a id="download" class="btn" download>Download cutout</a>

  <p class="footer">Powered by rembg + Pillow. Runs entirely on localhost.</p>
</div>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const fmtSel = document.getElementById('fmt');
const modeSel = document.getElementById('mode');
const modelSel = document.getElementById('model');
const amChk = document.getElementById('am');
const keepChk = document.getElementById('keep');
const despillSel = document.getElementById('despill');
const strengthSl = document.getElementById('strength');
const strengthVal = document.getElementById('strengthVal');
const modelWrap = document.getElementById('modelWrap');
const amWrap = document.getElementById('amWrap');
const keepWrap = document.getElementById('keepWrap');
const despillWrap = document.getElementById('despillWrap');
const strengthWrap = document.getElementById('strengthWrap');
const statusEl = document.getElementById('status');

function syncControls() {
  // Show the controls that matter for the chosen mode.
  const m = modeSel.value;
  const isLogo = m === 'logo';
  const isChroma = m === 'chroma';
  modelWrap.hidden = isLogo || isChroma;   // ML model only for subject/auto
  amWrap.hidden = isLogo || isChroma;
  keepWrap.hidden = !isLogo;               // interior holes only for logo
  despillWrap.hidden = isLogo;             // spill removal for subject/auto/chroma
  // Chroma always despills on the server; show it as "Auto" and disabled there.
  if (isChroma) { despillSel.value = 'auto'; despillSel.disabled = true; }
  else { despillSel.disabled = false; }
  // The strength slider matters whenever despill actually runs.
  strengthWrap.hidden = isLogo || (!isChroma && despillSel.value === 'off');
}
modeSel.addEventListener('change', () => { syncControls(); if (currentFile) process(currentFile); });
syncControls();
const beforeStage = document.getElementById('beforeStage');
const afterStage = document.getElementById('afterStage');
const downloadBtn = document.getElementById('download');

let currentFile = null;
let resultUrl = null;

downloadBtn.style.display = 'none';

function setStatus(msg, isErr) {
  statusEl.textContent = msg || '';
  statusEl.classList.toggle('err', !!isErr);
}

function showBefore(file) {
  const url = URL.createObjectURL(file);
  beforeStage.innerHTML = '';
  const img = new Image();
  img.src = url;
  img.alt = 'original image';
  beforeStage.appendChild(img);
}

async function process(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) { setStatus('That is not an image file.', true); return; }
  currentFile = file;
  showBefore(file);
  afterStage.innerHTML = '<span class="empty">Working…</span>';
  downloadBtn.style.display = 'none';
  setStatus('');
  const label = statusEl;
  label.innerHTML = '<span class="spinner"></span>Removing background…';

  const fmt = fmtSel.value;
  const params = new URLSearchParams({
    format: fmt,
    mode: modeSel.value,
    model: modelSel.value,
    alpha_matting: amChk.checked ? 'true' : 'false',
    keep_interior: keepChk.checked ? 'true' : 'false',
    despill: despillSel.value,
    despill_strength: (strengthSl.value / 100).toFixed(2),
  });
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch('/cutout?' + params.toString(), { method: 'POST', body: form });
    if (!resp.ok) {
      const text = await resp.text();
      afterStage.innerHTML = '<span class="empty">Failed</span>';
      setStatus(text || ('Error ' + resp.status), true);
      return;
    }
    const ranMode = resp.headers.get('X-Cutout-Mode') || modeSel.value;
    const blob = await resp.blob();
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    resultUrl = URL.createObjectURL(blob);

    afterStage.innerHTML = '';
    const img = new Image();
    img.src = resultUrl;
    img.alt = 'cutout result';
    afterStage.appendChild(img);

    const base = (file.name || 'image').replace(/\\.[^.]+$/, '');
    downloadBtn.href = resultUrl;
    downloadBtn.download = base + '_cutout.' + fmt;
    downloadBtn.style.display = 'inline-block';
    const modeNote = modeSel.value === 'auto' ? ' · auto → ' + ranMode : '';
    setStatus('Done — ' + (blob.size / 1024).toFixed(0) + ' KB' + modeNote + '. Click Download to save.');
  } catch (err) {
    afterStage.innerHTML = '<span class="empty">Failed</span>';
    setStatus('Network error: ' + err, true);
  }
}

// Reprocess when any option changes and we already have a file.
[fmtSel, modelSel, amChk, keepChk, despillSel, strengthSl].forEach(el =>
  el.addEventListener('change', () => { if (currentFile) process(currentFile); }));
// Toggling the spill colour shows/hides the strength slider.
despillSel.addEventListener('change', syncControls);
// Live % label while dragging (reprocess happens on release, via 'change').
strengthSl.addEventListener('input', () => { strengthVal.textContent = strengthSl.value + '%'; });

// Click / keyboard to open the picker.
drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener('change', () => process(fileInput.files[0]));

// Drag and drop.
['dragenter', 'dragover'].forEach(ev =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) process(f);
});

// Paste from clipboard.
window.addEventListener('paste', (e) => {
  const items = e.clipboardData && e.clipboardData.files;
  if (items && items.length) process(items[0]);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
