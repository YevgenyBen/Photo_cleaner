"""Command line: scan, run (scheduled), init, install-schedule."""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
import zipfile
from pathlib import Path

from .config import default_config_path, default_state_dir, guess_inbox, load_config, render_default_config
from .detect import CATEGORIES, Settings
from .report import human_size
from .scan import ScanResult, run_scan
from .source import find_export_groups
from .state import State


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def summarize(result: ScanResult) -> str:
    counts: dict[str, int] = {}
    for f in result.findings:
        counts[f.category] = counts.get(f.category, 0) + len(f.flagged)
    lines = [
        f"Scanned {result.items} items in {result.seconds:.0f}s.",
        f"Suggested for deletion: {sum(counts.values())} (~{human_size(result.flagged_bytes)})",
    ]
    for cat, (name, _) in CATEGORIES.items():
        if counts.get(cat):
            lines.append(f"  {name:<32} {counts[cat]}")
    if result.unreadable:
        lines.append(f"  ({result.unreadable} files could not be decoded)")
    lines.append(f"Report: {result.report}")
    return "\n".join(lines)


# -- commands ----------------------------------------------------------------
def cmd_scan(args) -> int:
    cfg = load_config(args.config) if args.config else None
    settings = cfg.detect if cfg else Settings()
    state = State(args.state or (cfg.state_dir if cfg else default_state_dir()))
    out = args.out or (cfg.reports_dir if cfg else Path.cwd() / "reports")
    try:
        result = run_scan(
            [Path(p).expanduser() for p in args.paths],
            out,
            state,
            settings,
            only_new=args.only_new,
            workers=args.workers if args.workers is not None else (cfg.workers if cfg else 0),
            log=log,
        )
    finally:
        state.close()
    print(summarize(result))
    if args.open:
        webbrowser.open(result.report.resolve().as_uri())
    return 0


def cmd_run(args) -> int:
    """Scheduled entry point: process the newest Takeout export not seen yet."""
    cfg = load_config(args.config)
    if not cfg.inbox.exists():
        log(f"Inbox {cfg.inbox} does not exist (yet); nothing to do.")
        return 0
    state = State(cfg.state_dir)
    try:
        groups = find_export_groups(cfg.inbox)
        pending = {name: parts for name, parts in groups.items() if not state.export_done(name)}
        if not pending:
            log("No new Takeout export; nothing to do.")
            return 0
        name = max(pending)  # takeout-<timestamp> sorts chronologically
        parts = pending[name]
        newest_change = max(p.stat().st_mtime for p in parts)
        if time.time() - newest_change < cfg.settle_minutes * 60:
            log(f"{name} is still changing (syncing?); will try again next run.")
            return 0
        bad = [p.name for p in parts if not zipfile.is_zipfile(p)]
        if bad:
            log(f"{name}: incomplete or corrupt parts {bad}; will try again next run.")
            return 0

        log(f"Processing {name} ({len(parts)} part(s))")
        result = run_scan(
            parts, cfg.reports_dir, state, cfg.detect,
            only_new=cfg.only_new, workers=cfg.workers, title="Photo cleanup", log=log,
        )
        # A newer export supersedes older unprocessed ones.
        for other in pending:
            state.mark_export(other, str(result.report) if other == name else None)
    finally:
        state.close()

    print(summarize(result))
    flagged = sum(len(f.flagged) for f in result.findings)
    if cfg.notify:
        notify("Photo cleanup report ready", f"{flagged} suggestions (~{human_size(result.flagged_bytes)})")
    if cfg.open_report:
        webbrowser.open(result.report.resolve().as_uri())
    return 0


def cmd_init(args) -> int:
    path = args.config
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    inbox = Path(args.inbox).expanduser() if args.inbox else guess_inbox()
    reports = Path.home() / "Pictures" / "photo-cleaner-reports"
    path.write_text(render_default_config(inbox, reports), encoding="utf-8")
    print(f"Wrote {path}\n  inbox:   {inbox}\n  reports: {reports}\nEdit it, then run: photo-cleaner install-schedule")
    return 0


def cmd_install_schedule(args) -> int:
    load_config(args.config)  # fail early on a broken config
    python = Path(sys.executable).absolute()  # not resolve(): keep the venv interpreter
    argv = [str(python), "-m", "photo_cleaner", "run", "--config", str(args.config.resolve())]
    hour, minute = args.time.split(":")

    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        service = (
            "[Unit]\nDescription=Look for a new Google Takeout export and build a photo cleanup report\n\n"
            f"[Service]\nType=oneshot\nExecStart={shlex.join(argv)}\nNice=10\nIOSchedulingClass=idle\n"
        )
        timer = (
            "[Unit]\nDescription=Daily photo-cleaner check\n\n"
            f"[Timer]\nOnCalendar=*-*-* {hour}:{minute}:00\nPersistent=true\nRandomizedDelaySec=10m\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        if args.dry_run:
            print(f"# {unit_dir}/photo-cleaner.service\n{service}\n# {unit_dir}/photo-cleaner.timer\n{timer}")
            return 0
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "photo-cleaner.service").write_text(service)
        (unit_dir / "photo-cleaner.timer").write_text(timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "photo-cleaner.timer"], check=True)
        print("Installed systemd user timer. Check it with: systemctl --user list-timers photo-cleaner.timer")
        print("Logs: journalctl --user -u photo-cleaner.service")
        return 0

    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.photo-cleaner.plist"
        args_xml = "".join(f"<string>{a}</string>" for a in argv)
        log_path = default_state_dir() / "photo-cleaner.log"
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.photo-cleaner</string>
  <key>ProgramArguments</key><array>{args_xml}</array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{int(hour)}</integer><key>Minute</key><integer>{int(minute)}</integer></dict>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict></plist>
"""
        if args.dry_run:
            print(f"# {plist_path}\n{plist}")
            return 0
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist)
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist_path)], check=True)
        print(f"Installed launchd agent {plist_path}\nLogs: {log_path}")
        return 0

    if sys.platform == "win32":
        cmd = ["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", "PhotoCleaner",
               "/TR", subprocess.list2cmdline(argv), "/ST", f"{int(hour):02d}:{int(minute):02d}"]
        if args.dry_run:
            print(subprocess.list2cmdline(cmd))
            return 0
        subprocess.run(cmd, check=True)
        print("Installed Windows scheduled task 'PhotoCleaner'.")
        return 0

    print("No supported scheduler found. Add this line with `crontab -e`:")
    print(f"{int(minute)} {int(hour)} * * * {shlex.join(argv)}")
    return 0


def notify(title: str, body: str) -> None:
    try:
        if sys.platform == "darwin":
            script = f"display notification {body!r} with title {title!r}".replace("'", '"')
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# -- argument parsing ----------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="photo-cleaner", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan Takeout zips or folders now and write a report")
    s.add_argument("paths", nargs="+", help="Takeout .zip files and/or folders")
    s.add_argument("--out", type=Path, help="report directory (default: ./reports or config)")
    s.add_argument("--state", type=Path, help="state/cache directory")
    s.add_argument("--config", type=Path, help="use detection settings from this config")
    s.add_argument("--only-new", action="store_true", help="skip suggestions shown in earlier reports")
    s.add_argument("--workers", type=int)
    s.add_argument("--open", action="store_true", help="open the report in a browser")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("run", help="scheduled job: process a new export from the inbox, if any")
    r.add_argument("--config", type=Path, default=default_config_path())
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("init", help="write a starter config file")
    i.add_argument("--config", type=Path, default=default_config_path())
    i.add_argument("--inbox", help="folder where Takeout zips arrive")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    k = sub.add_parser("install-schedule", help="run `photo-cleaner run` daily via systemd/launchd/Task Scheduler")
    k.add_argument("--config", type=Path, default=default_config_path())
    k.add_argument("--time", default="09:13", help="daily time, HH:MM (default 09:13)")
    k.add_argument("--dry-run", action="store_true", help="print what would be installed")
    k.set_defaults(func=cmd_install_schedule)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
