# Cutout

Drop in a photo, get back the **main subject** on a **transparent background**,
saved as a PNG (or WebP). Runs entirely offline — no external API calls, nothing
leaves your machine.

It uses [`rembg`](https://github.com/danielgatis/rembg) (self-hosted, CPU, ONNX)
for background removal and [Pillow](https://python-pillow.org/) for image I/O.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### CLI

```bash
# Single image -> input_cutout.png next to it (auto-detects photo vs logo)
python cutout.py photo.jpg

# Choose the output path and format
python cutout.py photo.jpg -o subject.png
python cutout.py photo.jpg -o subject.webp -f webp

# Pick a model and refine hair edges (subject mode)
python cutout.py photo.jpg --mode subject --model portrait --alpha-matting

# Knock out a flat background behind a logo
python cutout.py logo.png --mode logo

# Batch: every image in a folder -> ./photos/cutouts/
python cutout.py --batch ./photos
python cutout.py --batch ./photos -o ./out -f webp
```

**Modes** (`--mode`): `auto` (default — local heuristic picks subject vs logo,
no network), `subject` (ML removal), `logo` (flat-background colour-key).

**Models** (`--model`, subject mode): `fast` (u2netp, ~4 MB, weakest edges),
`portrait` (u2net_human_seg, best for people), `general` (isnet-general-use,
**default**, strong all-round), `best` (birefnet, state-of-the-art edges but
~930 MB and slow on CPU). `--alpha-matting` refines hair/soft edges (slower).

### Web UI

```bash
python server.py        # open http://127.0.0.1:8000
```

Drag-drop (or click / paste) an image. You get a before/after preview on a
checkerboard so the transparency is visible, and a Download button. The controls
let you pick the **mode** (Auto / Subject / Logo), the **quality model** and
**alpha matting** (subject mode), **keep interior holes** (logo mode), and PNG
vs WebP. In Auto mode the status line shows which mode actually ran.

## Desktop app (drop window on the taskbar / Dock)

You don't have to touch the command line to use the GUI. The same drag-drop
window can run as a one-click desktop app.

**Prerequisite:** Python 3.11+ installed on the machine. The launcher does the
rest — on first run it creates its own virtual environment under `~/.cutout/`,
installs dependencies, starts the server, and opens the drop window in your
browser. (First launch takes a minute; later launches are instant.)

### Run it now (from the cloned folder)

- **macOS:** double-click **`Cutout.command`** in Finder. It opens in Terminal;
  leave that window open while you work, close it to quit.
- **Windows:** double-click **`Cutout.bat`**. Leave the console window open; close
  it to quit.

Behind the scenes this runs `python launch.py`, which you can also call directly:

```bash
python launch.py            # start (or focus an already-running instance)
python launch.py --stop     # stop a running instance
python launch.py --no-browser
```

### Make it pinnable (taskbar / Dock)

Build a packaged launcher with an app icon that you can pin.

- **macOS** → a `Cutout.app` bundle:
  ```bash
  scripts/make_macos_app.sh        # writes dist/Cutout.app
  ```
  Drag `dist/Cutout.app` to `/Applications`, open it once, then right-click its
  Dock icon → **Options → Keep in Dock**.

- **Windows** → a launcher folder with a pinnable shortcut:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\make_windows_launcher.ps1
  ```
  In `dist\Cutout\`, right-click **`Cutout.lnk`** → **Pin to taskbar** (or Pin to
  Start). `Cutout.vbs` starts it with no console window once you're past first
  run.

These packaged apps still use the system Python (they're launchers, not
self-contained binaries), so Python 3.11+ must be installed.

### Build both automatically (CI)

The [`Build desktop launchers`](.github/workflows/build.yml) GitHub Actions
workflow builds the macOS `.app` and the Windows launcher on real runners and
uploads them as downloadable artifacts — no local dev setup needed. Run it from
the **Actions** tab (**Run workflow**), or push a tag like `v1.0.0`. Download the
`Cutout-macos` / `Cutout-windows` artifacts from the run.

> Want a truly self-contained binary (no Python needed on the target machine)?
> That's a PyInstaller build — ask and it can be added as a second packaging path.

### Optional: app icon

Drop `assets/Cutout.icns` (macOS) and/or `assets/Cutout.ico` (Windows) into the
repo and the build scripts will use them automatically.

## How it works

There are two independent engines, picked by mode.

**Subject mode** — `make_cutout(image_bytes) -> png/webp bytes` in
[`cutout.py`](cutout.py), the ML path:

1. **Fix EXIF orientation** — phone photos carry rotation; apply it first.
2. **Downscale before removal** — the single biggest speedup. Runs the model on
   ~1 MP, not a 12 MP phone photo.
3. **Remove the background** with a warmed `rembg` session → RGBA (optionally
   with alpha matting for cleaner hair).
4. **Downscale the result** to the final output size.
5. **Trim transparent margins** so the subject fills the frame.
6. **Encode** as PNG (lossless alpha) or WebP (`quality=82, method=6`).
7. **Fail soft** — a bad file or a missing model never crashes the app.

**Logo mode** — `make_logo_cutout(...)` in [`logo.py`](logo.py), a non-ML
colour-key method for flat-background graphics:

1. Detect the background colour from the four corners.
2. **Flood-fill inward from the edges** so enclosed art survives.
3. Build a **graduated alpha matte** so near-background pixels fade (kills the
   anti-aliased halo around thin text).
4. **De-fringe** semi-transparent edges to remove the pale background tint.
   `keep_interior` preserves enclosed background-coloured regions; by default
   they're cleared (transparent letter counters / ring centres).

**Auto mode** uses a local heuristic (corner-colour spread + colour complexity)
to choose subject vs logo — no network, no API calls.

## Configuration

Edit the tunables at the top of `cutout.py`:

| Constant        | Default     | Meaning                                              |
| --------------- | ----------- | --------------------------------------------------- |
| `MAX_IN_DIM`    | `1600`      | Downscale input to this max dimension before removal.|
| `MAX_OUT_DIM`   | `1024`      | Final cutout max dimension.                          |
| `DEFAULT_MODEL` | `general`   | Default subject model (friendly name in `MODELS`).   |

## Offline / sandboxed environments

On first use `rembg` downloads its model (a few MB) to its cache — depending on
the `rembg` version this is `~/.u2net/` or `~/.rembg/models/`. If your
environment blocks the internet, pre-place the `.onnx` file there (or point
`U2NET_HOME` at a folder that contains it) and ship it with the app. Once the
model is cached, everything runs with no network access.

## Notes

- **PNG is the default deliverable** (lossless alpha). WebP is offered as a
  smaller alternative, but some corporate tools still choke on WebP.
- The model is **warmed at startup** (web) or before the first image (CLI) so
  the first request isn't slow; the session is reused for every image after.
- `rembg` picks *one main subject* — it's the right tool for photos, not for
  knocking a flat background out from behind a logo. Use **logo mode** for that
  (the non-ML colour-key path in `logo.py`).
- The larger models download on first use: `portrait`/`general` ~170 MB each,
  `best` ~930 MB. `fast` (u2netp) is only ~4 MB. Alpha matting needs `pymatting`
  (in `requirements.txt`).
