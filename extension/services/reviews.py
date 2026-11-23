from uuid import uuid4
from extension.core.base import Service
from extension.core.domain import uuid_text, require, text, review_values, rating_values, utcnow
from extension.core.events import order_data


class ReviewService(Service):
    name = 'reviews'
    ROUTES = [
        ('POST', '/reviews', 'create', 'Добавить отзыв по полученному заказу'),
        ('GET', '/reviews', 'list', 'Опубликованные отзывы'),
        ('GET', '/ratings/{target_type}/{target_id}', 'rating', 'Рейтинг ресторана или курьера'),
        ('POST', '/reviews/{review_id}/hide', 'hide', 'Скрыть отзыв')]

    def create(self, body, **_):
        values = review_values(body)
        order = self.peers.get('orders', 'orders/'+values['order_id'])
        require(order['receipt_confirmed'] and order['status'] == 'completed',
                'receipt_required', 'Сначала подтвердите получение заказа', 409)
        expected = order['restaurant_id'] if values['target_type'] == 'restaurant' else order['courier_id']
        require(values['target_id'] == expected, 'wrong_review_target', 'Объект не относится к заказу')
        key = values['order_id']+':'+values['target_type']
        with self.store.transaction() as tx:
            require(tx.get('eligible_orders', values['order_id'], False) is not None,
                    'event_pending', 'Событие завершения ещё обрабатывается', 409)
            require(tx.get('review_keys', key, False) is None, 'duplicate_review', 'Отзыв уже оставлен', 409)
            review = dict(values, review_id=str(uuid4()), customer_id=order['customer_id'],
                          hidden=False, hidden_reason=None, created_at=utcnow())
            tx.put('reviews', review['review_id'], review)
            tx.put('review_keys', key, {'review_id': review['review_id']})
            tx.emit('review.created', order_data(order, review_id=review['review_id'],
                    target_type=values['target_type'], target_id=values['target_id'], score=values['score']))
            self.emit_rating(tx, values['target_type'], values['target_id'])
        return 201, review

    def list(self, query, **_):
        target = uuid_text(query['target_id']) if 'target_id' in query else None
        return 200, self.page('reviews', query, lambda r: not r['hidden'] and (target is None or r['target_id'] == target))

    def rating(self, target_type, target_id, **_):
        require(target_type in {'restaurant','courier'}, 'invalid_target', 'restaurant или courier')
        target_id = uuid_text(target_id)
        with self.store.transaction() as tx:
            result = self.calculate(tx, target_type, target_id)
        return 200, result

    @staticmethod
    def calculate(tx, target_type, target_id):
        rows = [r for r in tx.all('reviews') if r['target_type'] == target_type and r['target_id'] == target_id]
        return dict(target_type=target_type, target_id=target_id, **rating_values(rows))

    def emit_rating(self, tx, target_type, target_id):
        tx.emit('rating.updated', self.calculate(tx, target_type, target_id))

    def hide(self, review_id, body, **_):
        review_id, reason = uuid_text(review_id), text(body.get('reason'), 'reason')
        with self.store.transaction() as tx:
            review = tx.get('reviews', review_id)
            if not review['hidden']:
                review.update(hidden=True, hidden_reason=reason)
                tx.put('reviews', review_id, review)
                self.emit_rating(tx, review['target_type'], review['target_id'])
        return 200, review

    def react(self, tx, kind, data):
        tx.put('eligible_orders', data['order_id'], data)
