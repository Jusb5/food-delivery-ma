import hashlib
import json
from uuid import uuid4
from common.domain import DomainError, uuid_text, utcnow, pagination

CUSTOMER_ID = '11111111-1111-4111-8111-111111111111'
RESTAURANT_ID = '22222222-2222-4222-8222-222222222222'
DISH_ID = '33333333-3333-4333-8333-333333333333'


def calculate_total(items, catalog):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise DomainError(422, 'invalid_items', 'Нужен непустой список блюд до 100 позиций')
    total, result = 0, []
    for item in items:
        if not isinstance(item, dict):
            raise DomainError(422, 'invalid_item', 'Позиция должна быть объектом')
        dish = uuid_text(item.get('dish_id'))
        quantity = item.get('quantity')
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise DomainError(422, 'invalid_quantity', 'Количество должно быть целым от 1 до 100')
        if dish not in catalog:
            raise DomainError(422, 'unknown_dish', 'Блюдо не найдено в каталоге ресторана')
        price = catalog[dish]
        total += price * quantity
        result.append({'dish_id': dish, 'quantity': quantity, 'unit_price': price})
    return total, result


class OrderService:
    def __init__(self, db):
        self.db = db
        with db.transaction() as tx:
            tx.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
            tx.execute('CREATE TABLE IF NOT EXISTS catalog (dish_id TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL, unit_price INTEGER NOT NULL)')
            tx.execute('CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, routing_key TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT)')
            tx.execute('CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY)')
            tx.execute('INSERT INTO catalog VALUES (?, ?, ?) ON CONFLICT (dish_id) DO NOTHING', (DISH_ID, RESTAURANT_ID, 60000))

    def _event(self, tx, kind, order):
        event = {'event_id': str(uuid4()), 'type': kind, 'occurred_at': utcnow(), 'data': order}
        tx.execute('INSERT INTO outbox (event_id, routing_key, payload, created_at) VALUES (?, ?, ?, ?)',
                   (event['event_id'], kind, json.dumps(event, ensure_ascii=False), event['occurred_at']))
        return event

    def create(self, body, key):
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 128:
            raise DomainError(422, 'invalid_idempotency_key', 'Нужен заголовок Idempotency-Key длиной 1-128')
        if not isinstance(body, dict):
            raise DomainError(422, 'invalid_body', 'Нужен JSON-объект')
        customer = uuid_text(body.get('customer_id'))
        restaurant = uuid_text(body.get('restaurant_id'))
        address = body.get('address')
        if not isinstance(address, str) or not 1 <= len(address.strip()) <= 300:
            raise DomainError(422, 'invalid_address', 'Адрес должен содержать от 1 до 300 символов')
        normalized = dict(body, customer_id=customer, restaurant_id=restaurant, address=address.strip())
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.db.transaction() as tx:
            tx.lock_key(key)
            previous = tx.one('SELECT payload, request_hash FROM orders WHERE idem_key=?', (key,))
            if previous:
                if previous['request_hash'] != digest:
                    raise DomainError(409, 'idempotency_conflict', 'Этот ключ уже использован с другим запросом')
                return json.loads(previous['payload']), False
            catalog = {r['dish_id']: r['unit_price'] for r in tx.all('SELECT * FROM catalog WHERE restaurant_id=?', (restaurant,))}
            total, items = calculate_total(body.get('items'), catalog)
            order = {'id': str(uuid4()), 'customer_id': customer, 'restaurant_id': restaurant,
                     'address': address.strip(), 'items': items, 'total': total, 'status': 'awaiting_payment',
                     'receipt_confirmed': False, 'created_at': utcnow(), 'cancel_reason': None}
            tx.execute('INSERT INTO orders VALUES (?, ?, ?, ?, ?)',
                       (order['id'], key, digest, order['status'], json.dumps(order, ensure_ascii=False)))
            self._event(tx, 'order.created', order)
        return order, True

    def get(self, order_id):
        order_id = uuid_text(order_id)
        with self.db.transaction() as tx:
            return self._get(tx, order_id)

    @staticmethod
    def _get(tx, order_id):
        row = tx.one('SELECT payload FROM orders WHERE id=?', (order_id,))
        if not row:
            raise DomainError(404, 'order_not_found', 'Заказ не найден')
        return json.loads(row['payload'])

    def list(self, limit=20, offset=0, status=None):
        limit, offset = pagination(limit, offset)
        if status is not None and status not in {'awaiting_payment', 'paid', 'delivered', 'cancelled', 'completed'}:
            raise DomainError(422, 'invalid_status', 'Неизвестный статус заказа')
        where, params = (' WHERE status=?', (status,)) if status else ('', ())
        with self.db.transaction() as tx:
            rows = tx.all('SELECT payload FROM orders'+where+' ORDER BY id LIMIT ? OFFSET ?', params+(limit, offset))
            total = tx.one('SELECT COUNT(*) AS n FROM orders'+where, params)['n']
        return {'items': [json.loads(r['payload']) for r in rows], 'total': total}

    def cancel(self, order_id, reason):
        order_id = uuid_text(order_id)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300:
            raise DomainError(422, 'invalid_reason', 'Нужна причина отмены длиной 1-300')
        with self.db.transaction() as tx:
            tx.lock_key(order_id)
            order = self._get(tx, order_id)
            if order['status'] == 'cancelled':
                return order
            if order['status'] not in {'awaiting_payment', 'paid'}:
                raise DomainError(409, 'cannot_cancel', 'После доставки отмена недоступна')
            order.update(status='cancelled', cancel_reason=reason.strip())
            self._save(tx, order)
            self._event(tx, 'order.cancelled', order)
        return order

    def receipt(self, order_id):
        order_id = uuid_text(order_id)
        with self.db.transaction() as tx:
            tx.lock_key(order_id)
            order = self._get(tx, order_id)
            if order['receipt_confirmed']:
                return order
            if order['status'] != 'delivered':
                raise DomainError(409, 'not_delivered', 'Доставка ещё не подтверждена')
            order.update(receipt_confirmed=True, status='completed')
            self._save(tx, order)
            self._event(tx, 'order.receipt_confirmed', order)
        return order

    @staticmethod
    def _save(tx, order):
        tx.execute('UPDATE orders SET status=?, payload=? WHERE id=?',
                   (order['status'], json.dumps(order, ensure_ascii=False), order['id']))

    def handle(self, event):
        event_id = uuid_text(event.get('event_id'))
        kind, data = event.get('type'), event.get('data', {})
        if kind not in {'payment.succeeded', 'delivery.confirmed'}:
            return False
        order_id = uuid_text(data.get('order_id'))
        with self.db.transaction() as tx:
            tx.lock_key(order_id)
            if tx.one('SELECT event_id FROM processed_events WHERE event_id=?', (event_id,)):
                return False
            order = self._get(tx, order_id)
            expected, target = ('awaiting_payment', 'paid') if kind == 'payment.succeeded' else ('paid', 'delivered')
            if order['status'] != expected:
                raise DomainError(409, 'invalid_transition', 'Нарушен порядок оплаты и доставки')
            order['status'] = target
            self._save(tx, order)
            tx.execute('INSERT INTO processed_events VALUES (?)', (event_id,))
        return True
