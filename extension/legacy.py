"""Integration additions to the accepted two services; their original modules stay unchanged."""
import json
from uuid import uuid4
from common.db import Database
from common.domain import DomainError,utcnow,uuid_text,pagination
from orders.service import OrderService as AcceptedOrderService
from notifications.service import NotificationService as AcceptedNotificationService
from extension.core.events import order_data,validate_event,SUBSCRIPTIONS
from extension.core.peer import ServiceClient


class LegacyOutbox:
    def __init__(self,db):self.db=db
    def pending(self):
        with self.db.transaction() as tx:
            return tx.all('SELECT event_id,payload FROM outbox WHERE sent_at IS NULL ORDER BY created_at LIMIT 50')
    def mark_sent(self,event_id):
        with self.db.transaction() as tx:tx.execute('UPDATE outbox SET sent_at=? WHERE event_id=?',(utcnow(),event_id))


class OrderService(AcceptedOrderService):
    name='orders'
    ROUTES=[('POST','/orders'),('GET','/orders'),('GET','/orders/{order_id}'),
            ('POST','/orders/{order_id}/cancel'),('POST','/orders/{order_id}/receipt')]
    def __init__(self,db,peers=None):
        super().__init__(db);self.peers=peers or ServiceClient(self.name)
        self.store=LegacyOutbox(db);self.bus_status={'publisher':False,'consumer':False}

    def create(self,body,key):
        if not isinstance(body,dict) or not isinstance(key,str) or not 1<=len(key.strip())<=128:
            return super().create(body,key)
        with self.db.transaction() as tx:
            existing=tx.one('SELECT id FROM orders WHERE idem_key=?',(key,))
        if existing:return super().create(body,key)
        self.peers.get('profiles','customers/'+uuid_text(body.get('customer_id')))
        restaurant=uuid_text(body.get('restaurant_id'))
        menu=self.peers.get('catalog','restaurants/'+restaurant+'/menu?limit=100')
        with self.db.transaction() as tx:
            tx.lock_key('catalog:'+restaurant)
            tx.execute('DELETE FROM catalog WHERE restaurant_id=?',(restaurant,))
            for dish in menu['items']:
                if dish['available']:tx.execute('INSERT INTO catalog VALUES(?,?,?)',(dish['dish_id'],restaurant,dish['price']))
        return super().create(body,key)

    def list(self,limit=20,offset=0,status=None):
        if status!='accepted':return super().list(limit,offset,status)
        limit,offset=pagination(limit,offset)
        with self.db.transaction() as tx:
            rows=tx.all('SELECT payload FROM orders WHERE status=? ORDER BY id LIMIT ? OFFSET ?',(status,limit,offset))
            total=tx.one('SELECT COUNT(*) AS n FROM orders WHERE status=?',(status,))['n']
        return {'items':[json.loads(row['payload']) for row in rows],'total':total}

    def _event(self,tx,kind,order):
        if kind=='order.created':
            order.update(payment_id=None,payment_status='pending',courier_id=None,refund_id=None,
                         refund_status=None,restaurant_status='pending',restaurant_sent=False)
            self._save(tx,order)
        event=super()._event(tx,kind,order)
        extra={}
        if kind=='order.cancelled':
            extra=dict(cause=order.get('cancel_cause','customer_paid' if order.get('payment_status')=='succeeded' else 'customer_unpaid'),reason=order['cancel_reason'])
        if kind=='order.receipt_confirmed':extra['receipt_confirmed']=True
        event.update(producer='orders',correlation_id=order['id'],data=order_data(order,**extra))
        validate_event(event)
        tx.execute('UPDATE outbox SET payload=? WHERE event_id=?',(json.dumps(event,ensure_ascii=False),event['event_id']))
        return event

    def cancel(self,order_id,reason):
        order_id=uuid_text(order_id)
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=300:
            raise DomainError(422,'invalid_reason','Нужна причина отмены длиной 1-300')
        with self.db.transaction() as tx:
            tx.lock_key(order_id);order=self._get(tx,order_id)
            if order['status']=='cancelled':return order
            if order['status'] not in {'awaiting_payment','paid'} or order.get('restaurant_status')=='accepted':
                raise DomainError(409,'cannot_cancel','Ресторан уже принял заказ или доставка завершена')
            order.update(status='cancelled',cancel_reason=reason.strip())
            self._save(tx,order);self._event(tx,'order.cancelled',order)
        return order

    def handle(self,event):
        if isinstance(event,dict) and 'producer' not in event and event.get('type') in {'payment.succeeded','delivery.confirmed'}:
            return super().handle(event)
        validate_event(event)
        kind,data=event['type'],event['data']
        if kind not in SUBSCRIPTIONS[self.name]:return False
        order_id=data['order_id']
        with self.db.transaction() as tx:
            tx.lock_key(order_id)
            if tx.one('SELECT event_id FROM processed_events WHERE event_id=?',(event['event_id'],)):return False
            order=self._get(tx,order_id)
            if kind.startswith('payment.'):
                order.update(payment_id=data['payment_id'],payment_status={'payment.created':'pending','payment.failed':'failed','payment.succeeded':'succeeded'}[kind])
                if kind=='payment.succeeded' and order['status']!='cancelled' and not order.get('restaurant_sent'):
                    if order['status']!='awaiting_payment':raise DomainError(409,'invalid_transition','Нарушен порядок оплаты')
                    order.update(status='paid',restaurant_sent=True)
                    self._event(tx,'order.sent_to_restaurant',order)
            elif kind=='restaurant.order_accepted':
                if order['status']!='paid':raise DomainError(409,'invalid_transition','Заказ не оплачен')
                order.update(status='accepted',restaurant_status='accepted')
            elif kind=='restaurant.order_rejected':
                if order['status']!='cancelled':
                    order.update(status='cancelled',restaurant_status='rejected',cancel_reason=data['reason'],cancel_cause='restaurant_rejected')
                    self._event(tx,'order.cancelled',order)
            elif kind=='courier.assigned':order['courier_id']=data['courier_id']
            elif kind=='delivery.confirmed':
                if order['status']!='accepted':raise DomainError(409,'invalid_transition','Нарушен порядок доставки')
                order['status']='delivered'
            elif kind.startswith('refund.'):
                order.update(refund_id=data['refund_id'],refund_status=kind.split('.')[1].replace('requested','pending'))
            self._save(tx,order)
            tx.execute('INSERT INTO processed_events VALUES(?)',(event['event_id'],))
        return True


class NotificationService(AcceptedNotificationService):
    name='notifications'
    ROUTES=[('GET','/notifications'),('GET','/notifications/{notification_id}'),('POST','/notifications/{notification_id}/read')]
    def __init__(self,db):
        super().__init__(db);self.TEXTS=dict(self.TEXTS)
        self.store=LegacyOutbox(db);self.bus_status={'publisher':'not_required','consumer':False}
        with db.transaction() as tx:
            tx.execute('CREATE TABLE IF NOT EXISTS contacts (customer_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def handle(self,event):
        if isinstance(event,dict) and 'producer' not in event and event.get('type') in AcceptedNotificationService.TEXTS:
            return super().handle(event)
        validate_event(event);kind,data=event['type'],event['data']
        if kind not in SUBSCRIPTIONS[self.name]:return False
        if kind in {'customer.registered','customer.updated','address.saved'}:
            with self.db.transaction() as tx:
                tx.lock_key(event['event_id'])
                if tx.one('SELECT event_id FROM processed_events WHERE event_id=?',(event['event_id'],)):return False
                old=tx.one('SELECT payload FROM contacts WHERE customer_id=?',(data['customer_id'],))
                contact=json.loads(old['payload']) if old else {};contact.update(data)
                tx.execute('INSERT INTO contacts VALUES(?,?) ON CONFLICT(customer_id) DO UPDATE SET payload=EXCLUDED.payload',
                           (data['customer_id'],json.dumps(contact,ensure_ascii=False)))
                tx.execute('INSERT INTO processed_events VALUES(?)',(event['event_id'],))
            return True
        descriptions={'order.created':'Заказ оформлен','order.cancelled':'Заказ отменён','order.receipt_confirmed':'Получение заказа подтверждено',
            'payment.created':'Платёж создан','payment.attempt_created':'Создан повторный платёж',
            'order.sent_to_restaurant':'Заказ передан ресторану','restaurant.order_accepted':'Ресторан принял заказ',
            'restaurant.order_rejected':'Ресторан отклонил заказ','kitchen.started':'Приготовление начато','kitchen.ready':'Заказ приготовлен',
            'delivery.created':'Доставка создана','courier.assigned':'Курьер назначен','delivery.picked_up':'Курьер забрал заказ',
            'delivery.confirmed':'Заказ доставлен','refund.requested':'Возврат запрошен','refund.succeeded':'Возврат подтверждён',
            'refund.failed':'Возврат не выполнен','review.created':'Отзыв сохранён'}
        if kind.startswith('payment.') and kind in {'payment.failed','payment.succeeded'}:
            label=('Повторная оплата' if data['attempt_number']==2 else 'Оплата')+(' подтверждена' if kind.endswith('succeeded') else ' отклонена')
        else:label=descriptions[kind]
        self.TEXTS[kind]=label
        compatible=dict(event,data=dict(data,id=data['order_id']))
        return super().handle(compatible)
