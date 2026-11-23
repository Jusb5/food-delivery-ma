"""Runs real HTTP/RabbitMQ flows. Only the external bank callback is simulated."""
from pathlib import Path
from uuid import uuid4
import json
import time
from urllib.request import urlopen
from extension.core.peer import PORTS
from extension.core.events import EVENTS
from extension.core.broker import connect, topology, AUDIT_QUEUE
from extension.services.catalog import RESTAURANT_ID,DISH_ID
from labs.extended_http import request,wait_for,provider_event,ROOT


def must(service,path,method='GET',body=None,headers=None,status=200):
    code,result=request(service,path,method,body,headers)
    assert code==status,(service,path,code,result)
    print(f'{service}: {method} /api/v1/{path} -> {code}',flush=True)
    return result


def order_body(customer):
    return {'customer_id':customer,'restaurant_id':RESTAURANT_ID,
            'address':'Москва, проспект Вернадского, 78','items':[{'dish_id':DISH_ID,'quantity':2}]}


def new_order(customer):
    key=uuid4().hex;body=order_body(customer)
    order=must('orders','orders','POST',body,{'Idempotency-Key':key},201)
    assert order['total']==120000
    replay=must('orders','orders','POST',body,{'Idempotency-Key':key},200)
    assert order['id']==replay['id']
    _,conflict=request('orders','orders','POST',dict(body,address='Другой адрес'),{'Idempotency-Key':key})
    assert conflict['code']=='idempotency_conflict'
    return order


def payment_for(order):
    order=wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['payment_id'] is not None)
    return must('payments','payments/'+order['payment_id'])


def pay(order,retry=False):
    payment=payment_for(order)
    if retry:
        body={'provider_event_id':uuid4().hex,'payment_id':payment['payment_id'],
              'attempt_id':payment['attempt_id'],'status':'failed'}
        assert provider_event(body)[0]==202
        assert provider_event(body)[1]['duplicate']
        wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['payment_status']=='failed')
        attempt=must('payments','payments/'+payment['payment_id']+'/attempts','POST',
                     {'return_url':'https://client.test/return'},status=202)
        payment['attempt_id']=attempt['attempt_id']
    body={'provider_event_id':uuid4().hex,'payment_id':payment['payment_id'],
          'attempt_id':payment['attempt_id'],'status':'succeeded'}
    assert provider_event(body)[0]==202
    return wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['status']=='paid')


def decide(order,accepted):
    body={'accepted':accepted}
    if not accepted:body['reason']='Нет ингредиентов'
    code,result=wait_for(lambda:request('catalog','restaurant-orders/'+order['id']+'/decision','POST',body),
                         lambda r:r[0]==200)
    return result


def refund(order,cause,failed=False):
    if cause=='restaurant_rejected':decide(order,False)
    else:must('orders','orders/'+order['id']+'/cancel','POST',{'reason':'Изменились планы'},status=202)
    current=wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['refund_id'] is not None)
    item=must('payments','refunds/'+current['refund_id'])
    target='failed' if failed else 'succeeded'
    assert provider_event({'provider_event_id':uuid4().hex,'refund_id':item['refund_id'],'status':target})[0]==202
    final=wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['refund_status']==target)
    assert final['status']=='cancelled'
    print('REFUND',cause,target,flush=True)
    return final


def main():
    (ROOT/'evidence').mkdir(exist_ok=True)
    health={}
    for name,port in PORTS.items():
        def probe():
            with urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response:return json.load(response)
        health[name]=wait_for(probe,lambda r:r['status']=='ok',timeout=60)
    (ROOT/'evidence/extended/health.json').write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding='utf-8')
    phone='+7999'+str(time.time_ns())[-7:]
    customer=must('profiles','customers','POST',{'name':'Учебный клиент','phone':phone},status=201)
    must('profiles','customers/'+customer['id'])
    must('profiles','customers/'+customer['id'],'PATCH',{'name':'Иван Петров'})
    must('profiles','customers/'+customer['id']+'/addresses','POST',
         {'address':'Москва, проспект Вернадского, 78','comment':'Домофон 12'},status=201)
    must('profiles','customers/'+customer['id']+'/addresses')
    must('catalog','restaurants');must('catalog','restaurants/'+RESTAURANT_ID+'/menu')
    dish=must('catalog','restaurants/'+RESTAURANT_ID+'/dishes','POST',{'name':'Суп','price':30000,'available':True},status=201)
    must('catalog','dishes/'+dish['dish_id'],'PATCH',{'price':32000})
    order=new_order(customer['id']);must('orders','orders');must('orders','orders/'+order['id'])
    _,early=request('orders','orders/'+order['id']+'/receipt','POST',{})
    assert early['code']=='not_delivered'
    order=pay(order,retry=True);decide(order,True)
    wait_for(lambda:request('kitchen','kitchen-orders/'+order['id']),lambda r:r[0]==200)
    must('kitchen','kitchen-orders');must('kitchen','kitchen-orders/'+order['id'])
    _,bad=request('kitchen','kitchen-orders/'+order['id']+'/ready','POST',{})
    assert bad['code']=='invalid_kitchen_transition'
    must('kitchen','kitchen-orders/'+order['id']+'/start','POST',{})
    delivery=wait_for(lambda:request('delivery','deliveries?order_id='+order['id'])[1],lambda p:p['total']==1)['items'][0]
    must('delivery','deliveries');must('delivery','deliveries/'+delivery['delivery_id'])
    courier=str(uuid4());must('delivery','deliveries/'+delivery['delivery_id']+'/assign','POST',{'courier_id':courier})
    assert request('delivery','deliveries/'+delivery['delivery_id']+'/pickup','POST',{})[0]==409
    must('kitchen','kitchen-orders/'+order['id']+'/ready','POST',{})
    must('delivery','deliveries/'+delivery['delivery_id']+'/pickup','POST',{})
    must('delivery','deliveries/'+delivery['delivery_id']+'/complete','POST',{})
    wait_for(lambda:request('orders','orders/'+order['id'])[1],lambda o:o['status']=='delivered')
    complete=must('orders','orders/'+order['id']+'/receipt','POST',{},status=202)
    assert complete['status']=='completed' and complete['receipt_confirmed']
    review_body={'order_id':order['id'],'target_type':'restaurant','target_id':RESTAURANT_ID,'score':5,'text':'Всё доставлено вовремя'}
    _,review=wait_for(lambda:request('reviews','reviews','POST',review_body),lambda r:r[0]==201)
    must('reviews','reviews');rating=must('reviews','ratings/restaurant/'+RESTAURANT_ID)
    assert rating['review_count']>=1
    must('catalog','restaurants')
    must('reviews','reviews/'+review['review_id']+'/hide','POST',{'reason':'Учебная проверка модерации'})
    assert request('reviews','reviews','POST',review_body)[0]==409
    unpaid=new_order(customer['id'])
    must('orders','orders/'+unpaid['id']+'/cancel','POST',{'reason':'Передумал до оплаты'},status=202)
    paid_cancel=refund(pay(new_order(customer['id'])),'customer_paid')
    rejected=refund(pay(new_order(customer['id'])),'restaurant_rejected')
    failed_refund=refund(pay(new_order(customer['id'])),'customer_paid',failed=True)
    page=wait_for(lambda:request('notifications','notifications?customer_id='+customer['id']+'&limit=100')[1],lambda r:r['total']>=10)
    notification=page['items'][0]
    must('notifications','notifications/'+notification['id'])
    must('notifications','notifications/'+notification['id']+'/read','POST',{})
    context={'customer_id':customer['id'],'restaurant_id':RESTAURANT_ID,'dish_id':DISH_ID,'order_id':order['id'],
        'delivery_id':delivery['delivery_id'],'courier_id':courier,'payment_id':order['payment_id'],
        'review_id':review['review_id'],'notification_id':notification['id'],
        'paid_cancel_order_id':paid_cancel['id'],'rejected_order_id':rejected['id'],'unpaid_order_id':unpaid['id']}
    (ROOT/'evidence/extended/context.json').write_text(json.dumps(context,indent=2),encoding='utf-8')
    time.sleep(1)
    events=[]
    with connect() as connection:
        channel=connection.channel();topology(channel)
        while True:
            method,_,body=channel.basic_get(AUDIT_QUEUE,auto_ack=False)
            if method is None:break
            events.append(json.loads(body));channel.basic_ack(method.delivery_tag)
    with (ROOT/'evidence/extended/messages.jsonl').open('a',encoding='utf-8') as f:
        for item in events:f.write(json.dumps(item,ensure_ascii=False)+'\n')
    observed={item['type'] for item in events}
    missing=set(EVENTS)-observed
    assert not missing,missing
    summary={'services':len(health),'business_endpoints':35,
        'message_types_observed':len(observed),'message_types_defined':len(EVENTS),
        'status':'PASS','payment_provider':'local simulated callback; real PaymentService and RabbitMQ'}
    (ROOT/'evidence/extended/scenario-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
