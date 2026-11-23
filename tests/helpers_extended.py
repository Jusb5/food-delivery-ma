import hashlib
import hmac
import json
import os
from uuid import uuid4
from extension.core.domain import utcnow
from extension.core.events import EVENTS, make_event
from extension.core.store import Store
from extension.services.catalog import RESTAURANT_ID, DISH_ID

CUSTOMER = '11111111-1111-4111-8111-111111111111'
ORDER = '44444444-4444-4444-8444-444444444444'
COURIER = '55555555-5555-4555-8555-555555555555'


def snapshot(**extra):
    result = dict(order_id=ORDER, customer_id=CUSTOMER, restaurant_id=RESTAURANT_ID,
        address='Москва, проспект Вернадского, 78',
        items=[{'dish_id':DISH_ID,'quantity':2,'unit_price':60000}], total=120000,
        status='awaiting_payment',payment_id=None,courier_id=COURIER)
    result.update(extra)
    return result


def event(kind, data):
    return make_event(EVENTS[kind]['producer'], kind, data)


def signature(body):
    raw=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return {'X-Provider-Signature':hmac.new(os.getenv('PROVIDER_SECRET','local-study-secret').encode(),raw,hashlib.sha256).hexdigest()}


class FakePeers:
    def __init__(self): self.calls=[]
    def get(self, service, path):
        self.calls.append((service,path))
        if service=='profiles': return {'id':CUSTOMER,'name':'Клиент','phone':'+79990000000'}
        if service=='catalog': return {'items':[{'dish_id':DISH_ID,'price':60000,'available':True}],'total':1}
        if service=='kitchen': return {'order_id':ORDER,'status':'ready'}
        if service=='reviews': return {'rating':0,'review_count':0}
        if service=='orders':
            result=snapshot();result.pop('order_id');result.update(id=ORDER,status='completed',receipt_confirmed=True)
            return result
        raise AssertionError('Unexpected peer')


def test_store(service):
    url=os.getenv('TEST_POSTGRES_URL','postgresql://lab_test:lab_test@127.0.0.1:15433/food_tests')
    return Store(url,service,'test_'+uuid4().hex)
