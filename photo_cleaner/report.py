"""Self-contained HTML review report (thumbnails embedded, no external assets)."""
from __future__ import annotations

import base64
import html
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .detect import CATEGORIES, CONFIDENCE_RANK, Finding, Item


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def photo_link(item: Item) -> str:
    return item.url or f"https://photos.google.com/search/{quote(item.title)}"


def _date(item: Item) -> str:
    ts = item.taken
    if not ts:
        return "no date"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _tile(item: Item, thumbs: dict[str, bytes], role: str, reasons: list[str]) -> str:
    e = html.escape
    thumb = thumbs.get(item.id)
    if thumb:
        img = f'<img alt="" src="data:image/jpeg;base64,{base64.b64encode(thumb).decode()}">'
    else:
        img = f'<div class="noimg">{e(item.main.ext.lstrip(".").upper() or "FILE")}</div>'
    badge = '<span class="badge keep">Keep</span>' if role == "keep" else ""
    done = (
        f'<label class="done"><input type="checkbox" data-id="{e(item.id)}"> Done</label>'
        if role == "flag"
        else ""
    )
    albums = f'<div class="meta">In album: {e(", ".join(item.albums))}</div>' if item.albums else ""
    why = "".join(f"<li>{e(r)}</li>" for r in reasons)
    return f"""
      <div class="tile {role}" data-id="{e(item.id)}">
        <a class="thumb" href="{e(photo_link(item))}" target="_blank" rel="noopener">{img}{badge}</a>
        <div class="info">
          <div class="name" title="{e(item.main.path)}">{e(item.title)}</div>
          <div class="meta">{_date(item)} · {human_size(item.size)}</div>
          {albums}
          {f'<ul class="why">{why}</ul>' if why else ''}
          <div class="actions">
            <a href="{e(photo_link(item))}" target="_blank" rel="noopener">Open in Google Photos ↗</a>
            {done}
          </div>
        </div>
      </div>"""


def write_report(
    path: Path,
    *,
    title: str,
    findings: list[Finding],
    thumbs: dict[str, bytes],
    scanned: int,
    flagged_bytes: int,
    unreadable: int,
    only_new: bool,
) -> None:
    e = html.escape
    by_cat: dict[str, list[Finding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)
    order = sorted(by_cat, key=lambda c: (CONFIDENCE_RANK[CATEGORIES[c][1]], list(CATEGORIES).index(c)))

    flagged_total = sum(len(f.flagged) for f in findings)
    nav, sections = [], []
    for cat in order:
        name, confidence = CATEGORIES[cat]
        group = by_cat[cat]
        count = sum(len(f.flagged) for f in group)
        size = sum(i.size for f in group for i in f.flagged)
        nav.append(f'<a href="#{cat}">{e(name)} <b>{count}</b></a>')
        cards = []
        for f in sorted(group, key=lambda f: -(f.flagged[0].taken or 0)):
            tiles = [_tile(i, thumbs, "keep", []) for i in f.keep]
            tiles += [_tile(i, thumbs, "flag", f.reasons.get(i.id, [])) for i in f.flagged]
            cls = "card group" if f.is_group else "card"
            cards.append(f'<div class="{cls}">{"".join(tiles)}</div>')
        sections.append(
            f"""
    <section id="{cat}">
      <h2>{e(name)} <span class="count">{count} · {human_size(size)}</span>
        <span class="conf {confidence}">{confidence} confidence</span></h2>
      <div class="cards">{''.join(cards)}</div>
    </section>"""
        )

    if not findings:
        sections.append('<p class="empty">Nothing to clean up. Your library looks tidy.</p>')

    scope = "New suggestions since the last report" if only_new else "All suggestions"
    generated = time.strftime("%Y-%m-%d %H:%M")
    report_id = path.stem

    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
:root {{
  --bg: #f6f5f2; --panel: #ffffff; --text: #1d1d1f; --muted: #6b6b70; --line: #e3e1dc;
  --accent: #1a73e8; --keep: #188038; --flag: #c5221f; --chip: #efede8;
  --high: #188038; --medium: #b06000; --low: #6b6b70;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #161618; --panel: #202023; --text: #ececee; --muted: #9a9aa2; --line: #333338;
    --accent: #8ab4f8; --keep: #81c995; --flag: #f28b82; --chip: #2a2a2e;
    --high: #81c995; --medium: #fdd663; --low: #9a9aa2;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
header {{ padding: 28px 16px 8px; max-width: 1200px; margin: 0 auto; }}
h1 {{ margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }}
.sub {{ color: var(--muted); margin: 0 0 18px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }}
.stat {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 10px 14px; min-width: 130px; }}
.stat b {{ display: block; font-size: 22px; font-variant-numeric: tabular-nums; }}
.stat span {{ color: var(--muted); font-size: 13px; }}
.howto {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; color: var(--muted); font-size: 14px; }}
.howto b {{ color: var(--text); }}
nav {{ position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line); }}
nav .inner {{ max-width: 1200px; margin: 0 auto; padding: 10px 16px; display: flex; gap: 8px; overflow-x: auto; align-items: center; }}
nav a {{ white-space: nowrap; text-decoration: none; color: var(--text); background: var(--chip); border-radius: 999px; padding: 5px 12px; font-size: 14px; }}
nav .progress {{ margin-left: auto; white-space: nowrap; color: var(--muted); font-size: 14px; font-variant-numeric: tabular-nums; }}
nav label {{ white-space: nowrap; font-size: 14px; color: var(--muted); }}
main {{ max-width: 1200px; margin: 0 auto; padding: 8px 16px 60px; }}
h2 {{ font-size: 19px; margin: 28px 0 12px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; }}
.count {{ color: var(--muted); font-weight: 400; font-size: 15px; }}
.conf {{ font-size: 12px; font-weight: 500; border: 1px solid currentColor; border-radius: 999px; padding: 1px 8px; }}
.conf.high {{ color: var(--high); }} .conf.medium {{ color: var(--medium); }} .conf.low {{ color: var(--low); }}
.cards {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.card {{ display: flex; flex-wrap: wrap; gap: 10px; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 10px; max-width: 100%; }}
.tile {{ width: 200px; display: flex; flex-direction: column; }}
.thumb {{ position: relative; display: block; width: 200px; height: 160px; border-radius: 10px; overflow: hidden; background: var(--chip); border: 3px solid transparent; }}
.tile.keep .thumb {{ border-color: var(--keep); }}
.tile.flag .thumb {{ border-color: var(--flag); }}
.thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.noimg {{ display: grid; place-items: center; height: 100%; color: var(--muted); font-weight: 600; }}
.badge {{ position: absolute; top: 6px; left: 6px; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; }}
.badge.keep {{ background: var(--keep); color: var(--panel); }}
.info {{ padding: 6px 2px 0; font-size: 13px; min-width: 0; overflow-wrap: anywhere; }}
.name {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.meta {{ color: var(--muted); }}
.why {{ margin: 4px 0; padding-left: 16px; }}
.actions {{ display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-top: 4px; }}
.actions a {{ color: var(--accent); text-decoration: none; }}
.done {{ color: var(--muted); cursor: pointer; }}
.tile.is-done {{ opacity: 0.4; }}
body.hide-done .tile.flag.is-done {{ display: none; }}
body.hide-done .card:not(:has(.tile.flag:not(.is-done))) {{ display: none; }}
.empty {{ font-size: 18px; color: var(--muted); padding: 40px 0; }}
footer {{ color: var(--muted); font-size: 13px; text-align: center; padding: 20px 16px 40px; }}
@media (max-width: 480px) {{
  .tile, .thumb {{ width: calc(50vw - 40px); }}
  .thumb {{ height: calc((50vw - 40px) * 0.8); }}
}}
</style>
</head>
<body>
<header>
  <h1>{e(title)}</h1>
  <p class="sub">{scope} · generated {generated}</p>
  <div class="stats">
    <div class="stat"><b>{scanned:,}</b><span>items scanned</span></div>
    <div class="stat"><b>{flagged_total:,}</b><span>suggested for deletion</span></div>
    <div class="stat"><b>{human_size(flagged_bytes)}</b><span>could be freed (approx.)</span></div>
    {f'<div class="stat"><b>{unreadable}</b><span>files could not be read</span></div>' if unreadable else ''}
  </div>
  <div class="howto">
    <b>Nothing has been deleted.</b> Google doesn't let apps delete from Google Photos, so you decide.
    Red frames are suggestions; green <b>Keep</b> frames are the best copy in a group.
    Click a photo to open it in Google Photos, delete it there (it stays in Trash for 60 days), then tick <b>Done</b>.
  </div>
</header>
<nav><div class="inner">
  {''.join(nav)}
  <label><input type="checkbox" id="hideDone"> Hide done</label>
  <span class="progress" id="progress"></span>
</div></nav>
<main>{''.join(sections)}
</main>
<footer>photo-cleaner · {e(report_id)}</footer>
<script>
(() => {{
  const KEY = "photo-cleaner:" + {report_id!r};
  let done = {{}};
  try {{ done = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
  const boxes = [...document.querySelectorAll('.done input')];
  const progress = document.getElementById('progress');
  function render() {{
    let n = 0;
    for (const b of boxes) {{
      b.checked = !!done[b.dataset.id];
      b.closest('.tile').classList.toggle('is-done', b.checked);
      if (b.checked) n++;
    }}
    progress.textContent = boxes.length ? `${{n}} / ${{boxes.length}} done` : '';
  }}
  for (const b of boxes) b.addEventListener('change', () => {{
    if (b.checked) done[b.dataset.id] = 1; else delete done[b.dataset.id];
    try {{ localStorage.setItem(KEY, JSON.stringify(done)); }} catch (e) {{}}
    render();
  }});
  document.getElementById('hideDone').addEventListener('change', ev =>
    document.body.classList.toggle('hide-done', ev.target.checked));
  render();
}})();
</script>
</body>
</html>
""",
        encoding="utf-8",
    )
