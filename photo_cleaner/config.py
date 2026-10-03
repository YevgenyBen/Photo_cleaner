"""Configuration file handling (TOML)."""
from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .detect import Settings


def default_config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "photo-cleaner" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "photo-cleaner" / "config.toml"


def default_state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "photo-cleaner"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "photo-cleaner"


@dataclass
class Config:
    inbox: Path
    reports_dir: Path
    state_dir: Path = field(default_factory=default_state_dir)
    only_new: bool = True
    workers: int = 0
    notify: bool = True
    open_report: bool = False
    settle_minutes: int = 15
    detect: Settings = field(default_factory=Settings)


def load_config(path: Path) -> Config:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    known = {f.name for f in dataclasses.fields(Settings)}
    detect_raw = raw.pop("detect", {})
    unknown = set(detect_raw) - known
    if unknown:
        raise SystemExit(f"{path}: unknown [detect] options: {', '.join(sorted(unknown))}")
    for key in ("inbox", "reports_dir", "state_dir"):
        if key in raw:
            raw[key] = Path(os.path.expandvars(raw[key])).expanduser()
    if "inbox" not in raw or "reports_dir" not in raw:
        raise SystemExit(f"{path}: 'inbox' and 'reports_dir' are required")
    top = {f.name for f in dataclasses.fields(Config)} - {"detect"}
    unknown = set(raw) - top
    if unknown:
        raise SystemExit(f"{path}: unknown options: {', '.join(sorted(unknown))}")
    return Config(**raw, detect=Settings(**detect_raw))


def guess_inbox() -> Path:
    """Where Google Drive for desktop usually puts Takeout deliveries."""
    home = Path.home()
    candidates = [
        home / "Google Drive" / "My Drive" / "Takeout",
        Path("G:/My Drive/Takeout"),
        *sorted(home.glob("Library/CloudStorage/GoogleDrive-*/My Drive/Takeout")),
        home / "Downloads",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def render_default_config(inbox: Path, reports_dir: Path) -> str:
    s = Settings()
    return f'''# photo-cleaner configuration

# Folder where Google Takeout zip files arrive. With "Google Drive for
# desktop", set Takeout's delivery method to "Add to Drive" and point this at
# the synced "Takeout" folder. Otherwise, download the zips here yourself.
inbox = "{inbox.as_posix()}"

# Where HTML review reports are written.
reports_dir = "{reports_dir.as_posix()}"

# Only show suggestions that weren't in an earlier report. Anything you were
# shown before and didn't delete is treated as "keep".
only_new = true

# Parallel workers for image analysis (0 = number of CPUs - 1).
workers = 0

# Desktop notification / open the report in a browser when a new one is ready.
notify = true
open_report = false

# Ignore an export until its zips haven't changed for this many minutes
# (Drive may still be syncing).
settle_minutes = 15

[detect]
exact_duplicates = {str(s.exact_duplicates).lower()}
near_duplicates = {str(s.near_duplicates).lower()}
burst_window_seconds = {s.burst_window_seconds}   # shots this close in time can be a burst
burst_max_distance = {s.burst_max_distance}       # 0-64, higher = looser "looks alike"
global_max_distance = {s.global_max_distance}     # same picture at any time (resized copies)
screenshots = {str(s.screenshots).lower()}
tiny = {str(s.tiny).lower()}
tiny_max_side = {s.tiny_max_side}
blurry = {str(s.blurry).lower()}
blur_threshold = {s.blur_threshold}       # lower = only flag the very blurriest
dark = {str(s.dark).lower()}
messaging = {str(s.messaging).lower()}       # WhatsApp/Telegram/... saves (low confidence)
graphics = {str(s.graphics).lower()}       # PNGs without camera data (low confidence)
protect_album_items = {str(s.protect_album_items).lower()}  # never flag single photos that are in an album
'''
