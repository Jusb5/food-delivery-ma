"""One transaction per operation; SQLite locally, PostgreSQL in Compose."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    def __init__(self, url):
        self.url = url
        self.postgres = url.startswith(('postgresql://', 'postgres://'))
        if not self.postgres:
            self.path = url.removeprefix('sqlite:///')
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(self.url, row_factory=dict_row)
        else:
            conn = sqlite3.connect(self.path, timeout=20)
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
        try:
            yield Session(conn, self.postgres)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class Session:
    def __init__(self, conn, postgres):
        self.conn, self.postgres = conn, postgres

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace('?', '%s') if self.postgres else sql, params)

    def one(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, params=()):
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def lock_key(self, key):
        if self.postgres:
            self.execute('SELECT pg_advisory_xact_lock(hashtextextended(?, 0))', (key,))
