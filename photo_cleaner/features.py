"""Per-file measurements: content hash, perceptual hash, sharpness, exposure."""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageOps

try:  # HEIC/HEIF support (iPhone photos) is optional.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 300_000_000

ANALYSIS_SIDE = 1024
THUMB_SIDE = 220
EXIF_MAKE, EXIF_MODEL, EXIF_IFD, EXIF_DATETIME_ORIGINAL = 0x010F, 0x0110, 0x8769, 0x9003


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def analyze(data: bytes, kind: str) -> dict:
    """Measure one file. Non-images only get a content hash."""
    out = {"sha256": sha256(data)}
    if kind != "image":
        return out
    try:
        out.update(_analyze_image(data))
    except Exception as exc:  # corrupt or unsupported file
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def _analyze_image(data: bytes) -> dict:
    im = Image.open(io.BytesIO(data))
    width, height = im.size
    exif = im.getexif()
    make = str(exif.get(EXIF_MAKE, "") or "").strip("\x00 ")
    model = str(exif.get(EXIF_MODEL, "") or "").strip("\x00 ")
    taken = _exif_time(exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL))

    im.draft("RGB", (ANALYSIS_SIDE, ANALYSIS_SIDE))  # fast JPEG downscale on decode
    im = ImageOps.exif_transpose(im)
    gray = im.convert("L")
    gray.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.BILINEAR)
    a = np.asarray(gray, dtype=np.float32)

    return {
        "width": width,
        "height": height,
        "camera": f"{make} {model}".strip(),
        "exif_taken": taken,
        "brightness": float(a.mean()),
        "contrast": float(a.std()),
        "sharpness": sharpness(a),
        "dhash": dhash(gray),
    }


def sharpness(a: np.ndarray) -> float:
    """Focus measure: Laplacian variance of the sharpest region.

    Using the best of a 4x4 grid instead of the whole frame keeps portraits
    with a blurred background (sharp subject, soft rest) from looking blurry.
    """
    if a.shape[0] < 8 or a.shape[1] < 8:
        return 0.0
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    h, w = lap.shape
    tiles = [
        lap[i * h // 4 : (i + 1) * h // 4, j * w // 4 : (j + 1) * w // 4].var()
        for i in range(4)
        for j in range(4)
    ]
    tiles.sort()
    return float(tiles[-2])  # second best: one noisy tile shouldn't decide


def dhash(gray: Image.Image, size: int = 8) -> int:
    """64-bit difference hash; similar images have a small Hamming distance."""
    small = np.asarray(gray.resize((size + 1, size), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def thumbnail(data: bytes, kind: str) -> bytes | None:
    """Small JPEG for the report, or None if the file can't be decoded."""
    if kind != "image":
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return buf.getvalue()
    except Exception:
        return None


def _exif_time(value) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
