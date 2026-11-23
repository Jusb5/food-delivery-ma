"""Actual HTTP requests to the two running Compose services."""
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
from tests.helpers import order_body
from common.broker import connect, topology, publish

P = Path(__file__).resolve().parents[1]/'evidence'
P.mkdir(exist_ok=True)


def request(port, path, method='GET', body=None, key=None, raw=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    data = raw if raw is not None else json.dumps(body).encode('utf-8') if body is not None else None
    req = Request(f'http://127.0.0.1:{port}/api/v1/{path}', data=data, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def wait_for(action, predicate):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        result = action()
        if predicate(result):
            return result
        time.sleep(.2)
    raise AssertionError('Timed out waiting for asynchronous effect')


def main():
    body = order_body();key = 'demo-'+uuid4().hex
    status, order = request(8001, 'orders', 'POST', body, key)
    assert status == 201 and order['total'] == 120000
    print('POST /orders ->', status, flush=True)
    print(json.dumps(order, ensure_ascii=False, indent=2), flush=True)
    (P/'created-order.json').write_text(json.dumps(order, ensure_ascii=False, indent=2), encoding='utf-8')
    repeated_status, repeated = request(8001, 'orders', 'POST', body, key)
    assert repeated_status == 200 and repeated['id'] == order['id']
    print('REPEAT SAME KEY -> 200; same id=True', flush=True)
    changed = dict(body, address='Другой адрес')
    assert request(8001, 'orders', 'POST', changed, key)[0] == 409
    assert request(8001, 'orders/'+str(uuid4()))[0] == 404
    assert request(8001, 'orders', 'POST', raw=b'{broken', key='bad')[0] == 400
    bad = order_body();bad['items'][0]['quantity'] = 0
    assert request(8001, 'orders', 'POST', bad, 'bad-quantity')[0] == 422
    print('ERROR CHECKS: changed body=409; absent=404; bad JSON=400; quantity=422: PASS', flush=True)
    notifications = wait_for(lambda: request(8002, 'notifications')[1], lambda x: any(n['order_id'] == order['id'] for n in x['items']))
    notification = next(n for n in notifications['items'] if n['order_id'] == order['id'])
    print(f"RABBITMQ -> NotificationService: {notification['text']}; order_id={order['id']}", flush=True)
    assert request(8002, 'notifications/'+notification['id']+'/read', 'POST', {})[1]['status'] == 'read'
    status, cancelled = request(8001, 'orders/'+order['id']+'/cancel', 'POST', {'reason': 'Изменились планы'})
    assert status == 202 and cancelled['status'] == 'cancelled'
    print('POST /orders/{id}/cancel -> 202 cancelled', flush=True)
    _, second = request(8001, 'orders', 'POST', body, 'flow-'+uuid4().hex)
    assert request(8001, 'orders/'+second['id']+'/receipt', 'POST', {})[0] == 409
    with connect() as connection:
        channel = connection.channel();topology(channel);channel.confirm_delivery()
        for kind, expected in [('payment.succeeded', 'paid'), ('delivery.confirmed', 'delivered')]:
            event = {'event_id': str(uuid4()), 'type': kind, 'data': {'order_id': second['id']}}
            publish(channel, event)
            wait_for(lambda: request(8001, 'orders/'+second['id'])[1], lambda x: x['status'] == expected)
            print(f'SIMULATED EXTERNAL EVENT {kind} -> status={expected}', flush=True)
    status, completed = request(8001, 'orders/'+second['id']+'/receipt', 'POST', {})
    assert status == 202 and completed['status'] == 'completed'
    print('POST /orders/{id}/receipt -> 202 completed; receipt_confirmed=True', flush=True)
    wait_for(lambda: request(8002, 'notifications')[1], lambda x: any(n['order_id'] == second['id'] and n['text'] == 'Получение заказа подтверждено' for n in x['items']))
    (P/'demo-context.json').write_text(json.dumps({'order_id': order['id'], 'completed_order_id': second['id'], 'notification_id': notification['id']}, indent=2), encoding='utf-8')
    print('TWO-SERVICE HTTP + RABBITMQ FLOW: PASS', flush=True)


if __name__ == '__main__':
    main()
