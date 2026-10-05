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
# Single image -> input_cutout.png next to it
python cutout.py photo.jpg

# Choose the output path and format
python cutout.py photo.jpg -o subject.png
python cutout.py photo.jpg -o subject.webp -f webp

# Batch: every image in a folder -> ./photos/cutouts/
python cutout.py --batch ./photos
python cutout.py --batch ./photos -o ./out -f webp
```

### Web UI

```bash
python server.py        # open http://127.0.0.1:8000
```

Drag-drop (or click / paste) an image. You get a before/after preview on a
checkerboard so the transparency is visible, and a Download button. Switch
between PNG and WebP with the dropdown.

## How it works

The whole engine is `make_cutout(image_bytes) -> png/webp bytes` in
[`cutout.py`](cutout.py). The pipeline, in order:

1. **Fix EXIF orientation** — phone photos carry rotation; apply it first.
2. **Downscale before removal** — the single biggest speedup. Runs the model on
   ~1 MP, not a 12 MP phone photo.
3. **Remove the background** with a warmed `rembg` session → RGBA.
4. **Downscale the result** to the final output size.
5. **Trim transparent margins** so the subject fills the frame.
6. **Encode** as PNG (lossless alpha) or WebP (`quality=82, method=6`).
7. **Fail soft** — a bad file or a missing model never crashes the app.

## Configuration

Edit the tunables at the top of `cutout.py`:

| Constant      | Default    | Meaning                                                |
| ------------- | ---------- | ------------------------------------------------------ |
| `MAX_IN_DIM`  | `1600`     | Downscale input to this max dimension before removal.  |
| `MAX_OUT_DIM` | `1024`     | Final cutout max dimension.                            |
| `MODEL`       | `u2netp`   | rembg model. `isnet-general-use` is cleaner but larger.|

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
  knocking a flat background out from behind a logo. (See section 6 of the
  original handoff for that separate colour-key approach.)
