# -*- coding: utf-8 -*-
"""Safely reset legacy memory and research notes after deployment.

The script never drops tables, never touches accounts, conversations, files,
session evidence, tool results, source ledgers, caches, keys, or backups.
It supports ``--dry-run`` and makes a consistent SQLite backup before changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


SESSION_TABLES = (
    "account_preferences",
    "ltm_facts",
    "long_term_memory",
    "account_resident_cards",
    "account_memory_retry_queue",
    "resident_cards",
)
ARTIFACT_TABLES = ("research_notes",)
MIGRATIONS = ("session_preferences_v1", "resident_cards_v1")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(path: Path, output_dir: Path) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = output_dir / f"{path.name}.backup_{stamp}.sqlite3"
    with sqlite3.connect(str(path)) as source, sqlite3.connect(str(destination)) as target:
        source.backup(target)
    return destination, sha256(destination)


def table_counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return {table: 0 for table in tables}
    with sqlite3.connect(str(path)) as conn:
        for table in tables:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if columns:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            else:
                counts[table] = 0
    return counts


def clean_database(path: Path, *, dry_run: bool) -> dict:
    tables = SESSION_TABLES if path.name == "sessions.db" else ARTIFACT_TABLES
    before = table_counts(path, tables)
    deleted: dict[str, int] = {table: 0 for table in tables}
    if path.exists() and not dry_run:
        with sqlite3.connect(str(path)) as conn:
            for table in tables:
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if not columns:
                    continue
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(f"DELETE FROM {table}")
                deleted[table] = int(cur.rowcount or 0)
                conn.commit()
    after = table_counts(path, tables)
    return {
        "database": str(path),
        "tables": list(tables),
        "before": before,
        "deleted": deleted,
        "after": after,
    }


def ensure_migration_markers(path: Path, *, dry_run: bool) -> dict:
    result = {"database": str(path), "migrations": {}, "created": []}
    if not path.exists():
        return result
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS account_memory_migrations (
                migration_key TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL
            )"""
        )
        for key in MIGRATIONS:
            exists = conn.execute(
                "SELECT 1 FROM account_memory_migrations WHERE migration_key=?",
                (key,),
            ).fetchone()
            if exists:
                result["migrations"][key] = "already-present"
                continue
            if not dry_run:
                conn.execute(
                    "INSERT INTO account_memory_migrations(migration_key,completed_at) VALUES(?,?)",
                    (key, datetime.now().isoformat()),
                )
                conn.commit()
            result["migrations"][key] = "created-if-not-dry-run"
            result["created"].append(key)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("agent/state"))
    parser.add_argument("--backup-dir", type=Path, default=Path("agent/state/_memory_reset_backups"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sessions_db = args.state_dir / "sessions.db"
    artifacts_db = args.state_dir / "research_artifacts.sqlite3"
    report = {
        "timestamp": datetime.now().isoformat(),
        "dry_run": bool(args.dry_run),
        "backups": {},
        "cleanup": [],
        "migrations": {},
    }
    if not args.dry_run:
        for source in (sessions_db, artifacts_db):
            if source.exists():
                path, digest = backup_database(source, args.backup_dir)
                report["backups"][str(source)] = {"path": str(path), "sha256": digest}
    report["cleanup"].append(clean_database(sessions_db, dry_run=args.dry_run))
    report["cleanup"].append(clean_database(artifacts_db, dry_run=args.dry_run))
    report["migrations"] = ensure_migration_markers(sessions_db, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
