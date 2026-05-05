from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class DatabaseManager:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    config_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id INTEGER NOT NULL,
    ilo_host TEXT NOT NULL DEFAULT '',
    system_serial TEXT NOT NULL DEFAULT '',
    server_model TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    manager_model TEXT NOT NULL DEFAULT '',
    last_inventory_kind TEXT NOT NULL DEFAULT '',
    currently_seen INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    raw_summary_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (kit_id) REFERENCES kits(id)
);
CREATE INDEX IF NOT EXISTS idx_hosts_kit_id ON hosts(kit_id);
CREATE INDEX IF NOT EXISTS idx_hosts_serial ON hosts(system_serial);
CREATE INDEX IF NOT EXISTS idx_hosts_ilo_host ON hosts(ilo_host);
CREATE TABLE IF NOT EXISTS controllers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id INTEGER NOT NULL,
    redfish_path TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    firmware_version TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    currently_seen INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (host_id) REFERENCES hosts(id),
    UNIQUE(host_id, redfish_path)
);
CREATE TABLE IF NOT EXISTS drives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id INTEGER NOT NULL,
    controller_id INTEGER,
    redfish_path TEXT NOT NULL,
    controller_path TEXT NOT NULL DEFAULT '',
    controller_name TEXT NOT NULL DEFAULT '',
    bay TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    capacity_gib REAL NOT NULL DEFAULT 0,
    media_type TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    currently_seen INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (host_id) REFERENCES hosts(id),
    FOREIGN KEY (controller_id) REFERENCES controllers(id),
    UNIQUE(host_id, redfish_path)
);
CREATE TABLE IF NOT EXISTS storage_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id INTEGER NOT NULL,
    host_id INTEGER,
    plan_path TEXT NOT NULL UNIQUE,
    discovery_raw_path TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 0,
    os_controller_path TEXT NOT NULL DEFAULT '',
    data_controller_path TEXT NOT NULL DEFAULT '',
    hot_spare_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    plan_summary_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (kit_id) REFERENCES kits(id),
    FOREIGN KEY (host_id) REFERENCES hosts(id)
);
CREATE TABLE IF NOT EXISTS storage_plan_drives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storage_plan_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    drive_path TEXT NOT NULL,
    controller_path TEXT NOT NULL DEFAULT '',
    bay TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    capacity_gib REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (storage_plan_id) REFERENCES storage_plans(id)
);
CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id INTEGER NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    current_stage TEXT NOT NULL DEFAULT '',
    run_bundle_dir TEXT NOT NULL DEFAULT '',
    run_summary_path TEXT NOT NULL DEFAULT '',
    event_time TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (kit_id) REFERENCES kits(id)
);
CREATE TABLE IF NOT EXISTS known_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issue_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    kit_id INTEGER,
    host_id INTEGER,
    fingerprint TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    message TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (issue_id) REFERENCES known_issues(id),
    FOREIGN KEY (kit_id) REFERENCES kits(id),
    FOREIGN KEY (host_id) REFERENCES hosts(id)
);
"""


@dataclass
class SQLiteRuntime:
    path_fn: Callable[[], Path]
    schema_sql: str = SCHEMA_SQL
    ready_path: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def db_path(self) -> Path:
        return Path(self.path_fn())

    def connect(self) -> sqlite3.Connection:
        manager = DatabaseManager(self.db_path())
        return manager.connect()

    def ensure_ready(self) -> None:
        path = str(self.db_path().resolve())
        with self.lock:
            if self.ready_path == path and Path(path).exists():
                return
            with self.connect() as conn:
                conn.executescript(self.schema_sql)
                conn.commit()
            self.ready_path = path
