import importlib,os,sys,signal,threading
from common.db import Database
from common.http import make_server as legacy_server
from extension.core.store import Store
from extension.core.registry import CLASSES
from extension.core.http import make_server
from extension.core.broker import Workers
from extension.core.domain import utcnow
from extension.core.telemetry import setup_tracing

name=sys.argv[1]
role=sys.argv[2] if len(sys.argv)>2 else 'http'
port=int(os.getenv('PORT','8000'))
if role == 'http':
    setup_tracing(name)
if name in {'orders','notifications'}:
    from extension import legacy
    service=getattr(legacy,CLASSES[name])(Database(os.environ['DATABASE_URL']))
    if role in {'publisher','consumer'}:
        workers=Workers(service)
        getattr(workers,role)()
        raise SystemExit
    server=legacy_server(service,CLASSES[name],'0.0.0.0',port)
    workers=None
else:
    module=importlib.import_module('extension.services.'+name)
    service=getattr(module,CLASSES[name])(Store(os.environ['DATABASE_URL'],name))
    if name=='profiles':
        from orders.service import CUSTOMER_ID
        with service.store.transaction() as tx:
            if tx.get('customers',CUSTOMER_ID,False) is None:
                customer=dict(id=CUSTOMER_ID,name='Учебный клиент',phone='+79990000000',created_at=utcnow())
                tx.put('customers',CUSTOMER_ID,customer);tx.put('phones',customer['phone'],{'customer_id':CUSTOMER_ID})
                tx.emit('customer.registered',dict(customer_id=CUSTOMER_ID,name=customer['name'],phone=customer['phone']))
    workers=Workers(service);workers.start()
    server=make_server(service,'0.0.0.0',port)
def stop(*_):
    if workers:workers.stop.set()
    threading.Thread(target=server.shutdown,daemon=True).start()
signal.signal(signal.SIGTERM,stop)
print(CLASSES[name],role,'ready',flush=True)
try:server.serve_forever()
finally:server.server_close()
