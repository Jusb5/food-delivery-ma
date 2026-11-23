from uuid import uuid4
from extension.core.base import Service
from extension.core.domain import text, phone_number, uuid_text, utcnow, require


class ProfileService(Service):
    name = 'profiles'
    ROUTES = [
        ('POST', '/customers', 'create', 'Зарегистрировать клиента'),
        ('GET', '/customers/{customer_id}', 'get', 'Получить профиль'),
        ('PATCH', '/customers/{customer_id}', 'update', 'Изменить имя'),
        ('POST', '/customers/{customer_id}/addresses', 'add_address', 'Сохранить адрес'),
        ('GET', '/customers/{customer_id}/addresses', 'addresses', 'Получить адреса')]

    def create(self, body, **_):
        name, phone = text(body.get('name'), 'name', 100), phone_number(body.get('phone'))
        with self.store.transaction() as tx:
            require(tx.get('phones', phone, False) is None, 'phone_exists', 'Телефон уже зарегистрирован', 409)
            customer = {'id': str(uuid4()), 'name': name, 'phone': phone, 'created_at': utcnow()}
            tx.put('customers', customer['id'], customer)
            tx.put('phones', phone, {'customer_id': customer['id']})
            tx.emit('customer.registered', dict(customer_id=customer['id'], name=name, phone=phone))
        return 201, customer

    def get(self, customer_id, **_):
        return 200, self.get_record('customers', customer_id)

    def update(self, customer_id, body, **_):
        customer_id = uuid_text(customer_id)
        name = text(body.get('name'), 'name', 100)
        with self.store.transaction() as tx:
            customer = tx.get('customers', customer_id)
            customer['name'] = name
            tx.put('customers', customer_id, customer)
            tx.emit('customer.updated', dict(customer_id=customer_id, name=name, phone=customer['phone']))
        return 200, customer

    def add_address(self, customer_id, body, **_):
        customer_id = uuid_text(customer_id)
        address = text(body.get('address'), 'address', 500)
        comment = body.get('comment', '')
        require(isinstance(comment, str) and len(comment) <= 300, 'invalid_comment', 'Комментарий до 300 символов')
        result = {'address_id': str(uuid4()), 'customer_id': customer_id, 'address': address, 'comment': comment}
        with self.store.transaction() as tx:
            tx.get('customers', customer_id)
            tx.put('addresses', result['address_id'], result)
            tx.emit('address.saved', result)
        return 201, result

    def addresses(self, customer_id, query, **_):
        customer_id = uuid_text(customer_id)
        self.get_record('customers', customer_id)
        return 200, self.page('addresses', query, lambda a: a['customer_id'] == customer_id)
