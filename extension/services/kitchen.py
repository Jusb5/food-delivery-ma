from extension.core.base import Service
from extension.core.domain import uuid_text, utcnow, kitchen_transition
from extension.core.events import order_data


class KitchenService(Service):
    name = 'kitchen'
    ROUTES = [
        ('GET', '/kitchen-orders', 'list', 'Список заданий кухни'),
        ('GET', '/kitchen-orders/{order_id}', 'get', 'Получить задание'),
        ('POST', '/kitchen-orders/{order_id}/start', 'start', 'Начать приготовление'),
        ('POST', '/kitchen-orders/{order_id}/ready', 'ready', 'Завершить приготовление')]

    def list(self, query, **_):
        return 200, self.page('kitchen_orders', query)

    def get(self, order_id, **_):
        return 200, self.get_record('kitchen_orders', order_id)

    def change(self, order_id, action):
        order_id = uuid_text(order_id)
        with self.store.transaction() as tx:
            job = tx.get('kitchen_orders', order_id)
            target = 'cooking' if action == 'start' else 'ready'
            if job['status'] != target:
                job['status'] = kitchen_transition(job['status'], action)
                job['started_at' if action == 'start' else 'ready_at'] = utcnow()
                tx.put('kitchen_orders', order_id, job)
                tx.emit('kitchen.started' if action == 'start' else 'kitchen.ready', order_data(job))
        return 200, job

    def start(self, order_id, **_):
        return self.change(order_id, 'start')

    def ready(self, order_id, **_):
        return self.change(order_id, 'ready')

    def react(self, tx, kind, data):
        if tx.get('kitchen_orders', data['order_id'], False) is None:
            job = dict(data, status='accepted', started_at=None, ready_at=None)
            tx.put('kitchen_orders', data['order_id'], job)
