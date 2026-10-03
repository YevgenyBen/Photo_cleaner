# photo-cleaner

Finds clutter in your Google Photos library and gives you a review page with
one-click links to delete it. It runs on your own computer, on a schedule.

**It never deletes anything.** Google's Photos API has no delete call, and since
April 2025 apps can't even list photos they didn't upload. So photo-cleaner
works from **Google Takeout** exports instead, and you do the deleting from the report.

## What it flags

| Section | How | Confidence |
|---|---|---|
| Exact duplicates | identical bytes uploaded more than once | high |
| Screenshots | file name or the phone folder it was backed up from | high |
| Tiny images | longest side under 400 px (stickers, thumbnails) | high |
| Near-duplicates and bursts | perceptual hash; shots within 90 s that look alike, or resized copies at any time. The best copy is marked **Keep** | medium |
| Very blurry | Laplacian focus measure of the sharpest region (portraits with a soft background are fine) | medium |
| Almost black or white | pocket shots, blank frames | medium |
| Received in chat apps | WhatsApp/Telegram/Messenger/Signal names and folders | low |

Never flagged: **favourites**, items in **Trash**, and (for the single-photo rules)
anything in one of your **albums**. The same photo appearing in an album folder
and a year folder, or an original plus its `-edited` version, counts as one item.

## Setup

Python 3.11 or newer.

```bash
git clone https://github.com/yevgenyben/photo_cleaner && cd photo_cleaner
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[heic]"
```

### 1. Try it once by hand

Request an export at <https://takeout.google.com>: *Deselect all*, tick
**Google Photos**, file type **.zip**, size **50 GB**. When the email arrives,
download the zip part(s) into one folder, then run:

```bash
photo-cleaner scan ~/Downloads/takeout-*.zip --open
```

This takes about 15 minutes per 50,000 photos on a 4-core machine, and nothing
is unpacked to disk. The report is a single HTML file. Click a photo to open it
in Google Photos, delete it there (Trash keeps it for 60 days), and tick
**Done**. Your ticks are saved in your browser.

### 2. Put it on a schedule

1. In Takeout, choose **Export every 2 months for 1 year** and the delivery method
   **Add to Drive**. With [Google Drive for desktop](https://www.google.com/drive/download/)
   installed, the zips sync to a local `Takeout` folder automatically.
2. Create a config and install the daily check:

```bash
photo-cleaner init                 # writes ~/.config/photo-cleaner/config.toml, guesses the Takeout folder
$EDITOR ~/.config/photo-cleaner/config.toml
photo-cleaner install-schedule     # systemd timer (Linux), launchd (macOS) or Task Scheduler (Windows)
```

Once a day, `photo-cleaner run` checks the inbox folder. If a new export has
arrived and finished syncing, it builds a report in `reports_dir` and sends a
desktop notification. Otherwise it does nothing. Later reports only show **new**
suggestions: anything you were shown before and kept isn't shown again.
Analysis results are cached, so only new photos get analyzed.

Use `install-schedule --dry-run` to see what would be installed. If you'd rather
use cron, add `13 9 * * * /path/to/.venv/bin/photo-cleaner run`.

## Tuning

All rules and thresholds live under `[detect]` in the config, for example:
`blur_threshold` (lower means only the very blurriest), `burst_max_distance`,
`messaging = false`, and `protect_album_items`.

## Try the demo

```bash
pip install -e ".[dev]"
python scripts/make_demo_takeout.py demo         # fake 2-part Takeout from sample photos
photo-cleaner scan demo --open
python scripts/check_demo.py demo                # compares the findings with the answer key
pytest
```

## Limits

- Takeout can be scheduled at most every 2 months, so reports arrive at that pace.
  You can also request a one-off export any time and drop it in the inbox.
- Videos are only checked for exact duplicates.
- RAW files are only checked for exact duplicates.
- You delete each photo by hand in Google Photos. There is no supported way
  to delete in bulk from outside.
