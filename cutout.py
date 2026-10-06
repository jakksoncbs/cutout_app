"""Cutout engine — turn an image into a transparent PNG of its main subject.

Two modes:

* **subject** (default) — ML background removal with ``rembg``. Good for photos:
  people, products, animals. Pick a model by quality/speed and optionally turn
  on alpha matting for cleaner hair/soft edges.
* **logo** — a non-ML colour-key method for flat-background raster logos and
  graphics (see :mod:`logo`). ``rembg`` is wrong for logos (it keeps one
  object); the colour-key method is wrong for photos.

The subject pipeline follows the proven recipe:

1. Fix EXIF orientation (phone photos carry rotation).
2. Downscale BEFORE removal (the single biggest speedup).
3. Remove the background with a warmed ``rembg`` session -> RGBA.
4. Downscale the result to the final output size.
5. Trim fully-transparent margins so the subject fills the frame.
6. Encode as PNG (lossless alpha) or WebP.
7. Fail soft — never crash on a bad input or a missing model.

Everything runs locally — no external API calls.

CLI::

    python cutout.py input.jpg -o output.png
    python cutout.py input.jpg --model portrait --alpha-matting
    python cutout.py logo.png --mode logo
    python cutout.py --batch ./photos --mode auto
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

import logo as logo_mode

# --- Tunables -------------------------------------------------------------

MAX_IN_DIM = 1600  # downscale input before removal (speed lever #1)
MAX_OUT_DIM = 1024  # final cutout max dimension

# Friendly model names -> rembg model ids. Larger = cleaner but slower/heavier.
MODELS = {
    "fast": "u2netp",            # ~4 MB, sub-second; weakest edges
    "portrait": "u2net_human_seg",  # ~168 MB, people/portraits — great for headshots
    "general": "isnet-general-use",  # ~171 MB, strong all-round default
    "best": "birefnet-general",  # ~930 MB, state-of-the-art edges, slow on CPU
}
DEFAULT_MODEL = "general"  # clearly better than u2netp, works on people and objects

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


# --- rembg sessions (lazy, warmed, reused per model) ----------------------

_sessions: dict[str, object] = {}


class CutoutError(Exception):
    """Raised when a cutout cannot be produced and the caller wants detail."""


def _resolve_model(model: str) -> str:
    """Accept a friendly name ('general') or a raw rembg id ('isnet-general-use')."""
    return MODELS.get(model, model)


def _get_session(model: str = DEFAULT_MODEL):
    """Return a cached rembg session for ``model``, creating it on first use.

    One session per model is held and reused — the first load + first inference
    is slow, every later call is fast. Safe to share across a single-worker
    server.
    """
    rembg_id = _resolve_model(model)
    if rembg_id not in _sessions:
        from rembg import new_session  # imported lazily so the CLI import is cheap

        _sessions[rembg_id] = new_session(rembg_id)
    return _sessions[rembg_id]


def warm(model: str = DEFAULT_MODEL) -> None:
    """Load a model once at startup so the first real request isn't slow."""
    _get_session(model)


# --- pipeline helpers -----------------------------------------------------


def _trim_alpha(img: Image.Image, pad_frac: float = 0.02) -> Image.Image:
    """Crop away fully-transparent margins so the subject fills the frame."""
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.where(a > 10)
    if len(xs) == 0:
        return img  # fully transparent — leave untouched
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    pad = max(2, int(pad_frac * max(img.size)))
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(img.size[0] - 1, x1 + pad)
    y1 = min(img.size[1] - 1, y1 + pad)
    return img.crop((x0, y0, x1 + 1, y1 + 1))


def _encode(img: Image.Image, fmt: str) -> bytes:
    """Encode an RGBA image to PNG or WebP bytes."""
    buf = io.BytesIO()
    if fmt.lower() == "webp":
        img.save(buf, format="WEBP", quality=82, method=6)
    else:
        img.save(buf, format="PNG")  # lossless, crisp alpha
    return buf.getvalue()


# --- subject engine (rembg) -----------------------------------------------


def cutout_image(
    src: Image.Image,
    *,
    model: str = DEFAULT_MODEL,
    alpha_matting: bool = False,
    max_in_dim: int = MAX_IN_DIM,
    max_out_dim: int = MAX_OUT_DIM,
    trim: bool = True,
) -> Image.Image:
    """Core subject transform: a PIL image in, a transparent RGBA cutout out.

    Raises on failure (no ``rembg``, bad model). Use :func:`make_cutout` for the
    fail-soft bytes-in/bytes-out wrapper.
    """
    from rembg import remove

    src = ImageOps.exif_transpose(src).convert("RGB")  # 1. orientation
    src.thumbnail((max_in_dim, max_in_dim))  # 2. downscale in (speed)

    kwargs: dict = {}
    if alpha_matting:
        # Refines soft/hair edges. Needs the optional `pymatting` dependency;
        # if it's missing, fall back to a plain removal rather than erroring.
        kwargs = dict(
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
    try:
        out = remove(src, session=_get_session(model), **kwargs).convert("RGBA")
    except Exception:
        if alpha_matting:
            out = remove(src, session=_get_session(model)).convert("RGBA")
        else:
            raise

    out.thumbnail((max_out_dim, max_out_dim))  # 4. downscale out
    if trim:
        out = _trim_alpha(out)  # 5. trim transparent margins
    return out


# --- public API -----------------------------------------------------------


def make_cutout(
    image_bytes: bytes,
    *,
    mode: str = "subject",
    model: str = DEFAULT_MODEL,
    alpha_matting: bool = False,
    fmt: str = "png",
    strict: bool = False,
) -> bytes | None:
    """Input image bytes -> transparent cutout bytes.

    ``mode``:
      * ``"subject"`` — ML removal (default), honours ``model`` / ``alpha_matting``.
      * ``"logo"``    — colour-key method for flat-background logos/graphics.
      * ``"auto"``    — pick subject vs logo with a local heuristic (no network).

    Returns ``None`` on any failure (fail soft). Set ``strict=True`` to raise
    :class:`CutoutError` with a reason instead. ``fmt`` is ``"png"`` or ``"webp"``.
    """
    if not image_bytes:
        if strict:
            raise CutoutError("empty input")
        return None

    if mode == "auto":
        mode = logo_mode.suggest_mode(image_bytes)

    if mode == "logo":
        return logo_mode.make_logo_cutout(image_bytes, fmt=fmt, strict=strict)

    try:
        src = Image.open(io.BytesIO(image_bytes))
        out = cutout_image(src, model=model, alpha_matting=alpha_matting)
        return _encode(out, fmt)
    except ImportError as exc:  # rembg / onnxruntime not installed
        if strict:
            raise CutoutError(
                "rembg is not installed — run 'pip install -r requirements.txt'"
            ) from exc
        return None
    except Exception as exc:  # fail soft on anything else
        if strict:
            raise CutoutError(f"could not process image: {exc}") from exc
        return None


# --- CLI ------------------------------------------------------------------


def _default_out_path(in_path: Path, fmt: str) -> Path:
    return in_path.with_name(f"{in_path.stem}_cutout.{fmt}")


def _process_one(in_path: Path, out_path: Path, fmt: str, **kw) -> bool:
    try:
        data = in_path.read_bytes()
    except OSError as exc:
        print(f"  ! cannot read {in_path}: {exc}", file=sys.stderr)
        return False
    try:
        result = make_cutout(data, fmt=fmt, strict=True, **kw)
    except CutoutError as exc:
        print(f"  ! {in_path.name}: {exc}", file=sys.stderr)
        return False
    if result is None:  # pragma: no cover - strict=True raises instead
        print(f"  ! {in_path.name}: cutout failed", file=sys.stderr)
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(result)
    print(f"  * {in_path.name} -> {out_path}")
    return True


def _run_batch(in_dir: Path, out_dir: Path | None, fmt: str, **kw) -> int:
    out_dir = out_dir or (in_dir / "cutouts")
    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        print(f"No images found in {in_dir}", file=sys.stderr)
        return 1
    if kw.get("mode") != "logo":
        print(f"Warming model ({_resolve_model(kw.get('model', DEFAULT_MODEL))})...")
        warm(kw.get("model", DEFAULT_MODEL))
    print(f"Processing {len(images)} image(s) -> {out_dir}")
    ok = sum(
        _process_one(img, out_dir / _default_out_path(img, fmt).name, fmt, **kw)
        for img in images
    )
    print(f"Done: {ok}/{len(images)} succeeded.")
    return 0 if ok else 1


def _run_single(in_path: Path, out_path: Path | None, fmt: str, **kw) -> int:
    if not in_path.is_file():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1
    out_path = out_path or _default_out_path(in_path, fmt)
    if kw.get("mode") != "logo":
        print(f"Warming model ({_resolve_model(kw.get('model', DEFAULT_MODEL))})...")
        warm(kw.get("model", DEFAULT_MODEL))
    return 0 if _process_one(in_path, out_path, fmt, **kw) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cut out an image's main subject (or knock out a flat logo "
        "background) and save a transparent PNG.",
    )
    parser.add_argument("input", nargs="?", help="input image file (single mode)")
    parser.add_argument("-o", "--output", help="output file (single) or directory (batch)")
    parser.add_argument("--batch", metavar="DIR", help="process every image in DIR")
    parser.add_argument(
        "-m", "--mode", choices=("auto", "subject", "logo"), default="auto",
        help="auto-detect (default), subject (ML), or logo (colour-key)",
    )
    parser.add_argument(
        "--model", choices=tuple(MODELS), default=DEFAULT_MODEL,
        help=f"subject model: {', '.join(MODELS)} (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--alpha-matting", action="store_true",
        help="refine hair/soft edges (subject mode; slower)",
    )
    parser.add_argument(
        "-f", "--format", choices=("png", "webp"), default="png",
        help="output format (default: png)",
    )
    args = parser.parse_args(argv)

    kw = dict(mode=args.mode, model=args.model, alpha_matting=args.alpha_matting)
    out = Path(args.output) if args.output else None

    if args.batch:
        return _run_batch(Path(args.batch), out, args.format, **kw)
    if not args.input:
        parser.error("provide an input image, or use --batch DIR")
    return _run_single(Path(args.input), out, args.format, **kw)


if __name__ == "__main__":
    raise SystemExit(main())
