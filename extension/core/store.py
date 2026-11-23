"""Repository and transaction boundary. Each service gets its own database."""
from contextlib import contextmanager
import json
import re
import psycopg
from psycopg.rows import dict_row
from psycopg import sql
from extension.core.domain import DomainError
from extension.core.events import make_event


class Store:
    def __init__(self, url, service, schema=None):
        self.url, self.service, self.schema = url, service, schema
        if schema is not None and not re.fullmatch(r'test_[a-z0-9_]+', schema):
            raise ValueError('Only an isolated test schema is allowed')
        with self.transaction() as tx:
            tx.conn.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')
            tx.conn.execute('CREATE TABLE IF NOT EXISTS outbox (seq BIGSERIAL PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, sent_at TIMESTAMPTZ)')
            tx.conn.execute('CREATE TABLE IF NOT EXISTS handled (event_id TEXT PRIMARY KEY)')

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            if self.schema:
                conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.schema)))
                conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(self.schema)))
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (self.schema or self.service,))
            yield Transaction(conn, self.service)

    def pending(self):
        with self.transaction() as tx:
            return tx.conn.execute('SELECT event_id,payload FROM outbox WHERE sent_at IS NULL ORDER BY seq LIMIT 50').fetchall()

    def mark_sent(self, event_id):
        with self.transaction() as tx:
            tx.conn.execute('UPDATE outbox SET sent_at=NOW() WHERE event_id=%s', (event_id,))


class Transaction:
    def __init__(self, conn, service):
        self.conn, self.service = conn, service

    def get(self, kind, record_id, required=True):
        row = self.conn.execute('SELECT payload FROM records WHERE kind=%s AND id=%s', (kind, record_id)).fetchone()
        if row is None:
            if required:
                raise DomainError(404, kind+'_not_found', 'Объект не найден')
            return None
        return json.loads(row['payload'])

    def put(self, kind, record_id, value):
        self.conn.execute('INSERT INTO records VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=EXCLUDED.payload',
                          (kind, record_id, json.dumps(value, ensure_ascii=False)))
        return value

    def all(self, kind):
        rows = self.conn.execute('SELECT payload FROM records WHERE kind=%s ORDER BY id', (kind,)).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def emit(self, kind, data):
        event = make_event(self.service, kind, data)
        self.conn.execute('INSERT INTO outbox(event_id,payload) VALUES(%s,%s)',
                          (event['event_id'], json.dumps(event, ensure_ascii=False)))
        return event

    def seen(self, event_id):
        return self.conn.execute('SELECT 1 FROM handled WHERE event_id=%s', (event_id,)).fetchone() is not None

    def remember(self, event_id):
        self.conn.execute('INSERT INTO handled VALUES(%s)', (event_id,))
