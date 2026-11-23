import json
from uuid import uuid4
from common.domain import DomainError, uuid_text, utcnow, pagination


class NotificationService:
    TEXTS = {'order.created': 'Заказ оформлен', 'order.cancelled': 'Заказ отменён',
             'order.receipt_confirmed': 'Получение заказа подтверждено'}

    def __init__(self, db):
        self.db = db
        with db.transaction() as tx:
            tx.execute('CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
            tx.execute('CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY)')

    def handle(self, event):
        if not isinstance(event, dict):
            raise DomainError(422, 'invalid_event', 'Нужен объект события')
        event_id = uuid_text(event.get('event_id'))
        kind = event.get('type')
        if kind not in self.TEXTS:
            return False
        data = event.get('data', {})
        order_id, customer_id = uuid_text(data.get('id')), uuid_text(data.get('customer_id'))
        with self.db.transaction() as tx:
            tx.lock_key(event_id)
            if tx.one('SELECT event_id FROM processed_events WHERE event_id=?', (event_id,)):
                return False
            tx.execute('INSERT INTO processed_events VALUES (?)', (event_id,))
            notification = {'id': str(uuid4()), 'order_id': order_id, 'customer_id': customer_id,
                            'text': self.TEXTS[kind], 'status': 'created', 'created_at': utcnow()}
            self._save_notification(tx, event_id, notification)
        return True

    @staticmethod
    def _save_notification(tx, event_id, notification):
        tx.execute('INSERT INTO notifications VALUES (?, ?, ?, ?)',
                   (notification['id'], event_id, notification['status'], json.dumps(notification, ensure_ascii=False)))

    def list(self, limit=20, offset=0, status=None):
        limit, offset = pagination(limit, offset)
        if status is not None and status not in {'created', 'read'}:
            raise DomainError(422, 'invalid_status', 'Ожидается created или read')
        where, params = (' WHERE status=?', (status,)) if status else ('', ())
        with self.db.transaction() as tx:
            rows = tx.all('SELECT payload FROM notifications'+where+' ORDER BY id LIMIT ? OFFSET ?', params+(limit, offset))
            total = tx.one('SELECT COUNT(*) AS n FROM notifications'+where, params)['n']
        return {'items': [json.loads(r['payload']) for r in rows], 'total': total}

    def get(self, notification_id):
        notification_id = uuid_text(notification_id)
        with self.db.transaction() as tx:
            return self._get(tx, notification_id)

    @staticmethod
    def _get(tx, notification_id):
        row = tx.one('SELECT payload FROM notifications WHERE id=?', (notification_id,))
        if not row:
            raise DomainError(404, 'notification_not_found', 'Уведомление не найдено')
        return json.loads(row['payload'])

    def read(self, notification_id):
        notification_id = uuid_text(notification_id)
        with self.db.transaction() as tx:
            tx.lock_key(notification_id)
            result = self._get(tx, notification_id)
            result['status'] = 'read'
            tx.execute('UPDATE notifications SET status=?, payload=? WHERE id=?',
                       ('read', json.dumps(result, ensure_ascii=False), notification_id))
        return result
