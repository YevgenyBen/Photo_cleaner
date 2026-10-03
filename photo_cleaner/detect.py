"""Turn analysed files into clutter findings.

Nothing here deletes anything: a finding is a suggestion shown in the report.
Favourites are never flagged, and items that live in one of your albums are
never flagged by the single-photo rules (blurry, screenshot, ...).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .features import hamming
from .source import MediaFile

# category -> (title, confidence); confidence orders the report sections
CATEGORIES = {
    "exact_duplicate": ("Exact duplicates", "high"),
    "near_duplicate": ("Near-duplicates and bursts", "medium"),
    "screenshot": ("Screenshots", "high"),
    "tiny": ("Tiny images", "high"),
    "blurry": ("Very blurry", "medium"),
    "dark": ("Almost black or white", "medium"),
    "messaging": ("Received in chat apps", "low"),
    "graphic": ("Graphics without camera data", "low"),
}

CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

SCREENSHOT_NAME_RE = re.compile(
    r"screenshot|screen[ _]shot|^scr_|^scrn|captura de pantalla|bildschirmfoto|"
    r"capture d.(é|e)cran|schermata|скриншот|スクリーンショット",
    re.I,
)
MESSAGING_NAME_RE = re.compile(
    r"^(IMG|VID|STK|PTT)-\d{8}-WA\d+|^received_\d+|^FB_IMG_\d+|^signal-\d{4}-\d{2}-\d{2}|"
    r"^photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|^mmexport\d+|^viber_image",
    re.I,
)
MESSAGING_FOLDERS = ("whatsapp", "telegram", "messenger", "signal", "viber", "wechat", "line", "snapchat")


@dataclass
class Settings:
    exact_duplicates: bool = True
    near_duplicates: bool = True
    burst_window_seconds: int = 90
    burst_max_distance: int = 10
    global_max_distance: int = 3
    screenshots: bool = True
    tiny: bool = True
    tiny_max_side: int = 400
    blurry: bool = True
    blur_threshold: float = 10.0
    dark: bool = True
    dark_max_brightness: float = 18.0
    bright_min_brightness: float = 245.0
    flat_max_contrast: float = 12.0
    messaging: bool = True
    graphics: bool = False
    protect_album_items: bool = True


@dataclass
class Item:
    """One Google Photos item; Takeout may hold several files for it
    (album copies, an "-edited" version)."""

    id: str
    files: list[MediaFile]
    feats: dict
    in_album: bool
    albums: list[str]

    @property
    def main(self) -> MediaFile:
        edited = [f for f in self.files if f.is_edited]
        return edited[0] if edited else self.files[0]

    @property
    def meta(self) -> dict:
        for f in self.files:
            if f.meta:
                return f.meta
        return {}

    @property
    def url(self) -> str | None:
        return self.meta.get("url")

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.main.name

    @property
    def favorite(self) -> bool:
        return bool(self.meta.get("favorited"))

    @property
    def taken(self) -> float | None:
        ts = (self.meta.get("photoTakenTime") or {}).get("timestamp")
        if ts:
            try:
                value = float(ts)
                if value > 0:
                    return value
            except ValueError:
                pass
        return self.feats.get("exif_taken")

    @property
    def size(self) -> int:
        seen, total = set(), 0
        for f in self.files:
            key = (f.size, f.name)
            if key not in seen:
                seen.add(key)
                total += f.size
        return total

    @property
    def device_folder(self) -> str:
        origin = self.meta.get("googlePhotosOrigin") or {}
        return ((origin.get("mobileUpload") or {}).get("deviceFolder") or {}).get("localFolderName", "")

    @property
    def protected(self) -> bool:
        return self.favorite


@dataclass
class Finding:
    category: str
    flagged: list[Item]
    keep: list[Item] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=dict)  # item id -> reasons

    @property
    def is_group(self) -> bool:
        return bool(self.keep)


def build_items(export, feats_by_path: dict[str, dict]) -> list[Item]:
    """Collapse files into Google Photos items, dropping trashed ones."""
    groups: dict[str, list[MediaFile]] = defaultdict(list)
    for f in export.files:
        if export.is_trash(f):
            continue
        feats = feats_by_path.get(f.path, {})
        url = f.meta.get("url")
        if url:
            key = url
        else:
            # No sidecar: the same bytes under the same name are an album copy.
            base = re.sub(r"-edited(?=\.[^.]+$)", "", f.name, flags=re.I)
            key = f"sha:{feats.get('sha256', f.path)}:{base.lower()}"
        groups[key].append(f)

    items = []
    for key, files in groups.items():
        files.sort(key=lambda f: (f.is_edited, export.is_album(f), f.path))
        albums = sorted({f.folder for f in files if export.is_album(f)})
        main = next((f for f in files if f.is_edited), files[0])
        items.append(Item(key, files, feats_by_path.get(main.path, {}), bool(albums), albums))
    return items


def detect(items: list[Item], s: Settings) -> list[Finding]:
    findings: list[Finding] = []
    grouped: set[str] = set()

    if s.exact_duplicates:
        for f in _exact_duplicates(items):
            findings.append(f)
            grouped.update(i.id for i in f.flagged)

    if s.near_duplicates:
        # Screenshots of the same app all look alike; they get their own section.
        candidates = [
            i for i in items
            if i.id not in grouped and "dhash" in i.feats and not (s.screenshots and _is_screenshot(i))
        ]
        for f in _near_duplicates(candidates, s):
            findings.append(f)
            grouped.update(i.id for i in f.flagged)

    for item in items:
        if item.id in grouped or item.protected or (s.protect_album_items and item.in_album):
            continue
        reasons = sorted(_single_reasons(item, s), key=lambda r: CONFIDENCE_RANK[CATEGORIES[r[0]][1]])
        if reasons:
            category = reasons[0][0]
            findings.append(Finding(category, [item], reasons={item.id: [r for _, r in reasons]}))
    return findings


# -- groups ------------------------------------------------------------------
def _exact_duplicates(items: list[Item]) -> list[Finding]:
    by_hash: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        sha = item.feats.get("sha256")
        if sha:
            by_hash[sha].append(item)
    out = []
    for group in by_hash.values():
        if len(group) < 2:
            continue
        # Keep a favourite / album copy / the earliest upload.
        group.sort(key=lambda i: (not i.favorite, not i.in_album, _created(i), i.main.path))
        keep, rest = [group[0]], group[1:]
        keep += [i for i in rest if i.protected]
        flagged = [i for i in rest if not i.protected]
        if flagged:
            reasons = {i.id: ["Identical copy of the kept file"] for i in flagged}
            out.append(Finding("exact_duplicate", flagged, keep, reasons))
    return out


def _near_duplicates(items: list[Item], s: Settings) -> list[Finding]:
    parent = {i.id: i.id for i in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    # 1. Bursts: shots taken close together that look alike. A shot joins a
    #    burst if it resembles any shot in it, but a burst never spans more
    #    than the time window from its first shot, so a slow pan can't chain
    #    a whole afternoon together.
    timed = sorted((i for i in items if i.taken), key=lambda i: i.taken)
    bursts: list[list[Item]] = []
    for item in timed:
        bursts = [b for b in bursts if item.taken - b[0].taken <= s.burst_window_seconds]
        for burst in bursts:
            if any(hamming(m.feats["dhash"], item.feats["dhash"]) <= s.burst_max_distance for m in burst):
                burst.append(item)
                union(item.id, burst[0].id)
                break
        else:
            bursts.append([item])

    # 2. Same picture at any time (resized/re-saved copies). Pigeonhole:
    #    distance <= 3 means at least one of four 16-bit chunks matches exactly.
    if s.global_max_distance >= 0:
        informative = [i for i in items if 6 <= i.feats["dhash"].bit_count() <= 58]
        chunks = max(4, s.global_max_distance + 1)
        width = 64 // chunks
        for c in range(chunks):
            buckets: dict[int, list[Item]] = defaultdict(list)
            for item in informative:
                buckets[(item.feats["dhash"] >> (c * width)) & ((1 << width) - 1)].append(item)
            for bucket in buckets.values():
                if len(bucket) < 2 or len(bucket) > 300:  # huge buckets: flat images
                    continue
                for x in range(len(bucket)):
                    for y in range(x + 1, len(bucket)):
                        a, b = bucket[x], bucket[y]
                        if hamming(a.feats["dhash"], b.feats["dhash"]) <= s.global_max_distance:
                            union(a.id, b.id)

    clusters: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        clusters[find(item.id)].append(item)

    out = []
    for group in clusters.values():
        if len(group) < 2:
            continue
        best_px = max(_pixels(i) for i in group)

        # Copies with at least half the best resolution compete on sharpness;
        # small copies always lose (tiny images measure as "sharp").
        group.sort(key=lambda i: (
            not i.favorite,
            not i.in_album,
            _pixels(i) < 0.5 * best_px,
            -i.feats.get("sharpness", 0),
            -_pixels(i),
        ))
        keep = [group[0]] + [i for i in group[1:] if i.protected or (s.protect_album_items and i.in_album)]
        flagged = [i for i in group if i not in keep]
        if not flagged:
            continue
        reasons = {}
        for i in flagged:
            why = []
            if i.feats.get("sharpness", 0) < 0.8 * keep[0].feats.get("sharpness", 0):
                why.append("blurrier")
            if _pixels(i) < 0.9 * _pixels(keep[0]):
                why.append("lower resolution")
            reasons[i.id] = ["Same shot as the kept photo" + (f", {' and '.join(why)}" if why else "")]
        out.append(Finding("near_duplicate", flagged, keep, reasons))
    return out


# -- single items ------------------------------------------------------------
def _single_reasons(item: Item, s: Settings) -> list[tuple[str, str]]:
    f, name, folder = item.feats, item.title, item.device_folder.lower()
    reasons: list[tuple[str, str]] = []
    if s.screenshots and _is_screenshot(item):
        reasons.append(("screenshot", "Screenshot"))
    if s.messaging and (MESSAGING_NAME_RE.search(name) or any(m in folder for m in MESSAGING_FOLDERS)):
        app = item.device_folder or "a chat app"
        reasons.append(("messaging", f"Saved from {app}"))
    if "width" not in f:
        return reasons
    if s.tiny and max(f["width"], f["height"]) < s.tiny_max_side:
        reasons.append(("tiny", f"Only {f['width']}×{f['height']} px"))
    if s.dark and f["contrast"] < s.flat_max_contrast:
        if f["brightness"] <= s.dark_max_brightness:
            reasons.append(("dark", "Almost completely black"))
        elif f["brightness"] >= s.bright_min_brightness:
            reasons.append(("dark", "Almost completely white"))
    if s.blurry and f["sharpness"] < s.blur_threshold and f["contrast"] >= s.flat_max_contrast:
        reasons.append(("blurry", f"Very blurry (focus score {f['sharpness']:.1f})"))
    if (
        s.graphics
        and not reasons
        and item.main.ext == ".png"
        and not f.get("camera")
    ):
        reasons.append(("graphic", "PNG without camera data (download or screenshot?)"))
    return reasons


def _pixels(item: Item) -> int:
    return item.feats.get("width", 0) * item.feats.get("height", 0)


def _is_screenshot(item: Item) -> bool:
    return bool(SCREENSHOT_NAME_RE.search(item.title)) or "screenshot" in item.device_folder.lower()


def _created(item: Item) -> float:
    ts = (item.meta.get("creationTime") or {}).get("timestamp")
    try:
        return float(ts)
    except (TypeError, ValueError):
        return float("inf")
