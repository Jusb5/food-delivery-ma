"""Message catalogue is also the source of the RabbitMQ bindings."""
from uuid import uuid4
from datetime import datetime
from extension.core.domain import require, uuid_text, utcnow

ORDER_FIELDS = {
    'order_id': 'uuid', 'customer_id': 'uuid', 'restaurant_id': 'uuid',
    'address': 'string', 'items': 'array', 'total': 'integer', 'status': 'string',
    'payment_id': 'uuid|null', 'courier_id': 'uuid|null'
}


def definition(producer, consumers, fields, action):
    return dict(producer=producer, consumers=consumers, fields=fields, action=action)


EVENTS = {
    'customer.registered': definition('profiles', ['notifications'],
        {'customer_id': 'uuid', 'name': 'string', 'phone': 'string'},
        'Сохранить контакт клиента в модели уведомлений'),
    'customer.updated': definition('profiles', ['notifications'],
        {'customer_id': 'uuid', 'name': 'string', 'phone': 'string'},
        'Обновить имя клиента в модели получателя'),
    'address.saved': definition('profiles', ['notifications'],
        {'customer_id': 'uuid', 'address_id': 'uuid', 'address': 'string', 'comment': 'string'},
        'Обновить адрес в модели получателя'),
    'dish.changed': definition('catalog', [],
        {'restaurant_id': 'uuid', 'dish_id': 'uuid', 'name': 'string', 'price': 'integer', 'available': 'boolean'},
        'Зафиксировать изменение каталога в журнале сообщений'),
    'order.created': definition('orders', ['payments', 'notifications'], ORDER_FIELDS,
        'Создать платёж; сохранить уведомление об оформлении'),
    'payment.created': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, amount='integer', attempt_number='integer'),
        'Связать заказ с платежом; уведомить о создании платежа'),
    'payment.attempt_created': definition('payments', ['notifications'],
        dict(ORDER_FIELDS, amount='integer', attempt_number='integer'),
        'Уведомить о повторной попытке оплаты'),
    'payment.failed': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, amount='integer', attempt_number='integer'),
        'Сохранить отказ; разрешить повторную попытку до отмены'),
    'payment.succeeded': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, amount='integer', attempt_number='integer'),
        'Отметить оплату и передать действующий заказ ресторану'),
    'order.sent_to_restaurant': definition('orders', ['catalog', 'notifications'], ORDER_FIELDS,
        'Создать локальный заказ ресторана и уведомление'),
    'restaurant.order_accepted': definition('catalog', ['orders', 'kitchen', 'delivery', 'notifications'], ORDER_FIELDS,
        'Отметить принятие; создать задания кухни и доставки'),
    'restaurant.order_rejected': definition('catalog', ['orders', 'notifications'],
        dict(ORDER_FIELDS, reason='string'),
        'Отменить заказ по отказу ресторана; уведомить клиента'),
    'kitchen.started': definition('kitchen', ['notifications'], ORDER_FIELDS,
        'Уведомить о начале приготовления'),
    'kitchen.ready': definition('kitchen', ['delivery', 'notifications'], ORDER_FIELDS,
        'Разрешить забор готового заказа; уведомить клиента'),
    'delivery.created': definition('delivery', ['notifications'],
        dict(ORDER_FIELDS, delivery_id='uuid'), 'Уведомить о создании доставки'),
    'courier.assigned': definition('delivery', ['orders', 'notifications'],
        dict(ORDER_FIELDS, delivery_id='uuid'), 'Сохранить курьера в заказе и уведомлении'),
    'delivery.picked_up': definition('delivery', ['notifications'],
        dict(ORDER_FIELDS, delivery_id='uuid'), 'Уведомить о получении заказа курьером'),
    'delivery.confirmed': definition('delivery', ['orders', 'notifications'],
        dict(ORDER_FIELDS, delivery_id='uuid'), 'Отметить доставку; разрешить подтверждение получения'),
    'order.cancelled': definition('orders', ['catalog', 'payments', 'notifications'],
        dict(ORDER_FIELDS, cause='string', reason='string'),
        'Закрыть заказ ресторана; вернуть оплаченную сумму; уведомить клиента'),
    'refund.requested': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, refund_id='uuid', amount='integer', cause='string'),
        'Связать возврат с заказом и уведомить о запросе'),
    'refund.succeeded': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, refund_id='uuid', amount='integer', cause='string'),
        'Отметить завершённый возврат и уведомить клиента'),
    'refund.failed': definition('payments', ['orders', 'notifications'],
        dict(ORDER_FIELDS, refund_id='uuid', amount='integer', cause='string'),
        'Сохранить неуспешный результат возврата и уведомление'),
    'order.receipt_confirmed': definition('orders', ['reviews', 'notifications'],
        dict(ORDER_FIELDS, receipt_confirmed='boolean'),
        'Разрешить отзыв только после подтверждения получения'),
    'review.created': definition('reviews', ['notifications'],
        dict(ORDER_FIELDS, review_id='uuid', target_type='string', target_id='uuid', score='integer'),
        'Уведомить о сохранении отзыва'),
    'rating.updated': definition('reviews', ['catalog', 'delivery'],
        {'target_type': 'string', 'target_id': 'uuid', 'rating': 'number', 'review_count': 'integer'},
        'Обновить рейтинг ресторана или курьера в собственной модели чтения')
}

SERVICES = ['profiles', 'catalog', 'orders', 'kitchen', 'delivery', 'payments', 'notifications', 'reviews']
SUBSCRIPTIONS = {s: [key for key, spec in EVENTS.items() if s in spec['consumers']] for s in SERVICES}


def order_data(order, **extra):
    data = {key: order.get(key) for key in ORDER_FIELDS}
    data['order_id'] = order.get('order_id', order.get('id'))
    data.update(extra)
    return data


def valid_field(value, datatype):
    if value is None and '|null' in datatype:
        return True
    datatype = datatype.split('|')[0]
    if datatype == 'uuid':
        uuid_text(value)
        return True
    return {'string': isinstance(value, str), 'integer': type(value) is int,
            'number': type(value) in {int, float}, 'boolean': type(value) is bool,
            'array': isinstance(value, list)}.get(datatype, False)


def validate_event(event):
    require(isinstance(event, dict), 'invalid_event', 'Сообщение должно быть объектом')
    uuid_text(event.get('event_id')); uuid_text(event.get('correlation_id'))
    kind = event.get('type')
    require(kind in EVENTS, 'unknown_event', 'Неизвестный тип сообщения')
    require(event.get('producer') == EVENTS[kind]['producer'], 'wrong_producer', 'Неверный издатель')
    try:
        stamp = datetime.fromisoformat(event.get('occurred_at', '').replace('Z', '+00:00'))
        require(stamp.tzinfo is not None, 'invalid_time', 'Нужно время с часовым поясом')
    except (ValueError, TypeError, AttributeError):
        require(False, 'invalid_time', 'Нужно время ISO 8601')
    data = event.get('data')
    require(isinstance(data, dict), 'invalid_data', 'data должен быть объектом')
    for key, datatype in EVENTS[kind]['fields'].items():
        require(key in data and valid_field(data[key], datatype), 'invalid_event_field', f'Неверное поле {key}')
    return event


def make_event(producer, kind, data):
    event_id = str(uuid4())
    event = {'event_id': event_id, 'type': kind, 'producer': producer, 'occurred_at': utcnow(),
             'correlation_id': data.get('order_id') or data.get('customer_id') or data.get('target_id') or event_id,
             'data': data}
    return validate_event(event)
