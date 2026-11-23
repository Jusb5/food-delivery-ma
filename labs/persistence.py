"""Preserve pending fanout/topic messages across an actual broker restart."""
import subprocess
import time
import pika
from common.broker import connect
from labs.queues import BASE


def verify():
    rows = []
    with connect() as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        for kind in ['fanout', 'topic']:
            exchange = BASE + '.persist.' + kind
            channel.exchange_declare(exchange, kind, durable=True)
            queues = [BASE+'_persist_'+kind+'_'+part for part in ['a', 'b']]
            for name in queues:
                channel.queue_declare(name, durable=True)
                channel.queue_bind(name, exchange, routing_key='order.#')
            task = ('pending###' if kind == 'fanout' else 'pending---').encode()
            channel.basic_publish(exchange, 'order.created', task, mandatory=True,
                                  properties=pika.BasicProperties(delivery_mode=2))
            rows.extend((kind, queue, task) for queue in queues)
    subprocess.run(['docker', 'compose', 'restart', 'rabbitmq'], check=True)
    for attempt in range(60):
        try:
            connection = connect()
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError('RabbitMQ did not become ready')
    with connection:
        channel = connection.channel()
        for kind, name, expected in rows:
            method, props, body = channel.basic_get(name, auto_ack=False)
            assert method is not None and body == expected and props.delivery_mode == 2
            channel.basic_ack(method.delivery_tag)
            print(f'{kind}: {name}; survived restart=True; persistent={props.delivery_mode == 2}', flush=True)
    print('PERSISTENT FANOUT AND TOPIC: 4 COPIES SURVIVED; PASS', flush=True)


if __name__ == '__main__':
    verify()
