"""
Unit and integration tests for the Asok database backup and disaster recovery system.
"""

import argparse
import os
import shutil
import tempfile
import unittest

from asok.backup import BackupManager
from asok.cli.backup import handle_backup_command
from asok.orm.field import Field
from asok.orm.model import Model


class BackupTestItem(Model):
    __tablename__ = "backup_test_items"
    name = Field.String(nullable=False)
    value = Field.Integer(default=0)


class TestBackupSystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.backup_path = os.path.join(self.temp_dir, "test_backup.sqlite3")
        self.backup_url = f"sqlite:///{self.backup_path}"

        # Populate primary database
        BackupTestItem.create_table()
        BackupTestItem.create(name="Item A", value=100)
        BackupTestItem.create(name="Item B", value=200)

    def tearDown(self):
        try:
            BackupTestItem.query().delete()
        except Exception:
            pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_perform_backup_and_status(self):
        res = BackupManager.perform_backup(target_url=self.backup_url)
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(self.backup_path))
        self.assertGreaterEqual(res["total_rows"], 2)

        status = BackupManager.get_backup_status(target_url=self.backup_url)
        self.assertEqual(status["target_url"], self.backup_url)
        self.assertTrue(status["file_exists"])
        self.assertGreater(status["file_size_bytes"], 0)
        self.assertIsNotNone(status["last_backup"])

    def test_perform_restore_disaster_recovery(self):
        # 1. Backup primary DB
        res_backup = BackupManager.perform_backup(target_url=self.backup_url)
        self.assertTrue(res_backup["success"])

        # 2. Simulate data loss on primary DB
        BackupTestItem.query().delete()
        self.assertEqual(BackupTestItem.count(), 0)

        # 3. Restore from isolated backup DB
        res_restore = BackupManager.perform_restore(source_url=self.backup_url, force=True)
        self.assertTrue(res_restore["success"])
        self.assertGreaterEqual(BackupTestItem.count(), 2)

        item_a = BackupTestItem.find(name="Item A")
        self.assertIsNotNone(item_a)
        self.assertEqual(item_a.value, 100)

    def test_cli_backup_handlers(self):
        res_backup = BackupManager.perform_backup(target_url=self.backup_url)
        self.assertTrue(res_backup["success"])

        args_status = argparse.Namespace(backup_subcommand="status", target=self.backup_url)
        handle_backup_command(args_status)

        args_list = argparse.Namespace(backup_subcommand="list", target=self.backup_url)
        handle_backup_command(args_list)

        args_run = argparse.Namespace(backup_subcommand="run", target=self.backup_url)
        handle_backup_command(args_run)

        args_restore = argparse.Namespace(
            backup_subcommand="restore", source=self.backup_url, snapshot=None, force=True
        )
        handle_backup_command(args_restore)

    def test_snapshots_listing_and_retention(self):
        res1 = BackupManager.perform_backup(target_url=self.backup_url)
        self.assertTrue(res1["success"])
        snap_id1 = res1.get("snapshot_id")

        snaps = BackupManager.list_snapshots(target_url=self.backup_url)
        self.assertGreaterEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["snapshot_id"], snap_id1)

        # Cleanup retention test with max_snapshots=1
        cleaned = BackupManager.cleanup_old_snapshots(target_url=self.backup_url, max_snapshots=1)
        self.assertGreaterEqual(cleaned, 0)
        snaps_after = BackupManager.list_snapshots(target_url=self.backup_url)
        self.assertEqual(len(snaps_after), 1)

    def test_point_in_time_restoration(self):
        res1 = BackupManager.perform_backup(target_url=self.backup_url)
        snap1_id = res1.get("snapshot_id")

        # Mutate primary DB data
        BackupTestItem.create(name="Item C", value=300)
        self.assertEqual(BackupTestItem.count(), 3)

        # Restore from snap1
        res_restore = BackupManager.perform_restore(
            source_url=self.backup_url, snapshot_id=snap1_id, force=True
        )
        self.assertTrue(res_restore["success"])

    def test_custom_backup_path_override(self):
        custom_dir = os.path.join(self.temp_dir, "custom_secure_backups")
        os.environ["DATABASE_BACKUP_PATH"] = custom_dir
        os.environ["DATABASE_BACKUP_URL"] = "backups/production.sqlite3"
        try:
            resolved_url = BackupManager.get_backup_url()
            self.assertEqual(resolved_url, os.path.join(custom_dir, "production.sqlite3"))
            self.assertTrue(os.path.exists(custom_dir))
        finally:
            os.environ.pop("DATABASE_BACKUP_PATH", None)
    def test_get_target_file_path_sqlite_relative_dsn(self):
        target = BackupManager._get_target_file_path("sqlite:///backups/backup.sqlite3")
        self.assertEqual(target, "backups/backup.sqlite3")

        abs_target = BackupManager._get_target_file_path("sqlite:////var/data/backup.sqlite3")
        self.assertEqual(abs_target, "/var/data/backup.sqlite3")


if __name__ == "__main__":
    unittest.main()
