"""Verify live HTTP metrics and a connected trace through the backend APIs."""
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

PROMETHEUS = 'http://127.0.0.1:19090'
JAEGER = 'http://127.0.0.1:16686'
ROUTE = 'POST /api/v1/orders'
POSTS = 'sum(food_http_requests_total{job="orders",method="POST",route="/api/v1/orders",status="201"})'
ERRORS = 'sum(food_http_requests_total{job="orders",route="unmatched",status="404"})'
SERVICES = {'orders', 'notifications', 'profiles', 'catalog', 'kitchen', 'delivery', 'payments', 'reviews'}


def get_json(url):
    with urlopen(url, timeout=4) as response:
        return json.load(response)


def require_eventually(probe, description, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError(description)


def metric(query):
    result = get_json(f'{PROMETHEUS}/api/v1/query?' + urlencode({'query': query}))
    values = result['data']['result']
    return float(values[0]['value'][1]) if values else 0


def create_order():
    body = {'customer_id': '11111111-1111-4111-8111-111111111111',
            'restaurant_id': '22222222-2222-4222-8222-222222222222',
            'address': 'Учебный адрес',
            'items': [{'dish_id': '33333333-3333-4333-8333-333333333333', 'quantity': 2}]}
    req = Request('http://127.0.0.1:8001/api/v1/orders', method='POST',
                  data=json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json', 'Idempotency-Key': uuid4().hex})
    with urlopen(req, timeout=8) as response:
        assert response.status == 201
        return json.load(response)['id']


def targets_up():
    data = get_json(f'{PROMETHEUS}/api/v1/targets')['data']['activeTargets']
    targets = {t['labels']['job']: t['health'] for t in data}
    return targets if targets == dict.fromkeys(SERVICES, 'up') else None


def traces_since(start):
    query = urlencode({'service': 'orders', 'operation': ROUTE, 'start': start, 'limit': 20})
    result = get_json(f'{JAEGER}/api/traces?{query}')
    for trace in result['data']:
        services = {process['serviceName'] for process in trace['processes'].values()}
        if {'orders', 'profiles', 'catalog'} <= services:
            spans = {span['spanID']: span for span in trace['spans']}
            root = next(span for span in spans.values() if span['operationName'] == ROUTE)
            for span in spans.values():
                if span is root:
                    continue
                assert span['traceID'] == trace['traceID']
                assert any(ref['refType'] == 'CHILD_OF' and ref['spanID'] in spans
                           for ref in span['references']), span
            return trace, root


def main():
    targets = require_eventually(targets_up, 'Not all eight scrape targets are up')
    before, errors_before = metric(POSTS), metric(ERRORS)
    start = time.time_ns() // 1000
    sent = 6
    for _ in range(sent):
        time.sleep(3)
        create_order()
    try:
        get_json('http://127.0.0.1:8001/api/v1/no-such-route')
        raise AssertionError('Expected HTTP 404')
    except HTTPError as exc:
        assert exc.code == 404
    require_eventually(lambda: metric(POSTS) >= before + sent, 'Missing POST metrics')
    require_eventually(lambda: metric(ERRORS) >= errors_before + 1, 'Missing HTTP 404 metric')
    trace, root = require_eventually(lambda: traces_since(start), 'Missing connected Jaeger trace')
    evidence = Path('evidence')
    evidence.mkdir(exist_ok=True)
    (evidence/'jaeger-trace.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
    result = dict(
        targets=targets, post_orders_201=dict(before=int(before), after=int(metric(POSTS)), sent=sent),
        unmatched_404_delta=int(metric(ERRORS) - errors_before), trace_id=trace['traceID'],
        span_count=len(trace['spans']),
        services=sorted({p['serviceName'] for p in trace['processes'].values()}),
        duration_ms=round(root['duration'] / 1000, 3),
    )
    data = json.dumps(result, indent=2)
    (evidence/'observability.json').write_text(data, encoding='utf-8')
    print(data)


if __name__ == '__main__':
    main()
