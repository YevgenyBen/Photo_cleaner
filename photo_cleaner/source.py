"""Read a Google Takeout export (zip parts or an extracted folder).

Takeout lays Google Photos out as ``Takeout/Google Photos/<folder>/<file>``,
where ``<folder>`` is either a year bucket ("Photos from 2021") or an album.
Every media file has a JSON sidecar holding the original title, timestamps,
the ``photos.google.com`` URL and a few flags. Sidecar names are mangled in
several ways (``.supplemental-metadata``, truncation to 51 chars, ``(1)``
counters moved behind the extension), so pairing needs some care.

Files are read straight out of the zips; nothing is extracted to disk.
"""
from __future__ import annotations

import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".wmv", ".mpg"}
RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}

TAKEOUT_PART_RE = re.compile(r"^(takeout-\d{8}T\d{6}Z)(?:-\d+)?(?:-\d+)?\.zip$", re.I)
YEAR_FOLDER_RE = re.compile(r"(19|20)\d{2}$")
NON_ALBUM_FOLDERS = {"archive", "trash", "bin", "failed videos", "untitled"}
TRASH_FOLDERS = {"trash", "bin"}
COUNTER_RE = re.compile(r"^(?P<stem>.*?)(?P<counter>\(\d+\))(?P<ext>\.[^.]+)$")
SIDECAR_SUFFIX = ".supplemental-metadata"
EDITED_RE = re.compile(r"-(edited|bearbeitet|modifié|editado|modificato|bewerkt)(?=\.[^.]+$)", re.I)


@dataclass
class MediaFile:
    path: str                 # logical path, unique within the export
    container: str | None     # zip file path, or None for loose files
    member: str               # member name in the zip, or filesystem path
    size: int
    folder: str
    name: str
    meta: dict = field(default_factory=dict)

    @property
    def ext(self) -> str:
        return PurePosixPath(self.name).suffix.lower()

    @property
    def kind(self) -> str:
        if self.ext in IMAGE_EXTS:
            return "image"
        if self.ext in VIDEO_EXTS:
            return "video"
        return "other"

    @property
    def is_edited(self) -> bool:
        return bool(EDITED_RE.search(self.name))


class Export:
    """All files of one Takeout export, possibly spread over several zips."""

    def __init__(self, inputs: list[Path]):
        self.inputs = inputs
        self._zips: dict[str, zipfile.ZipFile] = {}
        self.files: list[MediaFile] = []
        self.album_folders: set[str] = set()
        self._index()

    # -- reading -----------------------------------------------------------
    def read(self, f: MediaFile) -> bytes:
        if f.container is None:
            return Path(f.member).read_bytes()
        return self._zip(f.container).read(f.member)

    def close(self) -> None:
        for zf in self._zips.values():
            zf.close()
        self._zips.clear()

    # -- indexing ----------------------------------------------------------
    def _index(self) -> None:
        jsons_by_dir: dict[str, dict[str, tuple]] = defaultdict(dict)
        media: list[tuple] = []
        for container, member, folder, name, size in iter_entries(self.inputs):
            dir_key = folder_key(member)
            if name.lower().endswith(".json"):
                jsons_by_dir[dir_key][name] = (container, member)
            else:
                ext = PurePosixPath(name).suffix.lower()
                if ext in IMAGE_EXTS | VIDEO_EXTS | RAW_EXTS:
                    media.append((dir_key, container, member, folder, name, size))

        # Load sidecars lazily per directory; Takeout may put a sidecar into a
        # different zip part than its photo, which the dir_key ignores.
        loaded: dict[str, dict[str, dict]] = {}
        for dir_key, container, member, folder, name, size in media:
            if dir_key not in loaded:
                loaded[dir_key] = self._load_dir_jsons(jsons_by_dir.get(dir_key, {}))
                # Year buckets ("Photos from 2021", localized) hold everything;
                # any other Takeout folder, or one with album metadata, is an album.
                has_album_meta = any(_is_album_metadata(j) for j in loaded[dir_key].values())
                if loaded[dir_key] and folder.lower() not in NON_ALBUM_FOLDERS and (
                    has_album_meta or not YEAR_FOLDER_RE.search(folder)
                ):
                    self.album_folders.add(dir_key)
            meta = match_sidecar(name, loaded[dir_key])
            path = member if container is None else f"{Path(container).name}/{member}"
            self.files.append(MediaFile(path, container, member, size, folder, name, meta or {}))
        self.dir_key_of = {f.path: folder_key(f.member) for f in self.files}

    def _load_dir_jsons(self, entries: dict[str, tuple]) -> dict[str, dict]:
        out = {}
        for name, (container, member) in entries.items():
            try:
                raw = Path(member).read_bytes() if container is None else self._zip(container).read(member)
                out[name] = json.loads(raw)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        return out

    def _zip(self, container: str) -> zipfile.ZipFile:
        zf = self._zips.get(container)
        if zf is None:
            zf = self._zips[container] = zipfile.ZipFile(container)
        return zf

    def is_album(self, f: MediaFile) -> bool:
        return self.dir_key_of[f.path] in self.album_folders

    def is_trash(self, f: MediaFile) -> bool:
        return f.folder.lower() in TRASH_FOLDERS or bool(f.meta.get("trashed"))


def iter_entries(inputs: list[Path]):
    """Yield (container, member, folder, name, size) for every file in the inputs."""
    for inp in inputs:
        if inp.is_dir():
            for p in sorted(inp.rglob("*")):
                if p.is_file():
                    yield from iter_entries([p])
        elif inp.suffix.lower() == ".zip":
            with zipfile.ZipFile(inp) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        p = PurePosixPath(info.filename)
                        yield str(inp), info.filename, p.parent.name, p.name, info.file_size
        elif inp.is_file():
            yield None, str(inp), inp.parent.name, inp.name, inp.stat().st_size


def folder_key(member: str) -> str:
    """Directory identity; zip parts of one export share the same internal layout."""
    return str(PurePosixPath(member.replace("\\", "/")).parent)


def _is_album_metadata(j: dict) -> bool:
    return isinstance(j, dict) and "title" in j and "photoTakenTime" not in j and "url" not in j


def _is_media_sidecar(j: dict) -> bool:
    return isinstance(j, dict) and ("photoTakenTime" in j or "url" in j or "creationTime" in j)


def match_sidecar(media_name: str, jsons: dict[str, dict]) -> dict | None:
    """Find the sidecar JSON for ``media_name`` among the JSONs of its folder."""
    if not jsons:
        return None
    base = EDITED_RE.sub("", media_name)
    counter = ""
    m = COUNTER_RE.match(base)
    if m:
        base, counter = m["stem"] + m["ext"], m["counter"]

    # 1. Exact names: IMG.jpg.json, IMG.jpg.supplemental-metadata.json, IMG.jpg(1).json
    for cand in (f"{base}{counter}.json", f"{base}{SIDECAR_SUFFIX}{counter}.json"):
        j = jsons.get(cand)
        if _is_media_sidecar(j):
            return j

    # 2. Truncated names: Takeout cuts sidecar file names at 51 characters.
    full = base + SIDECAR_SUFFIX
    best, best_len = None, 0
    for jname, j in jsons.items():
        if len(jname) < 46 or not _is_media_sidecar(j):
            continue
        stem = jname[:-5]
        if counter:
            if not stem.endswith(counter):
                continue
            stem = stem[: -len(counter)]
        if full.startswith(stem) and len(stem) > best_len:
            best, best_len = j, len(stem)
    if best is not None:
        return best

    # 3. Fall back to the sidecar's "title" field (the original upload name).
    if not counter:
        hits = [j for j in jsons.values() if _is_media_sidecar(j) and j.get("title") == base]
        if len(hits) == 1:
            return hits[0]
    return None


def find_export_groups(inbox: Path) -> dict[str, list[Path]]:
    """Group Takeout zip parts in ``inbox`` by export (takeout-<timestamp>)."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for z in sorted(inbox.glob("*.zip")):
        m = TAKEOUT_PART_RE.match(z.name)
        groups[m[1] if m else z.stem].append(z)
    return dict(groups)
