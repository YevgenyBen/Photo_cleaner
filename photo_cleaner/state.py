"""SQLite state: an analysis cache, what was already reported, processed exports.

Each Takeout export contains the whole library again, so the cache keeps
re-runs fast, and "reported" lets scheduled runs show only new suggestions:
anything flagged before that is still in the next export is something you
decided to keep.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "state.db")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS features (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reported (item_id TEXT PRIMARY KEY, first_reported REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS exports (name TEXT PRIMARY KEY, processed REAL NOT NULL, report TEXT);
            """
        )

    def get_features(self, key: str) -> dict | None:
        row = self.db.execute("SELECT data FROM features WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_features(self, key: str, feats: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO features VALUES (?, ?)", (key, json.dumps(feats)))

    def reported_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT item_id FROM reported")}

    def mark_reported(self, ids) -> None:
        now = time.time()
        self.db.executemany("INSERT OR IGNORE INTO reported VALUES (?, ?)", [(i, now) for i in ids])
        self.db.commit()

    def export_done(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM exports WHERE name = ?", (name,)).fetchone() is not None

    def mark_export(self, name: str, report: str | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO exports VALUES (?, ?, ?)", (name, time.time(), report))
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
