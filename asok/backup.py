"""
Database backup and disaster recovery manager for the Asok framework.

Provides isolated database backup (cross-engine replication e.g. Postgres -> SQLite)
and restoration capabilities without exposing the backup database to application traffic.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from asok.orm.engines import get_engine
from asok.orm.model import MODELS_REGISTRY, Model
from asok.orm.utils import _pluralize
from asok.scheduler import ScheduledTask

logger = logging.getLogger("asok.backup")


def _get_model_tablename(model: Any) -> str:
    tbl = getattr(model, "__tablename__", None)
    if tbl:
        return tbl
    return _pluralize(model.__name__)


def _get_model_field_names(model: Any) -> list[str]:
    fields = []
    if "id" not in model._fields:
        fields.append("id")
    fields.extend(list(model._fields.keys()))
    return fields


def _resolve_app_backup_url(app: Any) -> Optional[str]:
    if app and hasattr(app, "config"):
        val = app.config.get("DATABASE_BACKUP_URL")
        if val:
            return str(val).strip()
    return None


def _resolve_app_backup_path(app: Any) -> Optional[str]:
    if app and hasattr(app, "config"):
        val = app.config.get("DATABASE_BACKUP_PATH")
        if val:
            return str(val).strip()
    return None


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _set_restricted_file_perm(path: Optional[str], mode: int = 0o600) -> None:
    if path:
        try:
            if os.path.exists(path):
                os.chmod(path, mode)
        except Exception:
            pass


def _set_restricted_dir_perm(path: str, mode: int = 0o700) -> None:
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
        os.chmod(path, mode)
    except Exception:
        pass


class BackupManager:
    """Manages database backups, status, and disaster recovery restoration."""

    _scheduled_task: Optional[ScheduledTask] = None

    @classmethod
    def _resolve_custom_path_target(cls, path: str, url: Optional[str]) -> str:
        filename = os.path.basename(url) if url else "backup.sqlite3"
        if not filename or filename.endswith(os.sep):
            filename = "backup.sqlite3"
        target = os.path.join(path, filename)
        _set_restricted_dir_perm(os.path.dirname(target), 0o700)
        return target

    @classmethod
    def _resolve_default_backup_url(cls, url: Optional[str]) -> str:
        if url:
            return url.strip()
        _set_restricted_dir_perm("backups", 0o700)
        return "backups/backup.sqlite3"

    @classmethod
    def get_backup_url(cls, app: Any = None) -> str:
        """Resolve backup database DSN/URL from app config or environment variables."""
        url = _resolve_app_backup_url(app) or os.getenv("DATABASE_BACKUP_URL")
        path = _resolve_app_backup_path(app) or os.getenv("DATABASE_BACKUP_PATH")
        if path and os.path.isabs(path):
            return cls._resolve_custom_path_target(path, url)
        return cls._resolve_default_backup_url(url)

    @classmethod
    def _get_target_file_path(cls, db_url: str) -> Optional[str]:
        if db_url.startswith("sqlite:///"):
            return db_url[len("sqlite:///"):]
        return db_url if "://" not in db_url else None

    @classmethod
    def _collect_registry_models(cls, models: list, seen: set) -> None:
        for m in list(MODELS_REGISTRY.values()):
            if isinstance(m, type) and issubclass(m, Model) and m not in seen:
                models.append(m)
                seen.add(m)

    @classmethod
    def _collect_subclass_models(cls, models: list, seen: set) -> None:
        for m in Model.__subclasses__():
            if m not in seen and getattr(m, "__name__", "") not in ("Model", "BaseModel"):
                models.append(m)
                seen.add(m)

    @classmethod
    def _ensure_models_loaded(cls) -> None:
        if MODELS_REGISTRY:
            return
        try:
            from asok.cli.database import _find_project_root, _load_models
            root = _find_project_root()
            if root:
                _load_models(root)
        except Exception:
            pass

    @classmethod
    def get_models(cls) -> List[Type[Model]]:
        """Collect all registered Model classes."""
        cls._ensure_models_loaded()
        models: List[Type[Model]] = []
        seen: set = set()
        cls._collect_registry_models(models, seen)
        cls._collect_subclass_models(models, seen)
        return models

    @classmethod
    def _create_backup_schema(cls, backup_engine: Any, table_name: str, model: Any) -> bool:
        try:
            model._execute_create_table(backup_engine)
            model._migrate_missing_columns(backup_engine)
            return True
        except Exception:
            return False

    @classmethod
    def _insert_batch(cls, backup_engine: Any, table_name: str, fields: list[str], rows: list[dict]) -> None:
        try:
            tbl_q = backup_engine.quote_identifier(table_name)
            quoted_fields = [backup_engine.quote_identifier(f) for f in fields]
            placeholders = ", ".join(["?"] * len(fields))
            insert_sql = f"INSERT OR REPLACE INTO {tbl_q} (" + ", ".join(quoted_fields) + f") VALUES ({placeholders});"

            with backup_engine.transaction():
                for r in rows:
                    values = [r.get(f) for f in fields]
                    backup_engine.execute(insert_sql, values)
        except Exception as e:
            logger.warning("Error inserting batch into backup table %s: %s", table_name, e)

    @classmethod
    def _read_primary_batch(cls, primary_engine: Any, tbl_q: str, batch_size: int, offset: int) -> list[dict]:
        try:
            return primary_engine.execute(f"SELECT * FROM {tbl_q} LIMIT {batch_size} OFFSET {offset};") or []
        except Exception:
            return []

    @classmethod
    def _backup_one_model(cls, backup_engine: Any, model: Any) -> int:
        try:
            table_name = _get_model_tablename(model)
            primary_engine = model.get_engine(op="read")
            tbl_q = primary_engine.quote_identifier(table_name)
            primary_engine.execute(f"SELECT * FROM {tbl_q} LIMIT 1 OFFSET 0;")
        except Exception:
            return 0

        if not cls._create_backup_schema(backup_engine, table_name, model):
            return 0

        offset, batch_size, model_rows = 0, 500, 0
        field_names = _get_model_field_names(model)

        while True:
            rows = cls._read_primary_batch(primary_engine, tbl_q, batch_size, offset)
            if not rows:
                break
            offset += len(rows)
            model_rows += len(rows)
            cls._insert_batch(backup_engine, table_name, field_names, rows)

        return model_rows

    @classmethod
    def _backup_all_models(cls, backup_engine: Any, models: list) -> tuple[dict, int]:
        table_stats, total_rows = {}, 0
        for m in models:
            t_name = _get_model_tablename(m)
            try:
                c = cls._backup_one_model(backup_engine, m)
                table_stats[t_name] = c
                total_rows += c
            except Exception as model_err:
                logger.warning("Skipping model %s: %s", t_name, model_err)
        return table_stats, total_rows

    @classmethod
    def _get_snapshots_dir(cls, base_url: str) -> str:
        target_file = cls._get_target_file_path(base_url)
        if target_file:
            parent = os.path.dirname(os.path.abspath(target_file))
            snapshots_dir = os.path.join(parent, "snapshots")
        else:
            snapshots_dir = os.path.abspath("backups/snapshots")
        _set_restricted_dir_perm(snapshots_dir, 0o700)
        return snapshots_dir

    @classmethod
    def _read_snapshot_meta(cls, snaps_dir: str, fname: str) -> Optional[dict]:
        meta_path = os.path.join(snaps_dir, fname)
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                db_file = meta_path[:-len(".meta.json")]
                snap_id = meta.get("snapshot_id") or os.path.basename(db_file).replace(".sqlite3", "")
                size = os.path.getsize(db_file) if os.path.exists(db_file) else 0
                meta["snapshot_id"] = snap_id
                meta["file_path"] = db_file
                meta["file_size_human"] = _format_file_size(size)
                ts = meta.get("timestamp", 0)
                meta["datetime_str"] = (
                    datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
                )
                return meta
        except Exception:
            return None

    @classmethod
    def _read_if_meta_file(cls, snaps_dir: str, fname: str) -> Optional[dict]:
        if fname.endswith(".meta.json"):
            return cls._read_snapshot_meta(snaps_dir, fname)
        return None

    @classmethod
    def _collect_snapshot_metas(cls, snaps_dir: str) -> list[dict]:
        if not os.path.exists(snaps_dir):
            return []
        metas = [cls._read_if_meta_file(snaps_dir, f) for f in os.listdir(snaps_dir)]
        return [m for m in metas if m]

    @classmethod
    def list_snapshots(cls, app: Any = None, target_url: Optional[str] = None) -> list[dict]:
        """List all point-in-time recovery snapshots sorted by timestamp descending."""
        url = target_url or cls.get_backup_url(app)
        snapshots = cls._collect_snapshot_metas(cls._get_snapshots_dir(url))
        snapshots.sort(key=lambda s: s.get("timestamp", 0), reverse=True)
        return snapshots

    @classmethod
    def _get_config_max_snapshots(cls, app: Any) -> Optional[int]:
        if app and hasattr(app, "config"):
            return app.config.get("DATABASE_BACKUP_MAX_SNAPSHOTS")
        return None

    @classmethod
    def _resolve_max_snapshots(cls, app: Any, max_snapshots: Optional[int]) -> int:
        if max_snapshots is not None:
            return max_snapshots
        conf_val = cls._get_config_max_snapshots(app)
        if conf_val is not None:
            return conf_val
        try:
            return int(os.getenv("DATABASE_BACKUP_MAX_SNAPSHOTS", "10"))
        except ValueError:
            return 10

    @classmethod
    def _delete_file_if_exists(cls, file_path: Optional[str]) -> None:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

    @classmethod
    def _delete_snapshot_files(cls, old_snap: dict) -> bool:
        db_path = old_snap.get("file_path")
        if not db_path:
            return False
        meta_path = db_path + ".meta.json"
        try:
            cls._delete_file_if_exists(db_path)
            cls._delete_file_if_exists(meta_path)
            cls._delete_file_if_exists(db_path + "-shm")
            cls._delete_file_if_exists(db_path + "-wal")
            return True
        except Exception as e:
            logger.warning("Error deleting old snapshot %s: %s", old_snap.get("snapshot_id"), e)
            return False

    @classmethod
    def cleanup_old_snapshots(
        cls, app: Any = None, target_url: Optional[str] = None, max_snapshots: Optional[int] = None
    ) -> int:
        """Cleanup old snapshots to enforce retention policy."""
        limit = cls._resolve_max_snapshots(app, max_snapshots)
        if limit <= 0:
            return 0

        snapshots = cls.list_snapshots(app, target_url)
        if len(snapshots) <= limit:
            return 0

        return sum(1 for snap in snapshots[limit:] if cls._delete_snapshot_files(snap))

    @classmethod
    def _prepare_backup_target(cls, base_url: str) -> tuple[Optional[str], str, Optional[str], str]:
        target_file = cls._get_target_file_path(base_url)
        snap_tag = time.strftime("%Y%m%d_%H%M%S")
        snap_id = f"backup_{snap_tag}"

        if not target_file:
            return None, base_url, None, snap_id

        snaps_dir = cls._get_snapshots_dir(base_url)
        snap_filename = f"{snap_id}.sqlite3"
        snap_file_path = os.path.join(snaps_dir, snap_filename)
        return target_file, f"sqlite:///{snap_file_path}", snap_file_path, snap_id

    @classmethod
    def _sync_latest_backup(
        cls, target_file: Optional[str], snap_file_path: Optional[str], base_url: str, result: dict
    ) -> None:
        if target_file and snap_file_path and os.path.exists(snap_file_path):
            os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
            shutil.copy2(snap_file_path, target_file)
            _set_restricted_file_perm(target_file, 0o600)
        cls._save_backup_metadata(base_url, result)

    @classmethod
    def perform_backup(
        cls, app: Any = None, target_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a full database backup with point-in-time snapshot creation."""
        start_time = time.time()
        base_url = target_url or cls.get_backup_url(app)
        target_file, snap_url, snap_file_path, snap_id = cls._prepare_backup_target(base_url)

        backup_engine = get_engine(snap_url)
        models = cls.get_models()
        result = {}

        try:
            table_stats, total_rows = cls._backup_all_models(backup_engine, models)
            duration = round(time.time() - start_time, 3)
            result = {
                "success": True,
                "timestamp": int(time.time()),
                "snapshot_id": snap_id,
                "target_url": snap_url,
                "duration_seconds": duration,
                "total_tables": len(models),
                "total_rows": total_rows,
                "tables": table_stats,
            }
            cls._save_backup_metadata(snap_url, result)
            _set_restricted_file_perm(snap_file_path, 0o600)
            return result
        except Exception as err:
            return {"success": False, "error": str(err), "timestamp": int(time.time()), "target_url": base_url}
        finally:
            backup_engine.close_connections()
            if result.get("success"):
                cls._sync_latest_backup(target_file, snap_file_path, base_url, result)
            cls.cleanup_old_snapshots(app, base_url)

    @classmethod
    def _fetch_backup_rows(cls, backup_engine: Any, table_name: str) -> list[dict]:
        try:
            tbl_q = backup_engine.quote_identifier(table_name)
            return backup_engine.execute(f"SELECT * FROM {tbl_q};") or []
        except Exception:
            return []

    @classmethod
    def _execute_restore_transaction(
        cls, primary_engine: Any, table_name: str, field_names: list[str], backup_rows: list[dict]
    ) -> int:
        tbl_q = primary_engine.quote_identifier(table_name)
        quoted_fields = [primary_engine.quote_identifier(f) for f in field_names]
        placeholders = ", ".join(["?"] * len(field_names))
        insert_sql = f"INSERT OR REPLACE INTO {tbl_q} (" + ", ".join(quoted_fields) + f") VALUES ({placeholders});"

        try:
            with primary_engine.transaction():
                for r in backup_rows:
                    values = [r.get(f) for f in field_names]
                    primary_engine.execute(insert_sql, values)
            return len(backup_rows)
        except Exception as e:
            logger.warning("Error restoring table %s: %s", table_name, e)
            return 0

    @classmethod
    def _restore_one_model(cls, backup_engine: Any, model: Any) -> int:
        table_name = _get_model_tablename(model)
        backup_rows = cls._fetch_backup_rows(backup_engine, table_name)
        if not backup_rows:
            return 0

        primary_engine = model.get_engine(op="write")
        try:
            model._execute_create_table(primary_engine)
            model._migrate_missing_columns(primary_engine)
        except Exception:
            pass

        field_names = _get_model_field_names(model)
        return cls._execute_restore_transaction(primary_engine, table_name, field_names, backup_rows)

    @classmethod
    def _validate_restore_file(cls, url: str) -> Optional[dict]:
        target_file = cls._get_target_file_path(url)
        if target_file and not os.path.exists(target_file):
            return {"success": False, "error": f"Backup file not found at '{target_file}'", "timestamp": int(time.time())}
        return None

    @classmethod
    def _restore_all_models(cls, backup_engine: Any, models: list) -> tuple[dict, int]:
        table_stats, total_rows = {}, 0
        for m in models:
            t_name = _get_model_tablename(m)
            c = cls._restore_one_model(backup_engine, m)
            if c > 0:
                table_stats[t_name] = c
                total_rows += c
        return table_stats, total_rows

    @classmethod
    def _find_snapshot_url_by_id(cls, snaps_dir: str, snapshot_id: str) -> Optional[str]:
        cand = os.path.join(snaps_dir, snapshot_id)
        if not cand.endswith(".sqlite3"):
            cand = f"{cand}.sqlite3"
        if os.path.exists(cand):
            return f"sqlite:///{cand}"
        return snapshot_id if os.path.exists(snapshot_id) else None

    @classmethod
    def _get_latest_snapshot_url(cls, app: Any, url: str) -> str:
        snaps = cls.list_snapshots(app, url)
        if snaps and snaps[0].get("file_path") and os.path.exists(snaps[0]["file_path"]):
            return f"sqlite:///{snaps[0]['file_path']}"
        return url

    @classmethod
    def _resolve_snapshot_restore_url(cls, app: Any, url: str, snapshot_id: Optional[str]) -> str:
        if snapshot_id:
            found = cls._find_snapshot_url_by_id(cls._get_snapshots_dir(url), snapshot_id)
            return found or url
        return cls._get_latest_snapshot_url(app, url)

    @classmethod
    def perform_restore(
        cls, app: Any = None, source_url: Optional[str] = None, snapshot_id: Optional[str] = None, force: bool = False
    ) -> Dict[str, Any]:
        """Restore data from a backup or specific point-in-time snapshot into primary DB."""
        start_time = time.time()
        base_url = source_url or cls.get_backup_url(app)
        url = cls._resolve_snapshot_restore_url(app, base_url, snapshot_id)

        err_res = cls._validate_restore_file(url)
        if err_res:
            return err_res

        backup_engine = get_engine(url)
        models = cls.get_models()

        try:
            table_stats, total_rows = cls._restore_all_models(backup_engine, models)
            duration = round(time.time() - start_time, 3)
            return {
                "success": True,
                "timestamp": int(time.time()),
                "source_url": url,
                "duration_seconds": duration,
                "total_tables": len(table_stats),
                "total_rows": total_rows,
                "tables": table_stats,
            }
        except Exception as err:
            return {"success": False, "error": str(err), "timestamp": int(time.time()), "source_url": url}
        finally:
            backup_engine.close_connections()

    @classmethod
    def _get_file_info(cls, target_file: Optional[str]) -> tuple[bool, int]:
        if not target_file:
            return True, 0
        exists = os.path.exists(target_file)
        size = os.path.getsize(target_file) if exists else 0
        return exists, size

    @classmethod
    def get_backup_status(
        cls, app: Any = None, target_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return status and metadata of the database backup system."""
        url = target_url or cls.get_backup_url(app)
        meta = cls._load_backup_metadata(url)
        snapshots = cls.list_snapshots(app, url)

        file_exists = False
        size = 0
        if snapshots:
            file_exists = True
            latest_path = snapshots[0].get("file_path")
            if latest_path and os.path.exists(latest_path):
                size = os.path.getsize(latest_path)
        else:
            target_file = cls._get_target_file_path(url)
            file_exists, size = cls._get_file_info(target_file)

        return {
            "target_url": url,
            "file_exists": file_exists,
            "file_size_bytes": size,
            "file_size_human": _format_file_size(size),
            "last_backup": meta,
            "total_snapshots": len(snapshots),
            "snapshots": snapshots,
        }

    @classmethod
    def _meta_file_path(cls, db_url: str) -> str:
        target_file = cls._get_target_file_path(db_url)
        return target_file + ".meta.json" if target_file else "backups/backup.meta.json"

    @classmethod
    def _save_backup_metadata(cls, db_url: str, result: Dict[str, Any]) -> None:
        meta_path = cls._meta_file_path(db_url)
        os.makedirs(os.path.dirname(os.path.abspath(meta_path)), exist_ok=True)
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            _set_restricted_file_perm(meta_path, 0o600)
        except Exception:
            pass

    @classmethod
    def _load_backup_metadata(cls, db_url: str) -> Optional[Dict[str, Any]]:
        meta_path = cls._meta_file_path(db_url)
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @classmethod
    def _get_schedule_interval(cls, app: Any) -> tuple[bool, Optional[str]]:
        enabled = True
        interval = None
        if app and hasattr(app, "config"):
            enabled = bool(app.config.get("DATABASE_BACKUP_ENABLED", True))
            interval = app.config.get("DATABASE_BACKUP_INTERVAL")
        if not interval:
            interval = os.getenv("DATABASE_BACKUP_INTERVAL")
        return enabled, interval

    @classmethod
    def start_scheduled_backups(cls, app: Any = None) -> Optional[ScheduledTask]:
        """Start recurring background backup schedule if configured."""
        if cls._scheduled_task:
            return cls._scheduled_task

        enabled, interval = cls._get_schedule_interval(app)
        if not enabled or not interval:
            return None

        def _job():
            cls.perform_backup(app)

        cls._scheduled_task = ScheduledTask(interval=interval, fn=_job, run_initial=True)
        return cls._scheduled_task
