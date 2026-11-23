from uuid import uuid4
from extension.core.base import Service
from extension.core.domain import uuid_text, require, pickup_allowed, utcnow
from extension.core.events import order_data


class DeliveryService(Service):
    name = 'delivery'
    ROUTES = [
        ('GET', '/deliveries', 'list', 'Список доставок'),
        ('GET', '/deliveries/{delivery_id}', 'get', 'Получить доставку'),
        ('POST', '/deliveries/{delivery_id}/assign', 'assign', 'Назначить курьера'),
        ('POST', '/deliveries/{delivery_id}/pickup', 'pickup', 'Подтвердить забор заказа'),
        ('POST', '/deliveries/{delivery_id}/complete', 'complete', 'Подтвердить доставку')]

    def list(self, query, **_):
        order_id = uuid_text(query['order_id']) if 'order_id' in query else None
        return 200, self.page('deliveries', query, lambda d: order_id is None or d['order_id'] == order_id)

    def get(self, delivery_id, **_):
        delivery = self.get_record('deliveries', delivery_id)
        with self.store.transaction() as tx:
            rating = tx.get('courier_ratings', delivery['courier_id'], False) if delivery['courier_id'] else None
        delivery['courier_rating'] = rating['rating'] if rating else 0
        return 200, delivery

    def assign(self, delivery_id, body, **_):
        delivery_id, courier = uuid_text(delivery_id), uuid_text(body.get('courier_id'))
        with self.store.transaction() as tx:
            delivery = tx.get('deliveries', delivery_id)
            require(delivery['status'] in {'created','assigned'}, 'already_picked_up', 'Курьер уже забрал заказ', 409)
            if delivery['courier_id'] != courier:
                delivery.update(courier_id=courier, status='assigned')
                tx.put('deliveries', delivery_id, delivery)
                tx.emit('courier.assigned', order_data(delivery, delivery_id=delivery_id))
        return 200, delivery

    def pickup(self, delivery_id, **_):
        delivery_id = uuid_text(delivery_id)
        existing = self.get_record('deliveries', delivery_id)
        kitchen = self.peers.get('kitchen', 'kitchen-orders/'+existing['order_id'])
        with self.store.transaction() as tx:
            delivery = tx.get('deliveries', delivery_id)
            if delivery['status'] != 'picked_up':
                pickup_allowed(delivery, kitchen)
                delivery.update(status='picked_up', kitchen_ready=True)
                tx.put('deliveries', delivery_id, delivery)
                tx.emit('delivery.picked_up', order_data(delivery, delivery_id=delivery_id))
        return 200, delivery

    def complete(self, delivery_id, **_):
        delivery_id = uuid_text(delivery_id)
        with self.store.transaction() as tx:
            delivery = tx.get('deliveries', delivery_id)
            if delivery['status'] != 'delivered':
                require(delivery['status'] == 'picked_up', 'not_picked_up', 'Заказ ещё не забран', 409)
                delivery.update(status='delivered', delivered_at=utcnow())
                tx.put('deliveries', delivery_id, delivery)
                tx.emit('delivery.confirmed', order_data(delivery, delivery_id=delivery_id))
        return 200, delivery

    def react(self, tx, kind, data):
        if kind == 'restaurant.order_accepted':
            old = tx.get('delivery_by_order', data['order_id'], False)
            if old:
                return
            ready = tx.get('ready_orders', data['order_id'], False) is not None
            delivery_id = str(uuid4())
            delivery = dict(data, delivery_id=delivery_id, status='created', courier_id=None,
                            kitchen_ready=ready, delivered_at=None)
            tx.put('deliveries', delivery_id, delivery)
            tx.put('delivery_by_order', data['order_id'], {'delivery_id': delivery_id})
            tx.emit('delivery.created', order_data(delivery, delivery_id=delivery_id))
        elif kind == 'kitchen.ready':
            tx.put('ready_orders', data['order_id'], {'ready': True})
            link = tx.get('delivery_by_order', data['order_id'], False)
            if link:
                delivery = tx.get('deliveries', link['delivery_id'])
                delivery['kitchen_ready'] = True
                tx.put('deliveries', link['delivery_id'], delivery)
        elif kind == 'rating.updated' and data['target_type'] == 'courier':
            tx.put('courier_ratings', data['target_id'], data)
