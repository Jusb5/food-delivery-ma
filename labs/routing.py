"""Practice 5: fanout, direct, topic; independent connections for consumers."""
from concurrent.futures import ThreadPoolExecutor
import sys
import time
import pika
from common.broker import connect
from labs.queues import BASE


def run_worker(queue, symbol, expected):
    with connect() as connection:
        channel = connection.channel()
        channel.basic_qos(prefetch_count=1)
        seen = []
        def received(ch, method, props, body):
            task = body.decode('utf-8')
            duration = task.count(symbol)
            start = time.monotonic()
            time.sleep(duration)
            ch.basic_ack(method.delivery_tag)
            elapsed = time.monotonic()-start
            print(f'{queue}: {task!r}; sleep={duration}s; elapsed={elapsed:.3f}s; ACK', flush=True)
            seen.append((task, props.delivery_mode))
            if len(seen) == expected:
                ch.stop_consuming()
        channel.basic_consume(queue, received, auto_ack=False)
        channel.start_consuming()
        assert channel.basic_get(queue, auto_ack=True)[0] is None
        return seen


def demonstrate(mode):
    symbol = {'fanout': '#', 'direct': '*', 'topic': '-'}[mode]
    durable = mode != 'direct'
    exchange = BASE + '.' + mode
    q1, q2 = BASE+'_'+mode+'_a', BASE+'_'+mode+'_b'
    with connect() as connection:
        channel = connection.channel()
        channel.exchange_declare(exchange, mode, durable=durable)
        for queue in [q1, q2]:
            channel.queue_declare(queue, durable=durable)
        if mode == 'fanout':
            bindings, messages, expected = ['', ''], [('', 'order###')], [1, 1]
        elif mode == 'direct':
            bindings, messages, expected = ['kitchen', 'notifications'], [('kitchen', 'cook**'), ('notifications', 'notify*')], [1, 1]
        else:
            bindings = ['order.*', 'order.#']
            messages = [('order.created', 'created-'), ('order.delivery.completed', 'completed--'), ('payment.succeeded', 'unmatched')]
            expected = [1, 2]
        for queue, key in zip([q1, q2], bindings):
            channel.queue_bind(queue, exchange, routing_key=key)
        channel.confirm_delivery()
        print(f'{mode.upper()}: durable={durable}; delay symbol={symbol}; bindings={bindings}', flush=True)
        for key, body in messages:
            channel.basic_publish(exchange, key, body.encode(),
                                  properties=pika.BasicProperties(delivery_mode=2 if durable else 1))
            print(f'PUBLISH key={key!r} task={body!r}', flush=True)
        counts = [channel.queue_declare(q, passive=True).method.message_count for q in [q1, q2]]
        assert counts == expected, (counts, expected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        workers = [pool.submit(run_worker, q, symbol, n) for q, n in zip([q1, q2], expected)]
        results = [w.result() for w in workers]
    assert [len(x) for x in results] == expected
    print(f'ROUTING counts={expected}; all messages processed: PASS', flush=True)


if __name__ == '__main__':
    demonstrate(sys.argv[1])
