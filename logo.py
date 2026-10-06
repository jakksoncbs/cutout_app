"""Flat-background / logo mode — knock out a solid background behind a graphic.

This is a non-ML colour-key method, the right tool for raster logos and
graphics on a flat background (rembg is wrong for logos — it keeps one object).
Everything here is offline: NumPy + Pillow only.

The recipe:

1. Detect the background colour from the four corners. If the corners disagree a
   lot, it's probably a photo, not a flat background.
2. **Flood-fill inward from the edges** so enclosed art survives (the whites
   inside an emblem, the counters of letters like O/A).
3. Build a **graduated alpha matte**: pixels near the background colour fade out
   (kills the anti-aliased halo around thin text), far pixels stay solid.
4. Optionally clear small enclosed background-coloured holes.
5. **De-fringe** semi-transparent edge pixels so no pale background tint remains.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

MAX_DIM = 1600


class LogoError(Exception):
    """Raised (in strict mode) when a logo cutout cannot be produced."""


def _corner_samples(arr: np.ndarray, frac: float = 0.06) -> np.ndarray:
    """Collect pixels from the four corner patches."""
    h, w = arr.shape[:2]
    ch = max(1, int(h * frac))
    cw = max(1, int(w * frac))
    patches = [
        arr[:ch, :cw],
        arr[:ch, w - cw:],
        arr[h - ch:, :cw],
        arr[h - ch:, w - cw:],
    ]
    return np.concatenate([p.reshape(-1, arr.shape[2]) for p in patches], axis=0)


def corner_bg(arr: np.ndarray) -> tuple[np.ndarray, float]:
    """Return (median background colour, disagreement spread) from the corners."""
    samples = _corner_samples(arr).astype(np.float64)
    bg = np.median(samples, axis=0)
    # Spread = typical distance of corner pixels from the median colour.
    spread = float(np.median(np.sqrt(((samples - bg) ** 2).sum(axis=1))))
    return bg, spread


def suggest_mode(image_bytes: bytes) -> str:
    """Local heuristic (no network): guess 'logo' vs 'subject'.

    Flat, uniform borders that enclose the art -> 'logo'. Busy/varied borders
    (a photo) -> 'subject'. Deliberately conservative: when unsure, 'subject'.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((512, 512))
        arr = np.asarray(img).astype(np.float64)
    except Exception:
        return "subject"

    bg, spread = corner_bg(arr)
    if spread > 28:  # corners disagree -> looks like a photo
        return "subject"

    # How much of the border ring is close to the background colour?
    dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    h, w = dist.shape
    b = max(2, int(min(h, w) * 0.04))
    ring = np.concatenate([
        dist[:b, :].ravel(), dist[-b:, :].ravel(),
        dist[:, :b].ravel(), dist[:, -b:].ravel(),
    ])
    border_bg_frac = float((ring < 32).mean())

    # Colour complexity: graphics are built from a handful of flat colours; a
    # photo is continuous-tone with thousands. A flat border alone isn't enough
    # (photos can be letterboxed), so also require low colour complexity.
    q = (arr.astype(np.uint8) >> 3).reshape(-1, 3)
    _, counts = np.unique(q, axis=0, return_counts=True)
    top8_frac = float(np.sort(counts)[::-1][:8].sum() / len(q))

    flat_border = border_bg_frac > 0.85
    few_colours = top8_frac > 0.85
    return "logo" if (flat_border and few_colours) else "subject"


def cutout_logo(
    img: Image.Image,
    *,
    tolerance: int = 30,
    feather: int = 12,
    keep_interior: bool = False,
    defringe: bool = True,
) -> Image.Image:
    """Knock out a flat background from a logo/graphic -> RGBA.

    ``tolerance``     colour distance counted as "background".
    ``feather``       width of the graduated fade that kills anti-alias halos.
    ``keep_interior`` preserve enclosed background-coloured regions (e.g. a white
                      shape deliberately inside a badge). Off by default: the
                      common case for "basic raster logos" is that the counters
                      of O/A and the gap in a ring should become transparent.
    ``defringe``      remove residual background tint from semi-transparent edges.
    """
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_DIM, MAX_DIM))
    arr = np.asarray(img).astype(np.float64)
    h, w = arr.shape[:2]

    bg, _ = corner_bg(arr)

    # --- 1/2. Flood-fill the exterior background from the border ---------
    # Mark the exterior with a sentinel so enclosed art is preserved. We flood a
    # scratch RGB from every border pixel that is background-coloured.
    SENT = (0, 255, 1)  # improbable colour; we only read the mask back
    scratch = img.copy()
    seeds: list[tuple[int, int]] = []
    step = max(1, min(h, w) // 64)
    for x in range(0, w, step):
        seeds.append((x, 0))
        seeds.append((x, h - 1))
    for y in range(0, h, step):
        seeds.append((0, y))
        seeds.append((w - 1, y))

    dist_full = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    for (x, y) in seeds:
        if dist_full[y, x] <= tolerance and scratch.getpixel((x, y)) != SENT:
            ImageDraw.floodfill(scratch, (x, y), SENT, thresh=tolerance)

    exterior = np.all(np.asarray(scratch) == np.array(SENT), axis=2)

    # --- 3. Graduated alpha matte ---------------------------------------
    # Near-bg pixels fade out between tolerance and tolerance+feather, which
    # kills the anti-aliased halo around thin edges.
    lo, hi = float(tolerance), float(tolerance + max(1, feather))
    grad = np.clip((dist_full - lo) / (hi - lo), 0.0, 1.0)

    alpha = np.where(exterior, 0.0, 255.0)
    if keep_interior:
        # Fade only a band just inside the exterior boundary; fully-enclosed
        # background-coloured regions keep full alpha (preserve interior art).
        k = 2 * max(1, feather) + 1
        ext_img = Image.fromarray((exterior * 255).astype(np.uint8))
        dilated = np.asarray(ext_img.filter(ImageFilter.MaxFilter(min(k, 25)))) > 0
        band = dilated & ~exterior
        faded = np.minimum(alpha, grad * 255.0)
        alpha = np.where(band, faded, alpha)
    else:
        # Default: clear every background-coloured pixel, enclosed or not —
        # the expected result for basic logos (transparent counters/holes).
        alpha = np.minimum(alpha, grad * 255.0)

    alpha_u8 = alpha.astype(np.uint8)

    # --- 5. De-fringe semi-transparent edges ----------------------------
    rgb = arr.copy()
    if defringe:
        a = (alpha_u8.astype(np.float64) / 255.0)[..., None]
        partial = (a[..., 0] > 0.02) & (a[..., 0] < 0.98)
        with np.errstate(divide="ignore", invalid="ignore"):
            unmixed = (rgb - (1.0 - a) * bg) / np.clip(a, 1e-3, 1.0)
        rgb[partial] = np.clip(unmixed[partial], 0, 255)

    out = np.dstack([rgb.astype(np.uint8), alpha_u8])
    result = Image.fromarray(out, mode="RGBA")
    return _trim_alpha(result)


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


def make_logo_cutout(
    image_bytes: bytes,
    *,
    fmt: str = "png",
    tolerance: int = 30,
    feather: int = 12,
    keep_interior: bool = False,
    strict: bool = False,
) -> bytes | None:
    """Image bytes -> transparent logo cutout bytes, or None on failure."""
    if not image_bytes:
        if strict:
            raise LogoError("empty input")
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        out = cutout_logo(
            img, tolerance=tolerance, feather=feather, keep_interior=keep_interior
        )
        buf = io.BytesIO()
        if fmt.lower() == "webp":
            out.save(buf, format="WEBP", quality=82, method=6)
        else:
            out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        if strict:
            raise LogoError(f"could not process logo: {exc}") from exc
        return None
