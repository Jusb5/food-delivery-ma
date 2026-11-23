import json
import tempfile
import unittest
from pathlib import Path
from common.db import Database
from orders.service import OrderService, CUSTOMER_ID, RESTAURANT_ID, DISH_ID
from notifications.service import NotificationService


def order_body():
    return {'customer_id': CUSTOMER_ID, 'restaurant_id': RESTAURANT_ID,
            'address': 'Москва, проспект Вернадского, 78',
            'items': [{'dish_id': DISH_ID, 'quantity': 2}]}


class ServicesCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.orders = OrderService(Database('sqlite:///'+str(Path(self.temp.name)/'orders.db')))
        self.notifications = NotificationService(Database('sqlite:///'+str(Path(self.temp.name)/'notifications.db')))

    def create_order(self, key='checkout-001'):
        return self.orders.create(order_body(), key)[0]

    def event(self):
        self.create_order()
        with self.orders.db.transaction() as tx:
            return json.loads(tx.one('SELECT payload FROM outbox')['payload'])

    def count(self, service, table):
        assert table in {'orders', 'outbox', 'processed_events', 'notifications'}
        with service.db.transaction() as tx:
            return tx.one('SELECT COUNT(*) AS n FROM '+table)['n']
