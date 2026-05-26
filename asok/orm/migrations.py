from __future__ import annotations


class Migrations:
    """Utility to track and manage applied database migrations."""

    @staticmethod
    def ensure_table():
        """Ensures the tracking table exists in the database."""
        from .model import Model

        engine = Model.get_engine()
        pk_def = getattr(engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT")

        # Define table structure dynamically to support SQLite, Postgres, MySQL
        sql = f"""
            CREATE TABLE IF NOT EXISTS _asok_migrations (
                {pk_def},
                name VARCHAR(255) UNIQUE NOT NULL,
                batch INTEGER NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        engine.execute(sql)

    @staticmethod
    def get_applied() -> list[str]:
        """Return a list of all applied migration names in chronological order."""
        from .model import Model

        Migrations.ensure_table()
        engine = Model.get_engine()
        rows = engine.execute("SELECT name FROM _asok_migrations ORDER BY id ASC")
        return [row["name"] for row in rows]

    @staticmethod
    def log(name: str, batch: int):
        """Record a new migration as applied."""
        from .model import Model

        engine = Model.get_engine()
        engine.execute(
            "INSERT INTO _asok_migrations (name, batch) VALUES (?, ?)",
            (name, batch),
        )

    @staticmethod
    def get_last_batch_number() -> int:
        """Return the current maximum batch number."""
        from .model import Model

        Migrations.ensure_table()
        engine = Model.get_engine()
        rows = engine.execute("SELECT MAX(batch) as max_batch FROM _asok_migrations")
        if not rows:
            return 0
        val = list(rows[0].values())[0]
        return val or 0

    @staticmethod
    def get_last_batch() -> list[str]:
        """Return names of migrations belonging to the last executed batch."""
        from .model import Model

        last_batch = Migrations.get_last_batch_number()
        if last_batch == 0:
            return []
        engine = Model.get_engine()
        rows = engine.execute(
            "SELECT name FROM _asok_migrations WHERE batch = ? ORDER BY id DESC",
            (last_batch,),
        )
        return [row["name"] for row in rows]

    @staticmethod
    def remove(name: str):
        """Remove a migration record from the tracking table."""
        from .model import Model

        engine = Model.get_engine()
        engine.execute("DELETE FROM _asok_migrations WHERE name = ?", (name,))
