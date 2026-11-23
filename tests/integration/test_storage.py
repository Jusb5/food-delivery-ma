from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from common.domain import DomainError
from orders.service import OrderService
from tests.helpers import ServicesCase, order_body


class PersistenceTests(ServicesCase):
    def test_create_writes_order_and_outbox(self):
        self.create_order()
        self.assertEqual(self.count(self.orders, 'orders'), 1)
        self.assertEqual(self.count(self.orders, 'outbox'), 1)

    def test_outbox_failure_rolls_back_order(self):
        with patch.object(self.orders, '_event', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.create_order()
        self.assertEqual(self.count(self.orders, 'orders'), 0)
        self.assertEqual(self.count(self.orders, 'outbox'), 0)

    def test_same_key_is_idempotent(self):
        first, created = self.orders.create(order_body(), 'key')
        second, repeated = self.orders.create(order_body(), 'key')
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first, second)
        self.assertEqual(self.count(self.orders, 'outbox'), 1)

    def test_same_key_different_body_conflicts(self):
        self.create_order('key')
        body = order_body(); body['items'][0]['quantity'] = 3
        with self.assertRaises(DomainError) as error:
            self.orders.create(body, 'key')
        self.assertEqual(error.exception.status, 409)

    def test_concurrent_same_key_creates_one_order(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.orders.create(order_body(), 'parallel'), range(4)))
        self.assertEqual(len({r[0]['id'] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)

    def test_reopen_retains_order(self):
        order = self.create_order()
        reopened = OrderService(self.orders.db)
        self.assertEqual(reopened.get(order['id']), order)

    def test_cancel_is_idempotent(self):
        order = self.create_order()
        self.orders.cancel(order['id'], 'Передумал')
        self.orders.cancel(order['id'], 'Передумал')
        self.assertEqual(self.count(self.orders, 'outbox'), 2)

    def test_receipt_requires_delivery(self):
        order = self.create_order()
        with self.assertRaises(DomainError) as error:
            self.orders.receipt(order['id'])
        self.assertEqual(error.exception.status, 409)

    def test_payment_delivery_receipt(self):
        order = self.create_order()
        for kind in ['payment.succeeded', 'delivery.confirmed']:
            event = {'event_id': str(uuid4()), 'type': kind, 'data': {'order_id': order['id']}}
            self.assertTrue(self.orders.handle(event))
            self.assertFalse(self.orders.handle(event))
        self.assertEqual(self.orders.receipt(order['id'])['status'], 'completed')
        self.assertEqual(self.orders.receipt(order['id'])['status'], 'completed')
        self.assertEqual(self.count(self.orders, 'outbox'), 2)
        with self.assertRaises(DomainError):
            self.orders.cancel(order['id'], 'Поздно')

    def test_duplicate_event_creates_one_notification(self):
        event = self.event()
        self.assertTrue(self.notifications.handle(event))
        self.assertFalse(self.notifications.handle(event))
        self.assertEqual(self.notifications.list()['total'], 1)
        self.assertEqual(self.count(self.notifications, 'processed_events'), 1)

    def test_notification_failure_rolls_back_marker(self):
        event = self.event()
        with patch.object(self.notifications, '_save_notification', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.notifications.handle(event)
        self.assertEqual(self.count(self.notifications, 'processed_events'), 0)
        self.assertTrue(self.notifications.handle(event))

    def test_databases_are_separate(self):
        self.create_order()
        with self.notifications.db.transaction() as tx:
            tables = {r['name'] for r in tx.all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('orders', tables)
        self.assertEqual(self.notifications.list()['total'], 0)

    def test_notification_read(self):
        self.notifications.handle(self.event())
        notification = self.notifications.list()['items'][0]
        self.assertEqual(self.notifications.read(notification['id'])['status'], 'read')
        self.assertEqual(self.notifications.get(notification['id'])['status'], 'read')
