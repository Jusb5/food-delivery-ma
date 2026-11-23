"""Pure business validation, shared by the HTTP and message interfaces."""
from datetime import datetime, timezone
from uuid import UUID
import re


from common.domain import DomainError


def require(condition, code, message, status=422):
    if not condition:
        raise DomainError(status, code, message)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def uuid_text(value):
    try:
        require(isinstance(value, str), 'invalid_uuid', 'Ожидается UUID')
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise DomainError(422, 'invalid_uuid', 'Ожидается UUID')


def text(value, field, maximum=300):
    require(isinstance(value, str) and 1 <= len(value.strip()) <= maximum,
            'invalid_'+field, f'{field}: строка длиной 1-{maximum}')
    return value.strip()


def integer(value, field, low=1, high=2147483647):
    require(type(value) is int and low <= value <= high,
            'invalid_'+field, f'{field}: целое число {low}-{high}')
    return value


def phone_number(value):
    require(isinstance(value, str) and re.fullmatch(r'\+[1-9][0-9]{7,14}', value),
            'invalid_phone', 'Телефон в формате E.164, например +79991234567')
    return value


def dish_values(body):
    require(isinstance(body, dict), 'invalid_body', 'Ожидается объект')
    result = dict(body)
    if 'name' in body:
        result['name'] = text(body['name'], 'name', 200)
    if 'price' in body:
        result['price'] = integer(body['price'], 'price')
    if 'available' in body:
        require(type(body['available']) is bool, 'invalid_available', 'available: boolean')
    return result


def calculate_total(items, menu):
    require(isinstance(items, list) and 1 <= len(items) <= 100,
            'invalid_items', 'Нужен список из 1-100 позиций')
    result, total = [], 0
    seen = set()
    for item in items:
        require(isinstance(item, dict), 'invalid_item', 'Позиция должна быть объектом')
        dish_id = uuid_text(item.get('dish_id'))
        quantity = integer(item.get('quantity'), 'quantity', 1, 100)
        require(dish_id not in seen, 'duplicate_dish', 'Объедините одинаковые позиции')
        seen.add(dish_id)
        require(dish_id in menu and menu[dish_id]['available'], 'unavailable_dish', 'Блюдо недоступно')
        price = integer(menu[dish_id]['price'], 'price')
        total += price * quantity
        result.append({'dish_id': dish_id, 'quantity': quantity, 'unit_price': price})
    return total, result


def kitchen_transition(current, action):
    transitions = {('accepted', 'start'): 'cooking', ('cooking', 'ready'): 'ready'}
    require((current, action) in transitions, 'invalid_kitchen_transition',
            'Недопустимый переход состояния кухни', 409)
    return transitions[current, action]


def pickup_allowed(delivery, kitchen):
    require(delivery['status'] == 'assigned' and delivery.get('courier_id') is not None,
            'courier_required', 'Сначала назначьте курьера', 409)
    require(kitchen['status'] == 'ready', 'kitchen_not_ready', 'Кухня ещё не готова', 409)
    return True


def payment_transition(current, target):
    require(target in {'succeeded', 'failed'}, 'invalid_payment_status', 'Ожидается succeeded или failed')
    require(current == 'pending' or current == target,
            'invalid_payment_transition', 'Попытка оплаты уже завершена', 409)
    return target


def review_values(body):
    require(isinstance(body, dict), 'invalid_body', 'Ожидается объект')
    require(body.get('target_type') in {'restaurant', 'courier'}, 'invalid_target', 'restaurant или courier')
    integer(body.get('score'), 'score', 1, 5)
    comment = body.get('text', '')
    require(isinstance(comment, str) and len(comment) <= 2000, 'invalid_text', 'Текст до 2000 символов')
    return {'order_id': uuid_text(body.get('order_id')), 'target_id': uuid_text(body.get('target_id')),
            'target_type': body['target_type'], 'score': body['score'], 'text': comment.strip()}


def rating_values(reviews):
    visible = [r['score'] for r in reviews if not r['hidden']]
    return {'rating': round(sum(visible)/len(visible), 2) if visible else 0,
            'review_count': len(visible)}


def pagination(query):
    try:
        limit, offset = int(query.get('limit', '20')), int(query.get('offset', '0'))
        require(1 <= limit <= 100 and offset >= 0, 'invalid_pagination', 'limit 1-100, offset >= 0')
        return limit, offset
    except (ValueError, TypeError):
        raise DomainError(422, 'invalid_pagination', 'limit 1-100, offset >= 0')
