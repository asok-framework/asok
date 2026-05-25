from __future__ import annotations

import sqlite3


class Migrations:
    """Utility to track and manage applied database migrations."""

    @staticmethod
    def ensure_table():
        """Ensures the tracking table exists in the database."""
        from .model import Model

        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _asok_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    batch INTEGER NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_applied() -> list[str]:
        """Return a list of all applied migration names in chronological order."""
        from .model import Model

        Migrations.ensure_table()
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT name FROM _asok_migrations ORDER BY id ASC"
            ).fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def log(name: str, batch: int):
        """Record a new migration as applied."""
        from .model import Model

        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute(
                "INSERT INTO _asok_migrations (name, batch) VALUES (?, ?)",
                (name, batch),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_last_batch_number() -> int:
        """Return the current maximum batch number."""
        from .model import Model

        Migrations.ensure_table()
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT MAX(batch) as max_batch FROM _asok_migrations"
            ).fetchone()
            return row["max_batch"] or 0
        finally:
            conn.close()

    @staticmethod
    def get_last_batch() -> list[str]:
        """Return names of migrations belonging to the last executed batch."""
        from .model import Model

        last_batch = Migrations.get_last_batch_number()
        if last_batch == 0:
            return []
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT name FROM _asok_migrations WHERE batch = ? ORDER BY id DESC",
                (last_batch,),
            ).fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def remove(name: str):
        """Remove a migration record from the tracking table."""
        from .model import Model

        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute("DELETE FROM _asok_migrations WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()
