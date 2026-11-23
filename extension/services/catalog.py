from uuid import uuid4
from extension.core.base import Service
from extension.core.domain import uuid_text, dish_values, require, text
from extension.core.events import order_data

RESTAURANT_ID = '22222222-2222-4222-8222-222222222222'
DISH_ID = '33333333-3333-4333-8333-333333333333'


class CatalogService(Service):
    name = 'catalog'
    ROUTES = [
        ('GET', '/restaurants', 'restaurants', 'Список ресторанов с рейтингом'),
        ('GET', '/restaurants/{restaurant_id}/menu', 'menu', 'Меню ресторана'),
        ('POST', '/restaurants/{restaurant_id}/dishes', 'add_dish', 'Добавить блюдо'),
        ('PATCH', '/dishes/{dish_id}', 'update_dish', 'Изменить блюдо'),
        ('POST', '/restaurant-orders/{order_id}/decision', 'decision', 'Принять или отклонить заказ')]

    def __init__(self, store, peers=None):
        super().__init__(store, peers)
        with store.transaction() as tx:
            if tx.get('restaurants', RESTAURANT_ID, False) is None:
                tx.put('restaurants', RESTAURANT_ID, {'restaurant_id': RESTAURANT_ID, 'name': 'Обед', 'rating': 0})
                dish = {'dish_id': DISH_ID, 'restaurant_id': RESTAURANT_ID, 'name': 'Паста', 'price': 60000, 'available': True}
                tx.put('dishes', DISH_ID, dish)
                tx.emit('dish.changed', dish)

    def restaurants(self, query, **_):
        result = self.page('restaurants', query)
        for item in result['items']:
            rating = self.peers.get('reviews', 'ratings/restaurant/'+item['restaurant_id'])
            item.update(rating=rating['rating'], review_count=rating['review_count'])
        return 200, result

    def menu(self, restaurant_id, query, **_):
        restaurant_id = uuid_text(restaurant_id)
        self.get_record('restaurants', restaurant_id)
        return 200, self.page('dishes', query, lambda d: d['restaurant_id'] == restaurant_id)

    def add_dish(self, restaurant_id, body, **_):
        restaurant_id = uuid_text(restaurant_id)
        values = dish_values(body)
        require(all(k in values for k in ['name', 'price', 'available']), 'missing_fields', 'Нужны name, price, available')
        dish = {k: values[k] for k in ['name', 'price', 'available']}
        dish.update(dish_id=str(uuid4()), restaurant_id=restaurant_id)
        with self.store.transaction() as tx:
            tx.get('restaurants', restaurant_id)
            tx.put('dishes', dish['dish_id'], dish)
            tx.emit('dish.changed', dish)
        return 201, dish

    def update_dish(self, dish_id, body, **_):
        dish_id = uuid_text(dish_id)
        values = dish_values(body)
        require(any(k in values for k in ['name', 'price', 'available']), 'empty_patch', 'Нет изменяемых полей')
        with self.store.transaction() as tx:
            dish = tx.get('dishes', dish_id)
            dish.update({k: values[k] for k in ['name', 'price', 'available'] if k in values})
            tx.put('dishes', dish_id, dish)
            tx.emit('dish.changed', dish)
        return 200, dish

    def decision(self, order_id, body, **_):
        order_id = uuid_text(order_id)
        accepted = body.get('accepted')
        require(type(accepted) is bool, 'invalid_decision', 'accepted: boolean')
        reason = '' if accepted else text(body.get('reason'), 'reason')
        target = 'accepted' if accepted else 'rejected'
        with self.store.transaction() as tx:
            local = tx.get('restaurant_orders', order_id)
            require(local['status'] in {'pending', target}, 'order_closed', 'Заказ ресторана уже закрыт', 409)
            if local['status'] != target:
                local.update(status=target, reason=reason)
                tx.put('restaurant_orders', order_id, local)
                kind = 'restaurant.order_accepted' if accepted else 'restaurant.order_rejected'
                tx.emit(kind, order_data(local) if accepted else order_data(local, reason=reason))
        return 200, local

    def react(self, tx, kind, data):
        if kind == 'order.sent_to_restaurant':
            if tx.get('restaurant_orders', data['order_id'], False) is None:
                tx.put('restaurant_orders', data['order_id'], dict(data, status='pending'))
        elif kind == 'order.cancelled':
            local = tx.get('restaurant_orders', data['order_id'], False)
            if local:
                local['status'] = 'cancelled'
                tx.put('restaurant_orders', data['order_id'], local)
        elif kind == 'rating.updated' and data['target_type'] == 'restaurant':
            restaurant = tx.get('restaurants', data['target_id'], False)
            if restaurant:
                restaurant['rating'] = data['rating']
                tx.put('restaurants', data['target_id'], restaurant)
