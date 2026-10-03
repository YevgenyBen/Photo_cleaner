import io
import json
import os
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from photo_cleaner.cli import main
from photo_cleaner.detect import Settings
from photo_cleaner.scan import run_scan
from photo_cleaner.state import State

ROOT = "Takeout/Google Photos/Photos from 2024"


def texture(seed: int, blur: float = 0) -> bytes:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (24, 32, 3), dtype=np.uint8)
    im = Image.fromarray(small).resize((800, 600), Image.Resampling.NEAREST)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def sidecar(title: str, n: int, **extra) -> bytes:
    return json.dumps({"title": title, "photoTakenTime": {"timestamp": str(1_700_000_000 + n * 3600)},
                       "url": f"https://photos.google.com/photo/{n}", **extra}).encode()


def make_export(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"{ROOT}/{name}", content)
    return path


def basic_export(path: Path) -> Path:
    return make_export(path, {
        "a.jpg": texture(1), "a.jpg.json": sidecar("a.jpg", 1),
        "b.jpg": texture(1), "b.jpg.json": sidecar("b.jpg", 2),          # exact duplicate of a
        "c.jpg": texture(2, blur=12), "c.jpg.json": sidecar("c.jpg", 3),  # blurry
        "d.jpg": texture(3, blur=12), "d.jpg.json": sidecar("d.jpg", 4, favorited=True),  # blurry favourite
        "e.jpg": texture(4), "e.jpg.json": sidecar("e.jpg", 5),
    })


def categories(result):
    return {(f.category, i.title) for f in result.findings for i in f.flagged}


def test_scan_finds_clutter_and_protects_favourites(tmp_path):
    z = basic_export(tmp_path / "takeout-20240101T000000Z-001.zip")
    state = State(tmp_path / "state")
    result = run_scan([z], tmp_path / "out", state, Settings(), log=lambda m: None)
    assert categories(result) == {("exact_duplicate", "b.jpg"), ("blurry", "c.jpg")}
    html = result.report.read_text()
    assert "https://photos.google.com/photo/3" in html and "data:image/jpeg;base64" in html


def test_only_new_skips_previously_reported_and_uses_cache(tmp_path):
    z = basic_export(tmp_path / "takeout-20240101T000000Z-001.zip")
    state = State(tmp_path / "state")
    first = run_scan([z], tmp_path / "out", state, Settings(), only_new=True, log=lambda m: None)
    assert len(first.findings) == 2
    logs = []
    second = run_scan([z], tmp_path / "out", state, Settings(), only_new=True, log=logs.append)
    assert second.findings == []
    assert any("0 to analyse" in m for m in logs)


def test_run_command_processes_each_export_once(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    z = basic_export(inbox / "takeout-20240101T000000Z-001.zip")
    config = tmp_path / "config.toml"
    config.write_text(
        f'inbox = "{inbox.as_posix()}"\nreports_dir = "{(tmp_path / "reports").as_posix()}"\n'
        f'state_dir = "{(tmp_path / "state").as_posix()}"\nnotify = false\nsettle_minutes = 15\n'
    )
    # Freshly written zip: Drive may still be syncing, so wait.
    assert main(["run", "--config", str(config)]) == 0
    assert not (tmp_path / "reports").exists()

    old = time.time() - 3600
    os.utime(z, (old, old))
    assert main(["run", "--config", str(config)]) == 0
    assert len(list((tmp_path / "reports").glob("*.html"))) == 1
    assert "Suggested for deletion: 2" in capsys.readouterr().out

    assert main(["run", "--config", str(config)]) == 0
    assert len(list((tmp_path / "reports").glob("*.html"))) == 1


def test_init_writes_loadable_config(tmp_path):
    from photo_cleaner.config import load_config

    path = tmp_path / "c" / "config.toml"
    assert main(["init", "--config", str(path), "--inbox", str(tmp_path)]) == 0
    cfg = load_config(path)
    assert cfg.inbox == tmp_path and cfg.detect.blur_threshold == Settings().blur_threshold


def test_schedule_uses_venv_interpreter(tmp_path, capsys):
    import sys

    path = tmp_path / "config.toml"
    main(["init", "--config", str(path), "--inbox", str(tmp_path)])
    capsys.readouterr()
    assert main(["install-schedule", "--config", str(path), "--dry-run"]) == 0
    assert str(Path(sys.executable).absolute()) in capsys.readouterr().out
