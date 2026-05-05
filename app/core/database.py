from __future__ import annotations

import json
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DatabaseStore:
    runtime: SQLiteRuntime
    sanitize_kit_name: Callable[[str], str]
    kit_path: Callable[[str], Path]
    default_kit_name: str
    storage_plan_summary: Callable[[dict[str, Any]], dict[str, Any]]
    storage_plan_arrays: Callable[[dict[str, Any]], list[dict[str, Any]]]

    def upsert_kit(self, cfg: dict[str, Any], conn: sqlite3.Connection | None = None) -> int:
        self.runtime.ensure_ready()
        owned = conn is None
        conn = conn or self.runtime.connect()
        try:
            now = _now_text()
            kit_name = self.sanitize_kit_name(cfg.get("site", {}).get("name", self.default_kit_name))
            config_path = str(self.kit_path(kit_name))
            row = conn.execute("SELECT id FROM kits WHERE name = ?", (kit_name,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE kits SET config_path = ?, updated_at = ? WHERE id = ?",
                    (config_path, now, int(row["id"])),
                )
                kit_id = int(row["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO kits(name, config_path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (kit_name, config_path, now, now),
                )
                kit_id = int(cur.lastrowid)
            if owned:
                conn.commit()
            return kit_id
        finally:
            if owned:
                conn.close()

    def find_host_id(
        self,
        conn: sqlite3.Connection,
        *,
        kit_id: int,
        system_serial: str = "",
        ilo_host: str = "",
    ) -> int | None:
        if system_serial:
            row = conn.execute(
                "SELECT id FROM hosts WHERE kit_id = ? AND system_serial = ? ORDER BY id DESC LIMIT 1",
                (kit_id, system_serial),
            ).fetchone()
            if row:
                return int(row["id"])
        if ilo_host:
            row = conn.execute(
                "SELECT id FROM hosts WHERE kit_id = ? AND ilo_host = ? ORDER BY id DESC LIMIT 1",
                (kit_id, ilo_host),
            ).fetchone()
            if row:
                return int(row["id"])
        return None

    def lookup_drive_rows(
        self,
        *,
        cfg: dict[str, Any],
        system_serial: str = "",
        ilo_host: str = "",
    ) -> dict[str, dict[str, Any]]:
        self.runtime.ensure_ready()
        with self.runtime.connect() as conn:
            kit_id = self.upsert_kit(cfg, conn=conn)
            host_id = self.find_host_id(conn, kit_id=kit_id, system_serial=system_serial, ilo_host=ilo_host)
            if not host_id:
                return {}
            rows = conn.execute(
                "SELECT redfish_path, controller_path, controller_name, bay, serial, capacity_gib, status, source, currently_seen "
                "FROM drives WHERE host_id = ?",
                (host_id,),
            ).fetchall()
            return {
                str(row["redfish_path"]): {
                    "path": str(row["redfish_path"]),
                    "controller_path": str(row["controller_path"] or ""),
                    "controller_name": str(row["controller_name"] or ""),
                    "bay": str(row["bay"] or ""),
                    "serial_number": str(row["serial"] or ""),
                    "size_gib": float(row["capacity_gib"] or 0),
                    "status": str(row["status"] or ""),
                    "source": str(row["source"] or ""),
                    "currently_seen": bool(row["currently_seen"]),
                }
                for row in rows
                if str(row["redfish_path"] or "").strip()
            }

    def record_run_history(self, cfg: dict[str, Any], entry: dict[str, Any]) -> None:
        self.runtime.ensure_ready()
        with self.runtime.connect() as conn:
            kit_id = self.upsert_kit(cfg, conn=conn)
            conn.execute(
                "INSERT INTO run_history(kit_id, scope, status, current_stage, run_bundle_dir, run_summary_path, event_time, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kit_id,
                    str(entry.get("scope") or ""),
                    str(entry.get("status") or ""),
                    str(entry.get("current_stage") or ""),
                    str(entry.get("run_bundle_dir") or ""),
                    str(entry.get("run_summary_path") or ""),
                    str(entry.get("time") or _now_text()),
                    _json_text(entry),
                ),
            )
            conn.commit()

    def record_known_issue_observation(
        self,
        cfg: dict[str, Any],
        *,
        fingerprint: str,
        title: str,
        description: str,
        message: str,
        discovery: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        status: str = "open",
    ) -> None:
        self.runtime.ensure_ready()
        summary = (discovery or {}).get("summary", {}) or {}
        source = (discovery or {}).get("raw", {}) or {}
        serial = str((summary.get("server", {}) or {}).get("serial_number") or (plan.get("source_discovery", {}) if isinstance(plan, dict) else {}).get("serial_number") or "").strip()
        ilo_host = str(source.get("source_host") or (plan.get("source_discovery", {}) if isinstance(plan, dict) else {}).get("host") or cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip()
        with self.runtime.connect() as conn:
            kit_id = self.upsert_kit(cfg, conn=conn)
            now = _now_text()
            issue_row = conn.execute("SELECT id FROM known_issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if issue_row:
                issue_id = int(issue_row["id"])
                conn.execute(
                    "UPDATE known_issues SET title = ?, description = ?, last_seen_at = ? WHERE id = ?",
                    (title, description, now, issue_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO known_issues(fingerprint, title, description, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                    (fingerprint, title, description, now, now),
                )
                issue_id = int(cur.lastrowid)
            host_id = self.find_host_id(conn, kit_id=kit_id, system_serial=serial, ilo_host=ilo_host)
            conn.execute(
                "INSERT INTO issue_observations(issue_id, kit_id, host_id, fingerprint, observed_at, status, message, context_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    kit_id,
                    host_id,
                    fingerprint,
                    now,
                    status,
                    message,
                    _json_text(
                        {
                            "serial_number": serial,
                            "ilo_host": ilo_host,
                            "plan_summary": self.storage_plan_summary(plan or {}) if plan else {},
                            "discovery_source_host": str(source.get("source_host") or ""),
                        }
                    ),
                ),
            )
            conn.commit()

    def persist_storage_plan(
        self,
        cfg: dict[str, Any],
        *,
        discovery: dict[str, Any],
        discovery_paths: dict[str, Path],
        plan: dict[str, Any],
        plan_paths: dict[str, Path],
        approved: bool,
    ) -> None:
        self.runtime.ensure_ready()
        summary = discovery.get("summary", {}) or {}
        source_host = str((discovery.get("raw", {}) or {}).get("source_host") or summary.get("source_host") or "").strip()
        serial = str((summary.get("server", {}) or {}).get("serial_number") or "").strip()
        with self.runtime.connect() as conn:
            kit_id = self.upsert_kit(cfg, conn=conn)
            host_id = self.find_host_id(conn, kit_id=kit_id, system_serial=serial, ilo_host=source_host)
            plan_path = str(plan_paths["plan"])
            discovery_raw_path = str(discovery_paths["raw"])
            now = _now_text()
            row = conn.execute("SELECT id, created_at FROM storage_plans WHERE plan_path = ?", (plan_path,)).fetchone()
            if row:
                plan_id = int(row["id"])
                conn.execute(
                    "UPDATE storage_plans SET kit_id = ?, host_id = ?, discovery_raw_path = ?, approved = ?, valid = ?, "
                    "os_controller_path = ?, data_controller_path = ?, hot_spare_path = ?, updated_at = ?, "
                    "plan_summary_json = ?, plan_json = ? WHERE id = ?",
                    (
                        kit_id,
                        host_id,
                        discovery_raw_path,
                        1 if approved else 0,
                        1 if bool(plan.get("valid")) else 0,
                        str((((plan.get("planned_layout") or {}).get("os_raid1") or {}).get("controller_path") or "")),
                        str((((plan.get("planned_layout") or {}).get("data_raid6") or {}).get("controller_path") or "")),
                        str((((plan.get("hot_spare") or {}).get("drive") or {}).get("path") or "")),
                        now,
                        _json_text(self.storage_plan_summary(plan)),
                        _json_text(plan),
                        plan_id,
                    ),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO storage_plans(kit_id, host_id, plan_path, discovery_raw_path, approved, valid, os_controller_path, "
                    "data_controller_path, hot_spare_path, created_at, updated_at, plan_summary_json, plan_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        kit_id,
                        host_id,
                        plan_path,
                        discovery_raw_path,
                        1 if approved else 0,
                        1 if bool(plan.get("valid")) else 0,
                        str((((plan.get("planned_layout") or {}).get("os_raid1") or {}).get("controller_path") or "")),
                        str((((plan.get("planned_layout") or {}).get("data_raid6") or {}).get("controller_path") or "")),
                        str((((plan.get("hot_spare") or {}).get("drive") or {}).get("path") or "")),
                        now,
                        now,
                        _json_text(self.storage_plan_summary(plan)),
                        _json_text(plan),
                    ),
                )
                plan_id = int(cur.lastrowid)
            conn.execute("DELETE FROM storage_plan_drives WHERE storage_plan_id = ?", (plan_id,))
            array_roles = [(str(array.get("role") or ""), list(array.get("drives") or [])) for array in self.storage_plan_arrays(plan)]
            hot_spare_drive = ((plan.get("hot_spare") or {}).get("drive") or {})
            if hot_spare_drive:
                array_roles.append(("hot_spare", [hot_spare_drive]))
            for role, drives in array_roles:
                for drive in drives:
                    conn.execute(
                        "INSERT INTO storage_plan_drives(storage_plan_id, role, drive_path, controller_path, bay, serial, capacity_gib) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            plan_id,
                            role,
                            str(drive.get("path") or drive.get("drive_path") or ""),
                            str(drive.get("controller_path") or ""),
                            str(drive.get("bay") or ""),
                            str(drive.get("serial_number") or drive.get("serial") or ""),
                            float(drive.get("size_gib") or drive.get("capacity") or 0),
                        ),
                    )
            conn.commit()

    def persist_inventory(
        self,
        cfg: dict[str, Any],
        *,
        source_host: str,
        server_summary: dict[str, Any],
        manager_summary: dict[str, Any],
        controllers: list[dict[str, Any]],
        drives: list[dict[str, Any]],
        inventory_kind: str,
        raw_summary: dict[str, Any] | None = None,
    ) -> None:
        self.runtime.ensure_ready()
        serial = str(server_summary.get("serial_number") or "").strip()
        with self.runtime.connect() as conn:
            kit_id = self.upsert_kit(cfg, conn=conn)
            now = _now_text()
            host_id = self.find_host_id(conn, kit_id=kit_id, system_serial=serial, ilo_host=source_host)
            if host_id:
                conn.execute(
                    "UPDATE hosts SET ilo_host = ?, system_serial = ?, server_model = ?, product_name = ?, manager_model = ?, "
                    "last_inventory_kind = ?, currently_seen = 1, last_seen_at = ?, raw_summary_json = ? WHERE id = ?",
                    (
                        source_host,
                        serial,
                        str(server_summary.get("model") or ""),
                        str(server_summary.get("product_name") or ""),
                        str(manager_summary.get("model") or ""),
                        inventory_kind,
                        now,
                        _json_text(raw_summary or {}),
                        host_id,
                    ),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO hosts(kit_id, ilo_host, system_serial, server_model, product_name, manager_model, last_inventory_kind, currently_seen, last_seen_at, raw_summary_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        kit_id,
                        source_host,
                        serial,
                        str(server_summary.get("model") or ""),
                        str(server_summary.get("product_name") or ""),
                        str(manager_summary.get("model") or ""),
                        inventory_kind,
                        now,
                        _json_text(raw_summary or {}),
                    ),
                )
                host_id = int(cur.lastrowid)

            seen_controller_paths = [str(item.get("path") or "").strip() for item in controllers if str(item.get("path") or "").strip()]
            controller_id_by_path: dict[str, int] = {}
            for item in controllers:
                controller_path = str(item.get("path") or "").strip()
                if not controller_path:
                    continue
                row = conn.execute(
                    "SELECT id FROM controllers WHERE host_id = ? AND redfish_path = ?",
                    (host_id, controller_path),
                ).fetchone()
                values = (
                    str(item.get("name") or ""),
                    str(item.get("model") or ""),
                    str(item.get("firmware_version") or ""),
                    str(item.get("source") or ""),
                    str(item.get("status") or ""),
                    now,
                    _json_text(item),
                )
                if row:
                    controller_id = int(row["id"])
                    conn.execute(
                        "UPDATE controllers SET name = ?, model = ?, firmware_version = ?, source = ?, status = ?, currently_seen = 1, last_seen_at = ?, raw_json = ? "
                        "WHERE id = ?",
                        (*values, controller_id),
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO controllers(host_id, redfish_path, name, model, firmware_version, source, status, currently_seen, last_seen_at, raw_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (host_id, controller_path, *values),
                    )
                    controller_id = int(cur.lastrowid)
                controller_id_by_path[controller_path] = controller_id
            if seen_controller_paths:
                placeholders = ",".join("?" for _ in seen_controller_paths)
                conn.execute(
                    f"UPDATE controllers SET currently_seen = 0 WHERE host_id = ? AND redfish_path NOT IN ({placeholders})",
                    (host_id, *seen_controller_paths),
                )

            seen_drive_paths = [str(item.get("path") or item.get("drive_path") or "").strip() for item in drives if str(item.get("path") or item.get("drive_path") or "").strip()]
            for item in drives:
                drive_path = str(item.get("path") or item.get("drive_path") or "").strip()
                if not drive_path:
                    continue
                controller_path = str(item.get("controller_path") or "").strip()
                row = conn.execute(
                    "SELECT id FROM drives WHERE host_id = ? AND redfish_path = ?",
                    (host_id, drive_path),
                ).fetchone()
                values = (
                    controller_id_by_path.get(controller_path),
                    controller_path,
                    str(item.get("controller_name") or ""),
                    str(item.get("bay") or ""),
                    str(item.get("serial_number") or item.get("serial") or ""),
                    float(item.get("size_gib") or item.get("capacity") or 0),
                    str(item.get("media_type") or ""),
                    str(item.get("protocol") or ""),
                    str(item.get("status") or ""),
                    str(item.get("source") or ""),
                    now,
                    _json_text(item),
                )
                if row:
                    conn.execute(
                        "UPDATE drives SET controller_id = ?, controller_path = ?, controller_name = ?, bay = ?, serial = ?, capacity_gib = ?, "
                        "media_type = ?, protocol = ?, status = ?, source = ?, currently_seen = 1, last_seen_at = ?, raw_json = ? WHERE id = ?",
                        (*values, int(row["id"])),
                    )
                else:
                    conn.execute(
                        "INSERT INTO drives(host_id, controller_id, redfish_path, controller_path, controller_name, bay, serial, capacity_gib, media_type, protocol, status, source, currently_seen, last_seen_at, raw_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (host_id, values[0], drive_path, *values[1:]),
                    )
            if seen_drive_paths:
                placeholders = ",".join("?" for _ in seen_drive_paths)
                conn.execute(
                    f"UPDATE drives SET currently_seen = 0 WHERE host_id = ? AND redfish_path NOT IN ({placeholders})",
                    (host_id, *seen_drive_paths),
                )
            conn.commit()
