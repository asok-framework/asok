"""
CLI commands for database backup management and disaster recovery restoration.
"""

from __future__ import annotations

import argparse
import sys
import time

from asok.backup import BackupManager
from asok.cli.style import Style


def _ensure_cli_models_loaded() -> None:
    try:
        from asok.cli.database import _find_project_root, _load_models
        root = _find_project_root()
        if root:
            _load_models(root)
    except Exception:
        pass


def handle_backup_command(args: argparse.Namespace) -> None:
    """Route CLI backup subcommand to appropriate handler."""
    _ensure_cli_models_loaded()
    subcmd = getattr(args, "backup_subcommand", "status")

    if subcmd == "run":
        run_backup(args)
    elif subcmd == "list":
        run_list(args)
    elif subcmd == "restore":
        run_restore(args)
    else:
        run_status(args)


def run_backup(args: argparse.Namespace) -> None:
    """Execute an immediate database backup with snapshot creation."""
    target = getattr(args, "target", None)
    Style.info("Starting database backup...")

    res = BackupManager.perform_backup(target_url=target)

    if res.get("success"):
        Style.success(
            f"Backup completed successfully in {res.get('duration_seconds')}s!"
        )
        Style.info(f"Target: {res.get('target_url')}")
        if res.get("snapshot_id"):
            Style.info(f"Snapshot ID: {res.get('snapshot_id')}")
        Style.info(f"Tables: {res.get('total_tables')} | Total rows: {res.get('total_rows')}")
    else:
        Style.error(f"Backup failed: {res.get('error')}")
        sys.exit(1)


def run_list(args: argparse.Namespace) -> None:
    """List available point-in-time recovery snapshots."""
    target = getattr(args, "target", None)
    snapshots = BackupManager.list_snapshots(target_url=target)

    Style.heading("=== Available Point-in-Time Recovery Snapshots ===")
    if not snapshots:
        Style.warn("No snapshots found.")
        return

    for idx, snap in enumerate(snapshots, start=1):
        s_id = snap.get("snapshot_id")
        d_str = snap.get("datetime_str")
        size = snap.get("file_size_human")
        rows = snap.get("total_rows", 0)
        tables = snap.get("total_tables", 0)
        tag = "[LATEST]" if idx == 1 else ""

        print(f" {idx}. {Style.BOLD}{s_id}{Style.RESET} {Style.GREEN}{tag}{Style.RESET}")
        print(f"    Date: {d_str} | Size: {size} | Tables: {tables} | Total Rows: {rows}")
        print()


def run_restore(args: argparse.Namespace) -> None:
    """Restore database from backup or specific snapshot (Disaster Recovery)."""
    source = getattr(args, "source", None)
    snapshot = getattr(args, "snapshot", None)
    force = getattr(args, "force", False)

    if not force:
        target_name = f"snapshot '{snapshot}'" if snapshot else "latest backup"
        Style.warn(f"WARNING: This will overwrite data in primary database with {target_name}!")
        if not Style.confirm("Are you sure you want to proceed?", default=False):
            Style.info("Restoration cancelled.")
            return

    Style.info("Starting database restoration from backup...")
    res = BackupManager.perform_restore(source_url=source, snapshot_id=snapshot, force=force)

    if res.get("success"):
        Style.success(
            f"Database restored successfully in {res.get('duration_seconds')}s!"
        )
        Style.info(f"Source: {res.get('source_url')}")
        Style.info(f"Restored tables: {res.get('total_tables')} | Total rows: {res.get('total_rows')}")
    else:
        Style.error(f"Restoration failed: {res.get('error')}")
        sys.exit(1)


def run_status(args: argparse.Namespace) -> None:
    """Show current backup status and metadata."""
    target = getattr(args, "target", None)
    status = BackupManager.get_backup_status(target_url=target)

    Style.heading("=== Asok Database Backup Status ===")
    print(f"Target DSN/File: {status['target_url']}")
    print(f"File Exists:     {'Yes' if status['file_exists'] else 'No'}")
    print(f"File Size:       {status['file_size_human']}")
    print(f"Total Snapshots: {status.get('total_snapshots', 0)}")

    last = status.get("last_backup")
    if last:
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.get("timestamp", 0)))
        print(f"Last Backup:     {ts_str}")
        print(f"Duration:        {last.get('duration_seconds')}s")
        print(f"Total Rows:      {last.get('total_rows')}")
        print("\nBacked up tables:")
        for tbl, count in last.get("tables", {}).items():
            print(f"  - {tbl}: {count} rows")
    else:
        Style.warn("No previous backup metadata found.")
