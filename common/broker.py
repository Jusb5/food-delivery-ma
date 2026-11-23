import json
import os
import pika

EXCHANGE = 'food.events'


def connect():
    params = pika.URLParameters(os.getenv('AMQP_URL', 'amqp://student:student@127.0.0.1:5672/'))
    params.blocked_connection_timeout = 20
    params.socket_timeout = 10
    return pika.BlockingConnection(params)


def topology(channel):
    channel.exchange_declare(EXCHANGE, 'topic', durable=True)
    channel.exchange_declare('food.dead', 'fanout', durable=True)
    channel.queue_declare('food.dead.all', durable=True)
    channel.queue_bind('food.dead.all', 'food.dead')
    for name, keys in [('orders.events', ['payment.succeeded', 'delivery.confirmed']),
                       ('notifications.events', ['order.created', 'order.cancelled', 'order.receipt_confirmed'])]:
        channel.queue_declare(name, durable=True, arguments={'x-dead-letter-exchange': 'food.dead'})
        for key in keys:
            channel.queue_bind(name, EXCHANGE, routing_key=key)


def publish(channel, event):
    channel.basic_publish(EXCHANGE, event['type'], json.dumps(event, ensure_ascii=False).encode('utf-8'),
                          properties=pika.BasicProperties(delivery_mode=2, content_type='application/json', message_id=event['event_id']),
                          mandatory=True)
