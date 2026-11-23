"""Practice 5: all three lifecycle variants against a real RabbitMQ."""
import sys
import time
import pika
from common.broker import connect

BASE = 'ikbo-67-23_bagdyan'


def exists(name):
    with connect() as connection:
        try:
            return connection.channel().queue_declare(name, passive=True).method.message_count
        except pika.exceptions.ChannelClosedByBroker as error:
            if error.reply_code == 404:
                return None
            raise


def exclusive():
    name = BASE + '_exclusive'
    owner = connect()
    channel = owner.channel()
    channel.queue_declare(name, exclusive=True)
    print(f'CREATE {name}: exclusive=True', flush=True)
    with connect() as sender:
        sender.channel().basic_publish('', name, b'order-001')
    method, _, body = channel.basic_get(name, auto_ack=False)
    assert body == b'order-001'
    channel.basic_ack(method.delivery_tag)
    print('RECEIVED order-001; ACK', flush=True)
    with connect() as outsider:
        try:
            outsider.channel().queue_declare(name, exclusive=True)
        except pika.exceptions.ChannelClosedByBroker as error:
            assert error.reply_code == 405
            print('OTHER CONNECTION: 405 RESOURCE_LOCKED', flush=True)
        else:
            raise AssertionError('Exclusive queue became accessible')
    owner.close()
    time.sleep(.2)
    assert exists(name) is None
    print('OWNER CLOSED -> queue deleted: PASS', flush=True)


def durable_prepare():
    name = BASE + '_durable'
    with connect() as connection:
        channel = connection.channel()
        channel.queue_declare(name, durable=True)
        channel.confirm_delivery()
        channel.basic_publish('', name, b'persist-after-restart', mandatory=True,
                              properties=pika.BasicProperties(delivery_mode=2))
    count = exists(name)
    assert count >= 1
    print(f'{name}: durable=True; delivery_mode=2; CONFIRMED', flush=True)
    print(f'BEFORE RESTART: ready={count}', flush=True)


def durable_check():
    name = BASE + '_durable'
    with connect() as connection:
        channel = connection.channel()
        channel.queue_declare(name, durable=True)
        method, props, body = channel.basic_get(name, auto_ack=False)
        assert method is not None and body == b'persist-after-restart'
        assert props.delivery_mode == 2
        print(f'AFTER RESTART: body={body.decode()}; delivery_mode={props.delivery_mode}', flush=True)
        channel.basic_ack(method.delivery_tag)
    print('DURABLE MESSAGE SURVIVED RESTART: PASS', flush=True)


def auto_delete():
    name = BASE + '_auto_delete'
    with connect() as connection:
        channel = connection.channel()
        channel.queue_declare(name, auto_delete=True)
        got = []

        def received(ch, method, properties, body):
            got.append(body)
            ch.basic_ack(method.delivery_tag)

        tag = channel.basic_consume(name, received, auto_ack=False)
        with connect() as sender:
            sender.channel().basic_publish('', name, b'auto-delete-message')
        deadline = time.monotonic()+5
        while not got and time.monotonic() < deadline:
            connection.process_data_events(time_limit=.1)
        assert got == [b'auto-delete-message']
        print(f'{name}: auto_delete=True; received=1; ACK', flush=True)
        channel.basic_cancel(tag)
        assert exists(name) is None
        print(f'LAST CONSUMER CANCELLED -> queue deleted; connection_open={connection.is_open}: PASS', flush=True)


def redelivery():
    name = BASE + '_redelivery'
    owner = connect();channel = owner.channel()
    channel.queue_declare(name, durable=True)
    channel.basic_publish('', name, b'retry-me', properties=pika.BasicProperties(delivery_mode=2))
    first, _, body = channel.basic_get(name, auto_ack=False)
    assert first is not None
    print(f'FIRST RECEIVE: body={body.decode()}; no ACK; connection closed', flush=True)
    owner.close()
    with connect() as connection:
        channel = connection.channel()
        method, _, body = channel.basic_get(name, auto_ack=False)
        assert body == b'retry-me' and method.redelivered
        channel.basic_ack(method.delivery_tag)
        print(f'SECOND RECEIVE: redelivered={method.redelivered}; ACK: PASS', flush=True)


if __name__ == '__main__':
    {'exclusive': exclusive, 'durable-prepare': durable_prepare,
     'durable-check': durable_check, 'auto-delete': auto_delete,
     'redelivery': redelivery}[sys.argv[1]]()
