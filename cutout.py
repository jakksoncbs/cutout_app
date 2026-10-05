"""Cutout engine — turn an image into a transparent PNG of its main subject.

The core is :func:`make_cutout`, which takes raw image bytes and returns PNG
(or WebP) bytes with the background removed. Everything else in the app is UI
around this one function.

The pipeline follows the proven recipe:

1. Fix EXIF orientation (phone photos carry rotation).
2. Downscale BEFORE removal (the single biggest speedup).
3. Remove the background with a warmed ``rembg`` session -> RGBA.
4. Downscale the result to the final output size.
5. Trim fully-transparent margins so the subject fills the frame.
6. Encode as PNG (lossless alpha) or WebP.
7. Fail soft — never crash on a bad input or a missing model.

Can also be used directly as a CLI::

    python cutout.py input.jpg -o output.png
    python cutout.py --batch ./photos -o ./cutouts
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# --- Tunables -------------------------------------------------------------

MAX_IN_DIM = 1600  # downscale input before removal (speed lever #1)
MAX_OUT_DIM = 1024  # final cutout max dimension
MODEL = "u2netp"  # small + fast; "isnet-general-use" is cleaner but larger

# Image extensions we attempt to process in batch mode.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


# --- rembg session (lazy, warmed, reused) ---------------------------------

_session = None


class CutoutError(Exception):
    """Raised when a cutout cannot be produced and the caller wants detail."""


def _get_session():
    """Return a cached rembg session, creating it on first use.

    The first ``new_session`` plus first inference is slow (it loads/caches the
    ONNX model). Holding one global session and reusing it keeps every later
    request fast. It is safe to share a single session across a single-worker
    server.
    """
    global _session
    if _session is None:
        from rembg import new_session  # imported lazily so the CLI import is cheap

        _session = new_session(MODEL)
    return _session


def warm() -> None:
    """Load the model once at startup so the first real request isn't slow."""
    _get_session()


# --- pipeline helpers -----------------------------------------------------


def _trim_alpha(img: Image.Image, pad_frac: float = 0.02) -> Image.Image:
    """Crop away fully-transparent margins so the subject fills the frame.

    A few pixels of padding (``pad_frac`` of the largest dimension) are kept so
    the subject doesn't butt right against the edge.
    """
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.where(a > 10)
    if len(xs) == 0:
        # Nothing opaque — the whole image is transparent; leave it untouched.
        return img
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
    fmt = fmt.lower()
    if fmt == "webp":
        img.save(buf, format="WEBP", quality=82, method=6)
    else:
        img.save(buf, format="PNG")  # lossless, crisp alpha
    return buf.getvalue()


# --- the engine -----------------------------------------------------------


def cutout_image(
    src: Image.Image,
    *,
    max_in_dim: int = MAX_IN_DIM,
    max_out_dim: int = MAX_OUT_DIM,
    trim: bool = True,
) -> Image.Image:
    """Core transform: a PIL image in, a transparent RGBA cutout out.

    Raises on failure (no ``rembg``, bad model). Use :func:`make_cutout` for the
    fail-soft bytes-in/bytes-out wrapper.
    """
    from rembg import remove

    src = ImageOps.exif_transpose(src).convert("RGB")  # 1. orientation
    src.thumbnail((max_in_dim, max_in_dim))  # 2. downscale in (speed)
    out = remove(src, session=_get_session()).convert("RGBA")  # 3. remove bg
    out.thumbnail((max_out_dim, max_out_dim))  # 4. downscale out
    if trim:
        out = _trim_alpha(out)  # 5. trim transparent margins
    return out


def make_cutout(
    image_bytes: bytes,
    *,
    fmt: str = "png",
    strict: bool = False,
) -> bytes | None:
    """Input image bytes -> transparent cutout bytes (subject only).

    Returns ``None`` on any failure (fail soft) so a web server can surface a
    clean error instead of a 500. Set ``strict=True`` to raise
    :class:`CutoutError` with a reason instead of returning ``None`` — useful
    for a CLI that wants to tell the user what went wrong.

    ``fmt`` is ``"png"`` (default, lossless alpha) or ``"webp"`` (smaller).
    """
    if not image_bytes:
        if strict:
            raise CutoutError("empty input")
        return None
    try:
        src = Image.open(io.BytesIO(image_bytes))
        out = cutout_image(src)
        return _encode(out, fmt)  # 6. encode
    except ImportError as exc:  # rembg / onnxruntime not installed
        if strict:
            raise CutoutError(
                "rembg is not installed — run 'pip install -r requirements.txt'"
            ) from exc
        return None
    except Exception as exc:  # 7. fail soft on anything else
        if strict:
            raise CutoutError(f"could not process image: {exc}") from exc
        return None


# --- CLI ------------------------------------------------------------------


def _default_out_path(in_path: Path, fmt: str) -> Path:
    return in_path.with_name(f"{in_path.stem}_cutout.{fmt}")


def _process_one(in_path: Path, out_path: Path, fmt: str) -> bool:
    try:
        data = in_path.read_bytes()
    except OSError as exc:
        print(f"  ! cannot read {in_path}: {exc}", file=sys.stderr)
        return False
    try:
        result = make_cutout(data, fmt=fmt, strict=True)
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


def _run_batch(in_dir: Path, out_dir: Path | None, fmt: str) -> int:
    out_dir = out_dir or (in_dir / "cutouts")
    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        print(f"No images found in {in_dir}", file=sys.stderr)
        return 1
    print(f"Warming model ({MODEL})...")
    warm()
    print(f"Processing {len(images)} image(s) -> {out_dir}")
    ok = 0
    for img in images:
        if _process_one(img, out_dir / _default_out_path(img, fmt).name, fmt):
            ok += 1
    print(f"Done: {ok}/{len(images)} succeeded.")
    return 0 if ok else 1


def _run_single(in_path: Path, out_path: Path | None, fmt: str) -> int:
    if not in_path.is_file():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1
    out_path = out_path or _default_out_path(in_path, fmt)
    print(f"Warming model ({MODEL})...")
    warm()
    return 0 if _process_one(in_path, out_path, fmt) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cut out the main subject of an image and save a transparent PNG.",
    )
    parser.add_argument("input", nargs="?", help="input image file (single mode)")
    parser.add_argument("-o", "--output", help="output file (single) or directory (batch)")
    parser.add_argument("--batch", metavar="DIR", help="process every image in DIR")
    parser.add_argument(
        "-f",
        "--format",
        choices=("png", "webp"),
        default="png",
        help="output format (default: png)",
    )
    args = parser.parse_args(argv)

    if args.batch:
        return _run_batch(
            Path(args.batch),
            Path(args.output) if args.output else None,
            args.format,
        )
    if not args.input:
        parser.error("provide an input image, or use --batch DIR")
    return _run_single(
        Path(args.input),
        Path(args.output) if args.output else None,
        args.format,
    )


if __name__ == "__main__":
    raise SystemExit(main())
