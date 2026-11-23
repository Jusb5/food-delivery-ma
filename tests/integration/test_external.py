import json
import os
import unittest
from uuid import uuid4
from common.db import Database


class ExternalTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'TEST_POSTGRES_URL is not set')
    def test_postgres_commit_and_rollback(self):
        db = Database(os.environ['TEST_POSTGRES_URL'])
        table = 'test_' + uuid4().hex
        try:
            with db.transaction() as tx:
                tx.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
                tx.execute(f'INSERT INTO {table} VALUES (?)', (1,))
            with self.assertRaises(RuntimeError):
                with db.transaction() as tx:
                    tx.execute(f'INSERT INTO {table} VALUES (?)', (2,))
                    raise RuntimeError('rollback')
            with db.transaction() as tx:
                self.assertEqual(tx.one(f'SELECT COUNT(*) AS n FROM {table}')['n'], 1)
        finally:
            with db.transaction() as tx:
                tx.execute(f'DROP TABLE IF EXISTS {table}')

    @unittest.skipUnless(os.getenv('TEST_AMQP_URL'), 'TEST_AMQP_URL is not set')
    def test_rabbit_publish_confirm_receive_ack(self):
        import pika
        with pika.BlockingConnection(pika.URLParameters(os.environ['TEST_AMQP_URL'])) as conn:
            channel = conn.channel()
            name = channel.queue_declare('', exclusive=True).method.queue
            channel.confirm_delivery()
            event = {'event_id': str(uuid4()), 'type': 'test.created'}
            channel.basic_publish('', name, json.dumps(event).encode(), mandatory=True,
                                  properties=pika.BasicProperties(delivery_mode=2))
            method, properties, body = channel.basic_get(name, auto_ack=False)
            self.assertIsNotNone(method)
            self.assertEqual(json.loads(body), event)
            channel.basic_ack(method.delivery_tag)
            self.assertIsNone(channel.basic_get(name, auto_ack=True)[0])
