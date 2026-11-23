"""Pika connections belong to one worker thread each."""
import json
import os
import threading
import time
import pika
from extension.core.domain import DomainError
from extension.core.events import SUBSCRIPTIONS

EXCHANGE = 'food.events'
DEAD_EXCHANGE = 'food.dead'
AUDIT_QUEUE = 'food.audit'


def connect():
    params = pika.URLParameters(os.getenv('AMQP_URL', 'amqp://student:student@127.0.0.1:5672/'))
    params.heartbeat = 30
    params.blocked_connection_timeout = 10
    params.socket_timeout = 5
    return pika.BlockingConnection(params)


def topology(channel):
    channel.exchange_declare(EXCHANGE, 'topic', durable=True)
    channel.exchange_declare(DEAD_EXCHANGE, 'fanout', durable=True)
    channel.queue_declare('food.dead.all', durable=True)
    channel.queue_bind('food.dead.all', DEAD_EXCHANGE)
    channel.queue_declare(AUDIT_QUEUE, durable=True)
    channel.queue_bind(AUDIT_QUEUE, EXCHANGE, routing_key='#')
    for service, keys in SUBSCRIPTIONS.items():
        if not keys:
            continue
        queue = service+'.events'
        channel.queue_declare(queue, durable=True, auto_delete=False,
                              arguments={'x-dead-letter-exchange': DEAD_EXCHANGE})
        for key in keys:
            channel.queue_bind(queue, EXCHANGE, routing_key=key)


def publish(channel, event):
    channel.basic_publish(EXCHANGE, event['type'], json.dumps(event, ensure_ascii=False).encode(),
        mandatory=True, properties=pika.BasicProperties(delivery_mode=2, content_type='application/json',
        message_id=event['event_id'], correlation_id=event['correlation_id']))


class Workers:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()

    def start(self):
        threading.Thread(target=self.publisher, daemon=True, name='outbox-publisher').start()
        if SUBSCRIPTIONS[self.service.name]:
            threading.Thread(target=self.consumer, daemon=True, name='event-consumer').start()

    def publisher(self):
        while not self.stop.is_set():
            try:
                with connect() as connection:
                    channel = connection.channel(); topology(channel); channel.confirm_delivery()
                    self.service.bus_status['publisher'] = True
                    while not self.stop.is_set():
                        for row in self.service.store.pending():
                            event = json.loads(row['payload'])
                            publish(channel, event)
                            self.service.store.mark_sent(event['event_id'])
                            print('PUBLISH CONFIRMED', event['type'], event['event_id'], flush=True)
                        connection.process_data_events(time_limit=0)
                        self.stop.wait(.2)
            except Exception as error:
                self.service.bus_status['publisher'] = False
                print('PUBLISH RECONNECT', type(error).__name__, str(error), flush=True)
                self.stop.wait(1)

    def consumer(self):
        queue = self.service.name+'.events'
        while not self.stop.is_set():
            try:
                with connect() as connection:
                    channel = connection.channel(); topology(channel); channel.confirm_delivery()
                    channel.basic_qos(prefetch_count=1)
                    self.service.bus_status['consumer'] = True
                    for method, props, body in channel.consume(queue, auto_ack=False, inactivity_timeout=1):
                        if self.stop.is_set():
                            break
                        if method is None:
                            continue
                        try:
                            event = json.loads(body.decode())
                            changed = self.service.handle(event)
                        except Exception as error:
                            attempts = (props.headers or {}).get('attempts', 0)
                            if type(attempts) is not int:
                                attempts = 3
                            invalid = isinstance(error, (ValueError, UnicodeError, TypeError, KeyError)) or (
                                isinstance(error, DomainError) and error.status == 422)
                            if invalid or attempts >= 3:
                                print('DEAD', queue, str(error), flush=True)
                                channel.basic_nack(method.delivery_tag, requeue=False)
                            else:
                                time.sleep(min(2**attempts, 4))
                                channel.basic_publish('', queue, body, mandatory=True,
                                    properties=pika.BasicProperties(delivery_mode=2, content_type='application/json',
                                    message_id=props.message_id, correlation_id=props.correlation_id,
                                    headers={'attempts': attempts+1}))
                                channel.basic_ack(method.delivery_tag)
                            continue
                        channel.basic_ack(method.delivery_tag)
                        print('ACK', event['type'], 'changed='+str(changed), event['event_id'], flush=True)
                    channel.cancel()
            except Exception as error:
                self.service.bus_status['consumer'] = False
                print('CONSUMER RECONNECT', type(error).__name__, str(error), flush=True)
                self.stop.wait(1)
