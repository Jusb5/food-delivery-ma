import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from extension.core.domain import DomainError
from extension.core.store import Transaction
from tests.helpers_extended import test_store, FakePeers, snapshot, event, signature, CUSTOMER, ORDER, COURIER
from extension.services.catalog import CatalogService, RESTAURANT_ID, DISH_ID
from extension.services.profiles import ProfileService
from extension.services.kitchen import KitchenService
from extension.services.delivery import DeliveryService
from extension.services.payments import PaymentService
from extension.services.reviews import ReviewService


class AddedServicesCases(unittest.TestCase):
    def build(self, cls):
        store=test_store(cls.name)
        self.addCleanup(self.drop_schema,store)
        return cls(store,FakePeers())

    @staticmethod
    def drop_schema(store):
        assert store.schema.startswith('test_')
        with psycopg.connect(store.url,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(store.schema)))

    def test_profiles_persist_and_reject_duplicate_phone(self):
        service=self.build(ProfileService)
        code,profile=service.create(body={'name':'Иван','phone':'+79990000000'})
        self.assertEqual(code,201);self.assertEqual(service.get(profile['id'])[1]['phone'],profile['phone'])
        with self.assertRaises(DomainError): service.create(body={'name':'Другой','phone':profile['phone']})
        address=service.add_address(profile['id'],body={'address':'Москва, Вернадского, 78'})[1]
        self.assertEqual(service.addresses(profile['id'],query={})[1]['items'][0]['address_id'],address['address_id'])

    def test_catalog_dish_changes_are_persistent(self):
        service=self.build(CatalogService)
        dish=service.add_dish(RESTAURANT_ID,body={'name':'Суп','price':30000,'available':True})[1]
        service.update_dish(dish['dish_id'],body={'price':32000,'available':False})
        rows=service.menu(RESTAURANT_ID,query={})[1]['items']
        self.assertEqual(next(r for r in rows if r['dish_id']==dish['dish_id'])['price'],32000)
        self.assertEqual(len(service.restaurants(query={})[1]['items']),1)
        self.assertEqual(service.peers.calls[-1][0],'reviews')


    def test_kitchen_deduplicates_accepted_order(self):
        service=self.build(KitchenService);incoming=event('restaurant.order_accepted',snapshot(status='accepted'))
        self.assertTrue(service.handle(incoming));self.assertFalse(service.handle(incoming))
        self.assertEqual(service.list(query={})[1]['total'],1)
        service.start(ORDER);self.assertEqual(service.ready(ORDER)[1]['status'],'ready')

    def test_delivery_persists_assign_pickup_complete(self):
        service=self.build(DeliveryService)
        service.handle(event('restaurant.order_accepted',snapshot(status='accepted')))
        service.handle(event('kitchen.ready',snapshot(status='ready')))
        delivery=service.list(query={})[1]['items'][0]
        service.assign(delivery['delivery_id'],body={'courier_id':COURIER})
        service.pickup(delivery['delivery_id'])
        self.assertEqual(service.complete(delivery['delivery_id'])[1]['status'],'delivered')
        self.assertEqual(service.peers.calls[-1][0],'kitchen')

    def test_payments_retry_and_refund_are_persistent(self):
        service=self.build(PaymentService);service.handle(event('order.created',snapshot()))
        with service.store.transaction() as tx:payment=tx.all('payments')[0]
        def callback(body):return service.provider(body=body,headers=signature(body))
        body={'provider_event_id':'failed1','payment_id':payment['payment_id'],
              'attempt_id':payment['attempt_id'],'status':'failed'}
        callback(body);self.assertTrue(callback(body)[1]['duplicate'])
        attempt=service.attempt(payment['payment_id'],body={'return_url':'https://client.test/return'})[1]
        callback(dict(body,provider_event_id='paid2',attempt_id=attempt['attempt_id'],status='succeeded'))
        service.handle(event('order.cancelled',dict(snapshot(),cause='customer_paid',reason='Изменились планы')))
        payment=service.get(payment['payment_id'])[1]
        refund=service.refund(payment['refund_id'])[1]
        self.assertEqual(refund['status'],'pending')
        callback({'provider_event_id':'refund1','refund_id':refund['refund_id'],'status':'succeeded'})
        self.assertEqual(service.refund(refund['refund_id'])[1]['status'],'succeeded')


    def test_reviews_hide_recalculates_rating(self):
        service=self.build(ReviewService)
        service.handle(event('order.receipt_confirmed',snapshot(status='completed',receipt_confirmed=True)))
        body={'order_id':ORDER,'target_type':'restaurant','target_id':RESTAURANT_ID,'score':5,'text':'Всё хорошо'}
        review=service.create(body=body)[1]
        self.assertEqual(service.rating('restaurant',RESTAURANT_ID)[1]['rating'],5)
        service.hide(review['review_id'],body={'reason':'Проверка модерации'})
        self.assertEqual(service.rating('restaurant',RESTAURANT_ID)[1]['review_count'],0)
        self.assertEqual(service.list(query={})[1]['total'],0)
