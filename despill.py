"""Green/blue spill removal and green-screen chroma keying.

There's no standard Python "despill" package — it's a short channel operation,
the same math the pro tools use (Nuke apDespill, After Effects Advanced Spill
Suppressor, OBS spill reduction). This module needs only NumPy + Pillow.

Two things live here:

* :func:`despill_rgba` — a post-process that removes green (or blue) colour
  contamination from a cutout's subject, applied only where it's opaque.
* :func:`make_chroma_cutout` — a dedicated green/blue-screen keyer: it keys *on*
  the screen colour (more accurate than ML removal when you actually shot
  against a screen), then despills and de-fringes the result.

Everything is offline — no external API calls.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

MAX_DIM = 1600


class DespillError(Exception):
    """Raised (in strict mode) when a chroma cutout cannot be produced."""


# --- spill detection ------------------------------------------------------


def _score(rgb: np.ndarray, color: str) -> np.ndarray:
    """How much a pixel exceeds the other two channels in the key colour.

    Positive = the pixel leans toward the screen colour (spill / background).
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if color == "blue":
        return b - np.maximum(r, g)
    return g - np.maximum(r, b)  # green (default)


def detect_spill(rgba: np.ndarray, *, min_frac: float = 0.02) -> str | None:
    """Guess whether a cutout carries green or blue spill, or neither.

    Looks at opaque pixels only. Returns 'green', 'blue', or None.
    """
    if rgba.shape[2] == 4:
        opaque = rgba[..., 3] > 32
        rgb = rgba[..., :3][opaque].astype(np.float64)
    else:
        rgb = rgba.reshape(-1, 3).astype(np.float64)
    if rgb.size == 0:
        return None
    green = np.clip(rgb[:, 1] - np.maximum(rgb[:, 0], rgb[:, 2]), 0, None)
    blue = np.clip(rgb[:, 2] - np.maximum(rgb[:, 0], rgb[:, 1]), 0, None)
    gfrac = float((green > 12).mean())
    bfrac = float((blue > 12).mean())
    if max(gfrac, bfrac) < min_frac:
        return None
    return "green" if gfrac >= bfrac else "blue"


def detect_screen(arr: np.ndarray) -> str | None:
    """Decide if the image was shot on a green/blue screen (strong, flat border)."""
    h, w = arr.shape[:2]
    b = max(2, int(min(h, w) * 0.05))
    ring = np.concatenate([
        arr[:b, :].reshape(-1, 3), arr[-b:, :].reshape(-1, 3),
        arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3),
    ]).astype(np.float64)
    for color in ("green", "blue"):
        s = _score(ring, color)
        # Most of the border is strongly the screen colour.
        if float((s > 40).mean()) > 0.80:
            return color
    return None


# --- despill --------------------------------------------------------------


def despill_array(rgb: np.ndarray, strength: float, color: str) -> np.ndarray:
    """Reduce green/blue spill in a float RGB array (in place-safe copy).

    ``strength`` 0..1: 0 = no change, 1 = clamp the key channel to the brighter
    of the other two (classic green limiting). Luminance is nudged back up by
    redistributing a little of the removed amount so colours don't go muddy.
    """
    rgb = rgb.astype(np.float64).copy()
    strength = float(np.clip(strength, 0.0, 1.0))
    idx = 2 if color == "blue" else 1
    other = [0, 1, 2]
    other.remove(idx)
    hi = np.maximum(rgb[..., other[0]], rgb[..., other[1]])
    lo = np.minimum(rgb[..., other[0]], rgb[..., other[1]])
    # Target the key channel shouldn't exceed. At low strength this is max(R,B)
    # (classic green-limiting); as strength climbs it drops toward min(R,B) so a
    # stubborn yellow-green cast actually clears at the top of the slider.
    target = hi - strength * 0.35 * (hi - lo)
    spill = np.clip(rgb[..., idx] - target, 0.0, None)
    remove = strength * spill
    rgb[..., idx] -= remove
    # Preserve luminance: add a small, even share of the removed light back to
    # the other two channels (keeps skin/edges from darkening into magenta).
    give = remove * 0.15
    rgb[..., other[0]] += give
    rgb[..., other[1]] += give
    return np.clip(rgb, 0, 255)


def despill_rgba(
    img: Image.Image, *, strength: float = 0.7, color: str = "auto"
) -> Image.Image:
    """Return a copy of an RGBA image with green/blue spill reduced.

    Only opaque/semi-opaque pixels are touched. ``color`` is 'auto', 'green',
    'blue', or 'off'.
    """
    if color == "off" or strength <= 0:
        return img
    rgba = np.asarray(img.convert("RGBA")).astype(np.float64)
    if color == "auto":
        color = detect_spill(rgba) or "green"
    rgb = rgba[..., :3]
    alpha = rgba[..., 3:4] / 255.0
    cleaned = despill_array(rgb, strength, color)
    # Blend by alpha so fully-transparent pixels are untouched.
    rgb[...] = rgb * (1 - alpha) + cleaned * alpha
    out = np.dstack([rgb, rgba[..., 3]]).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


# --- chroma key (green/blue screen) ---------------------------------------


def _trim_alpha(img: Image.Image, pad_frac: float = 0.02) -> Image.Image:
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.where(a > 10)
    if len(xs) == 0:
        return img
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    pad = max(2, int(pad_frac * max(img.size)))
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(img.size[0] - 1, x1 + pad)
    y1 = min(img.size[1] - 1, y1 + pad)
    return img.crop((x0, y0, x1 + 1, y1 + 1))


def chroma_key(
    img: Image.Image,
    *,
    color: str = "auto",
    tolerance: int = 25,
    softness: int = 45,
    despill_strength: float = 0.8,
    defringe: bool = True,
) -> Image.Image:
    """Key out a green/blue screen -> RGBA, with despill + edge de-fringe.

    ``tolerance`` "greenness" below which a pixel is fully kept (subject).
    ``softness``  width of the soft edge above tolerance (bg fully transparent).
    """
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_DIM, MAX_DIM))
    arr = np.asarray(img).astype(np.float64)

    if color == "auto":
        color = detect_screen(arr) or "green"

    score = _score(arr, color)  # high on the screen, low/negative on the subject
    lo, hi = float(tolerance), float(tolerance + max(1, softness))
    # alpha 1 where score<=lo (subject), 0 where score>=hi (screen), ramp between.
    alpha = 1.0 - np.clip((score - lo) / (hi - lo), 0.0, 1.0)

    # De-fringe: estimate the screen colour and unmix it from edge pixels.
    rgb = arr.copy()
    if defringe:
        screen_px = arr[score > hi]
        screen = (
            np.median(screen_px, axis=0)
            if len(screen_px) > 16
            else np.array([0.0, 255.0, 0.0]) if color == "green"
            else np.array([0.0, 0.0, 255.0])
        )
        a = alpha[..., None]
        partial = (alpha > 0.02) & (alpha < 0.98)
        with np.errstate(divide="ignore", invalid="ignore"):
            unmixed = (rgb - (1.0 - a) * screen) / np.clip(a, 1e-3, 1.0)
        rgb[partial] = np.clip(unmixed[partial], 0, 255)

    # Despill the surviving subject (spill reaches well past the edge).
    if despill_strength > 0:
        rgb = despill_array(rgb, despill_strength, color)

    out = np.dstack([rgb, (alpha * 255.0)]).astype(np.uint8)
    return _trim_alpha(Image.fromarray(out, mode="RGBA"))


def make_chroma_cutout(
    image_bytes: bytes,
    *,
    fmt: str = "png",
    color: str = "auto",
    tolerance: int = 25,
    softness: int = 45,
    despill_strength: float = 0.8,
    strict: bool = False,
) -> bytes | None:
    """Image bytes -> transparent green/blue-screen cutout bytes, or None."""
    if not image_bytes:
        if strict:
            raise DespillError("empty input")
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        out = chroma_key(
            img, color=color, tolerance=tolerance, softness=softness,
            despill_strength=despill_strength,
        )
        buf = io.BytesIO()
        if fmt.lower() == "webp":
            out.save(buf, format="WEBP", quality=82, method=6)
        else:
            out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        if strict:
            raise DespillError(f"could not process chroma key: {exc}") from exc
        return None
