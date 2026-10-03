"""Build a realistic fake Google Takeout export for trying photo-cleaner.

Uses the sample photographs bundled with scikit-image and lays them out the
way Takeout does (two zip parts, year folders, an album, sidecar JSONs with
their naming quirks). Each file is either clutter or a trap that must NOT be
flagged; EXPECTED at the bottom lists which is which.

    python scripts/make_demo_takeout.py demo/
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from skimage import data

ROOT = "Takeout/Google Photos"
_counter = 0


def photo(name: str, scale: float = 1) -> Image.Image:
    arr = data.stereo_motorcycle()[0] if name == "motorcycle" else getattr(data, name)()
    im = Image.fromarray(arr).convert("RGB")
    if scale != 1:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
    return im


def jpeg(im: Image.Image, make: str = "Google", model: str = "Pixel 8", q: int = 88) -> bytes:
    exif = Image.Exif()
    if make:
        exif[0x010F], exif[0x0110] = make, model
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q, exif=exif)
    return buf.getvalue()


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def sidecar(title: str, when: str, **extra) -> bytes:
    global _counter
    _counter += 1
    ts = int(datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp())
    meta = {
        "title": title,
        "description": "",
        "imageViews": "3",
        "creationTime": {"timestamp": str(ts + 60), "formatted": when},
        "photoTakenTime": {"timestamp": str(ts), "formatted": when},
        "url": f"https://photos.google.com/photo/AF1QipDemo{_counter:04d}",
    }
    meta.update(extra)
    return json.dumps(meta, indent=2).encode()


def device(folder: str) -> dict:
    return {"googlePhotosOrigin": {"mobileUpload": {"deviceFolder": {"localFolderName": folder}, "deviceType": "ANDROID_PHONE"}}}


def screenshot_image() -> Image.Image:
    im = Image.new("RGB", (1080, 2400), "#ffffff")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1080, 160), fill="#1a73e8")
    for y in range(240, 2300, 120):
        d.rounded_rectangle((60, y, 1020, y + 90), 20, fill="#f1f3f4")
        d.text((90, y + 30), f"Order #{y * 7} — delivered", fill="#202124")
    return im


def chat_screenshot() -> Image.Image:
    im = Image.new("RGB", (1080, 2340), "#0b141a")
    d = ImageDraw.Draw(im)
    for k, y in enumerate(range(200, 2200, 170)):
        x0 = 60 if k % 2 else 380
        d.rounded_rectangle((x0, y, x0 + 640, y + 120), 30, fill="#005c4b" if k % 2 == 0 else "#202c33")
        d.text((x0 + 30, y + 45), f"see you at {k + 5}pm?", fill="#e9edef")
    return im


def portrait_with_bokeh() -> Image.Image:
    """Sharp subject in the middle, heavily blurred surroundings: keep it."""
    im = photo("camera")
    bg = im.filter(ImageFilter.GaussianBlur(14))
    w, h = im.size
    box = (w // 3, h // 4, 2 * w // 3, 3 * h // 4)
    bg.paste(im.crop(box), box)
    return bg


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    y24, album = f"{ROOT}/Photos from 2024", f"{ROOT}/Rocket launch"
    part1: dict[str, bytes] = {}
    part2: dict[str, bytes] = {}

    def add(part, folder, name, content, meta_name=None, meta=None):
        part[f"{folder}/{name}"] = content
        if meta is not None:
            part[f"{folder}/{meta_name or name + '.supplemental-metadata.json'}"] = meta

    # Good photos (must not be flagged)
    add(part1, y24, "PXL_20240412_101500123.jpg", jpeg(photo("coffee")), meta=sidecar("PXL_20240412_101500123.jpg", "2024-04-12T10:15:00"))
    add(part1, y24, "PXL_20240501_180000456.jpg", jpeg(photo("hubble_deep_field")), meta=sidecar("PXL_20240501_180000456.jpg", "2024-05-01T18:00:00"))
    add(part1, y24, "PXL_20240520_090000789.jpg", jpeg(photo("immunohistochemistry")), meta=sidecar("PXL_20240520_090000789.jpg", "2024-05-20T09:00:00"))
    add(part1, y24, "PXL_20240601_120000000.jpg", jpeg(portrait_with_bokeh()), meta=sidecar("PXL_20240601_120000000.jpg", "2024-06-01T12:00:00"))

    # Same item in a year folder and an album (same URL): not a duplicate.
    rocket = jpeg(photo("rocket"))
    rocket_meta = sidecar("PXL_20240315_143000111.jpg", "2024-03-15T14:30:00")
    add(part1, y24, "PXL_20240315_143000111.jpg", rocket, meta=rocket_meta)
    add(part2, album, "PXL_20240315_143000111.jpg", rocket, meta=rocket_meta)
    part2[f"{album}/metadata.json"] = json.dumps({"title": "Rocket launch", "description": ""}).encode()

    # Original + "-edited" version share one sidecar: one item, not a duplicate.
    astro = photo("astronaut")
    astro_meta = sidecar("PXL_20240220_110000222.jpg", "2024-02-20T11:00:00")
    add(part1, y24, "PXL_20240220_110000222.jpg", jpeg(astro), meta=astro_meta)
    add(part1, y24, "PXL_20240220_110000222-edited.jpg", jpeg(ImageEnhance.Color(astro).enhance(1.3)))

    # Exact duplicate: the same bytes uploaded twice (different URL, "(1)" name).
    coffee_dup = jpeg(photo("coins", 2))
    add(part1, y24, "IMG_1002.jpg", coffee_dup, meta=sidecar("IMG_1002.jpg", "2024-07-04T09:00:00"))
    add(part1, y24, "IMG_1002(1).jpg", coffee_dup, meta_name="IMG_1002.jpg.supplemental-metadata(1).json",
        meta=sidecar("IMG_1002.jpg", "2024-07-04T09:00:00"))

    # Burst of 4 cat shots within 3 seconds; one sharp, others shaky/shifted.
    cat = photo("chelsea", 2)
    w, h = cat.size
    burst = [
        cat,
        cat.crop((20, 10, w, h)).resize((w, h)).filter(ImageFilter.GaussianBlur(4)),
        cat.crop((0, 25, w - 30, h)).resize((w, h)).filter(ImageFilter.GaussianBlur(2.5)),
        ImageEnhance.Brightness(cat.crop((35, 0, w, h - 15)).resize((w, h))).enhance(0.9).filter(ImageFilter.GaussianBlur(3)),
    ]
    for k, im in enumerate(burst):
        name = f"PXL_20240810_163012{k}00_BURST.jpg"
        add(part1 if k % 2 == 0 else part2, y24, name, jpeg(im), meta=sidecar(name, f"2024-08-10T16:30:1{k}"))

    # Resized copy of the hubble photo re-uploaded months later.
    add(part2, y24, "hubble_small.jpg", jpeg(photo("hubble_deep_field", 0.5), q=75),
        meta=sidecar("hubble_small.jpg", "2024-11-02T20:00:00"))

    # Very blurry shot.
    add(part2, y24, "PXL_20240902_071500333.jpg", jpeg(photo("motorcycle").filter(ImageFilter.GaussianBlur(8))),
        meta=sidecar("PXL_20240902_071500333.jpg", "2024-09-02T07:15:00"))
    # Blurry, but a favourite: keep.
    add(part2, y24, "PXL_20240903_071500444.jpg", jpeg(photo("moon").filter(ImageFilter.GaussianBlur(8))),
        meta=sidecar("PXL_20240903_071500444.jpg", "2024-09-03T07:15:00", favorited=True))
    # Blurry, but in an album: keep.
    add(part2, album, "PXL_20240315_143500555.jpg", jpeg(photo("cell").filter(ImageFilter.GaussianBlur(8))),
        meta=sidecar("PXL_20240315_143500555.jpg", "2024-03-15T14:35:00"))

    # Pocket shot: nearly black.
    rng = np.random.default_rng(1)
    dark = Image.fromarray(rng.integers(0, 10, (1500, 2000, 3), dtype=np.uint8))
    add(part2, y24, "PXL_20241005_221000666.jpg", jpeg(dark), meta=sidecar("PXL_20241005_221000666.jpg", "2024-10-05T22:10:00"))

    # Screenshots: by file name, and by the phone folder it was backed up from.
    add(part2, y24, "Screenshot_20241010-093000_Amazon.png", png(screenshot_image()),
        meta=sidecar("Screenshot_20241010-093000_Amazon.png", "2024-10-10T09:30:00", **device("Screenshots")))
    add(part2, y24, "IMG_5001.PNG", png(chat_screenshot()),
        meta=sidecar("IMG_5001.PNG", "2024-10-11T10:00:00", **device("Screenshots")))

    # WhatsApp forward; sidecar lives in the other zip part.
    wa = "IMG-20241020-WA0007.jpg"
    part1[f"{y24}/{wa}"] = jpeg(photo("brick", 1.5), make="")
    part2[f"{y24}/{wa}.supplemental-metadata.json"] = sidecar(wa, "2024-10-20T19:45:00", **device("WhatsApp Images"))

    # Tiny image (sticker / thumbnail).
    add(part2, y24, "sticker.jpg", jpeg(photo("clock").resize((160, 120)), make=""), meta=sidecar("sticker.jpg", "2024-10-21T08:00:00"))

    # Long file name: Takeout truncates the sidecar name to 51 characters.
    long_name = "Family_reunion_at_grandmas_garden_summer_2024_final_version.jpg"
    long_sidecar = (long_name + ".supplemental-metadata")[:46] + ".json"
    add(part2, y24, long_name, jpeg(photo("retina", 0.6)), meta_name=long_sidecar, meta=sidecar(long_name, "2024-07-20T15:00:00"))

    # Item already in Trash: ignored.
    add(part2, f"{ROOT}/Trash", "PXL_20240101_000000999.jpg", jpeg(dark), meta=sidecar("PXL_20240101_000000999.jpg", "2024-01-01T00:00:00"))

    # A duplicated video (only exact duplicates are detected for videos).
    video = b"\x00\x00\x00\x18ftypmp42" + rng.bytes(200_000)
    add(part1, y24, "PXL_20240811_120000.mp4", video, meta=sidecar("PXL_20240811_120000.mp4", "2024-08-11T12:00:00"))
    add(part2, y24, "PXL_20240811_120000(1).mp4", video, meta_name="PXL_20240811_120000.mp4.supplemental-metadata(1).json",
        meta=sidecar("PXL_20240811_120000.mp4", "2024-08-11T12:00:00"))

    for idx, part in enumerate((part1, part2), start=1):
        with zipfile.ZipFile(out / f"takeout-20261001T090000Z-{idx:03d}.zip", "w", zipfile.ZIP_STORED) as zf:
            for name, content in part.items():
                zf.writestr(name, content)
    print(f"Wrote demo export to {out}")


# What a correct scan should report (by Google Photos title).
EXPECTED = {
    "exact_duplicate": {"IMG_1002.jpg", "PXL_20240811_120000.mp4"},
    "near_duplicate": {"PXL_20240810_163012100_BURST.jpg", "PXL_20240810_163012200_BURST.jpg",
                       "PXL_20240810_163012300_BURST.jpg", "hubble_small.jpg"},
    "blurry": {"PXL_20240902_071500333.jpg"},
    "dark": {"PXL_20241005_221000666.jpg"},
    "screenshot": {"Screenshot_20241010-093000_Amazon.png", "IMG_5001.PNG"},
    "messaging": {"IMG-20241020-WA0007.jpg"},
    "tiny": {"sticker.jpg"},
}

if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "demo"))
