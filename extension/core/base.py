from extension.core.domain import uuid_text, pagination
from extension.core.events import validate_event, SUBSCRIPTIONS
from extension.core.peer import ServiceClient


class Service:
    ROUTES = []

    def __init__(self, store, peers=None):
        self.store = store
        self.peers = peers or ServiceClient(self.name)
        self.bus_status = {'publisher': False, 'consumer': False if SUBSCRIPTIONS[self.name] else 'not_required'}

    def handle(self, event):
        validate_event(event)
        if event['type'] not in SUBSCRIPTIONS[self.name]:
            return False
        with self.store.transaction() as tx:
            if tx.seen(event['event_id']):
                return False
            self.react(tx, event['type'], event['data'])
            tx.remember(event['event_id'])
        return True

    def get_record(self, kind, record_id):
        with self.store.transaction() as tx:
            return tx.get(kind, uuid_text(record_id))

    def page(self, kind, query, predicate=None):
        limit, offset = pagination(query)
        with self.store.transaction() as tx:
            rows = tx.all(kind)
        if predicate:
            rows = [r for r in rows if predicate(r)]
        return {'items': rows[offset:offset+limit], 'total': len(rows)}
