from uuid import uuid4
import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit
from extension.core.base import Service
from extension.core.domain import uuid_text, require, text, utcnow, payment_transition
from extension.core.events import order_data


class PaymentService(Service):
    name = 'payments'
    ROUTES = [
        ('GET', '/payments/{payment_id}', 'get', 'Состояние платежа'),
        ('POST', '/payments/{payment_id}/attempts', 'attempt', 'Повторить оплату'),
        ('GET', '/refunds/{refund_id}', 'refund', 'Состояние возврата'),
        ('POST', '/provider-events', 'provider', 'Принять учебный callback провайдера')]

    def get(self, payment_id, **_):
        return 200, self.get_record('payments', payment_id)

    def refund(self, refund_id, **_):
        return 200, self.get_record('refunds', refund_id)

    def attempt(self, payment_id, body, **_):
        payment_id = uuid_text(payment_id)
        return_url = text(body.get('return_url'), 'return_url', 1000)
        url = urlsplit(return_url)
        require(url.scheme == 'https' and bool(url.hostname), 'invalid_return_url', 'Нужен HTTPS URL')
        with self.store.transaction() as tx:
            payment = tx.get('payments', payment_id)
            require(not payment['order_cancelled'] and payment['status'] == 'failed',
                    'cannot_retry', 'Повтор разрешён только после отказа по действующему заказу', 409)
            require(payment['attempt_number'] < 2, 'attempt_limit', 'Повторная попытка уже использована', 409)
            payment.update(status='pending', attempt_number=2, attempt_id=str(uuid4()), return_url=return_url)
            tx.put('payments', payment_id, payment)
            tx.emit('payment.attempt_created', self.payment_data(payment))
        return 202, {'payment_id': payment_id, 'attempt_id': payment['attempt_id'], 'status': 'pending',
                     'attempt_number': 2, 'payment_url': 'https://provider.example/pay/'+payment_id}

    @staticmethod
    def payment_data(payment):
        return order_data(payment['order'], payment_id=payment['payment_id'], status=payment['status'],
                          amount=payment['amount'], attempt_number=payment['attempt_number'])

    def request_refund(self, tx, payment):
        if payment['refund_id']:
            return
        refund = {'refund_id': str(uuid4()), 'payment_id': payment['payment_id'],
                  'order_id': payment['order']['order_id'], 'amount': payment['amount'],
                  'status': 'pending', 'cause': payment['cancel_cause'], 'created_at': utcnow()}
        payment['refund_id'] = refund['refund_id']
        tx.put('payments', payment['payment_id'], payment)
        tx.put('refunds', refund['refund_id'], refund)
        tx.emit('refund.requested', order_data(payment['order'], payment_id=payment['payment_id'],
                refund_id=refund['refund_id'], status='pending', amount=refund['amount'], cause=refund['cause']))

    def provider(self, body, headers, **_):
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        secret = os.getenv('PROVIDER_SECRET', 'local-study-secret').encode()
        expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
        require(hmac.compare_digest(headers.get('X-Provider-Signature', ''), expected),
                'invalid_signature', 'Неверная подпись callback', 401)
        provider_id = text(body.get('provider_event_id'), 'provider_event_id', 200)
        target = body.get('status')
        require(target in {'succeeded','failed'}, 'invalid_status', 'succeeded или failed')
        digest = hashlib.sha256(raw).hexdigest()
        with self.store.transaction() as tx:
            previous = tx.get('provider_events', provider_id, False)
            if previous:
                require(previous['digest'] == digest, 'provider_event_conflict', 'Событие уже принято с другим телом', 409)
                return 202, {'accepted': True, 'duplicate': True}
            if 'refund_id' in body:
                refund_id = uuid_text(body['refund_id'])
                refund = tx.get('refunds', refund_id)
                require(refund['status'] == 'pending', 'refund_closed', 'Возврат уже обработан', 409)
                refund['status'] = target
                tx.put('refunds', refund_id, refund)
                payment = tx.get('payments', refund['payment_id'])
                tx.emit('refund.'+target, order_data(payment['order'], payment_id=payment['payment_id'],
                    refund_id=refund_id, status=target, amount=refund['amount'], cause=refund['cause']))
            else:
                payment_id = uuid_text(body.get('payment_id'))
                payment = tx.get('payments', payment_id)
                require(body.get('attempt_id',payment['attempt_id']) == payment['attempt_id'], 'wrong_attempt', 'Callback относится к другой попытке', 409)
                changed = payment['status'] != target
                payment['status'] = payment_transition(payment['status'], target)
                tx.put('payments', payment_id, payment)
                if changed:
                    tx.emit('payment.'+target, self.payment_data(payment))
                if target == 'succeeded' and payment['order_cancelled']:
                    self.request_refund(tx, payment)
            tx.put('provider_events', provider_id, {'digest': digest})
        return 202, {'accepted': True, 'duplicate': False}

    def react(self, tx, kind, data):
        if kind == 'order.created':
            if tx.get('payment_by_order', data['order_id'], False):
                return
            payment_id = str(uuid4())
            payment = {'payment_id': payment_id, 'order': data, 'order_id': data['order_id'],
                       'amount': data['total'], 'currency': 'RUB', 'status': 'pending',
                       'attempt_id': str(uuid4()), 'attempt_number': 1, 'refund_id': None,
                       'order_cancelled': False, 'cancel_cause': None, 'return_url': None}
            tx.put('payments', payment_id, payment)
            tx.put('payment_by_order', data['order_id'], {'payment_id': payment_id})
            tx.emit('payment.created', self.payment_data(payment))
        elif kind == 'order.cancelled':
            link = tx.get('payment_by_order', data['order_id'])
            payment = tx.get('payments', link['payment_id'])
            payment.update(order_cancelled=True, cancel_cause=data['cause'])
            tx.put('payments', payment['payment_id'], payment)
            if payment['status'] == 'succeeded':
                self.request_refund(tx, payment)
