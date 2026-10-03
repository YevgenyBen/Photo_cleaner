"""Scan pipeline: index export -> analyse files -> detect -> report."""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import features
from .detect import Finding, Item, Settings, build_items, detect
from .report import write_report
from .source import Export, MediaFile
from .state import State


@dataclass
class ScanResult:
    report: Path
    items: int
    findings: list[Finding]
    flagged_bytes: int
    unreadable: int
    seconds: float


def cache_key(f: MediaFile) -> str:
    # The photo URL is the stable Google Photos id; size catches edits.
    url = f.meta.get("url")
    return f"{url}|{f.name}|{f.size}" if url else f"{f.path}|{f.size}"


def analyse_all(export: Export, state: State, workers: int, log) -> dict[str, dict]:
    feats: dict[str, dict] = {}
    todo: list[MediaFile] = []
    for f in export.files:
        cached = state.get_features(cache_key(f))
        if cached is not None:
            feats[f.path] = cached
        else:
            todo.append(f)
    log(f"{len(export.files)} files, {len(feats)} cached, {len(todo)} to analyse")
    if not todo:
        return feats

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    started, done = time.time(), 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {}
        queue = iter(todo)

        def submit_next() -> bool:
            f = next(queue, None)
            if f is None:
                return False
            pending[pool.submit(features.analyze, export.read(f), f.kind)] = f
            return True

        # Bounded window so a 50 GB export never sits in memory at once.
        for _ in range(workers * 3):
            if not submit_next():
                break
        while pending:
            fut = next(iter(pending))
            f = pending.pop(fut)
            result = fut.result()
            feats[f.path] = result
            state.put_features(cache_key(f), result)
            done += 1
            submit_next()
            if done % 200 == 0:
                state.commit()
                rate = done / max(time.time() - started, 1e-6)
                log(f"  analysed {done}/{len(todo)} ({rate:.0f}/s)")
    state.commit()
    return feats


def run_scan(
    inputs: list[Path],
    out_dir: Path,
    state: State,
    settings: Settings,
    only_new: bool = False,
    workers: int = 0,
    title: str = "Photo cleanup",
    log=lambda msg: print(msg, file=sys.stderr),
) -> ScanResult:
    t0 = time.time()
    export = Export(inputs)
    try:
        with_sidecar = sum(1 for f in export.files if f.meta)
        log(f"Indexed {len(export.files)} media files ({with_sidecar} with Takeout metadata)")
        feats = analyse_all(export, state, workers, log)
        items = build_items(export, feats)
        findings = detect(items, settings)

        if only_new:
            seen = state.reported_ids()
            findings = [f for f in findings if any(i.id not in seen for i in f.flagged)]

        thumbs = _thumbnails(export, findings)
        unreadable = sum(1 for i in items if "error" in i.feats)
        flagged_bytes = sum(i.size for f in findings for i in f.flagged)
        out_dir.mkdir(parents=True, exist_ok=True)
        report = out_dir / f"photo-cleanup-{time.strftime('%Y-%m-%d-%H%M%S')}.html"
        write_report(
            report,
            title=title,
            findings=findings,
            thumbs=thumbs,
            scanned=len(items),
            flagged_bytes=flagged_bytes,
            unreadable=unreadable,
            only_new=only_new,
        )
        state.mark_reported(i.id for f in findings for i in f.flagged)
        return ScanResult(report, len(items), findings, flagged_bytes, unreadable, time.time() - t0)
    finally:
        export.close()


def _thumbnails(export: Export, findings: list[Finding]) -> dict[str, bytes]:
    """Second pass: thumbnails only for the handful of files in the report."""
    wanted: dict[str, Item] = {}
    for f in findings:
        for i in f.flagged + f.keep:
            wanted[i.id] = i
    out = {}
    for item_id, item in wanted.items():
        try:
            thumb = features.thumbnail(export.read(item.main), item.main.kind)
        except Exception:
            thumb = None
        if thumb:
            out[item_id] = thumb
    return out
