import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from base64 import b64encode
from extension.core.peer import PORTS

ROOT=Path(__file__).resolve().parents[1]
LOG=ROOT/'evidence/extended/http-calls.jsonl'
opener=build_opener(ProxyHandler({}))

def management(path):
    req=Request('http://127.0.0.1:15672/api/'+path)
    req.add_header('Authorization','Basic '+b64encode(b'student:student').decode())
    return json.load(opener.open(req,timeout=10))


def request(service,path,method='GET',body=None,headers=None,record=True):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    req=Request(f'http://127.0.0.1:{PORTS[service]}/api/v1/'+path,data=data,
                headers={'Content-Type':'application/json',**(headers or {})},method=method)
    try:
        response=opener.open(req,timeout=10)
    except HTTPError as error:
        response=error
    with response:
        result=json.load(response);status=response.status
    if record:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':time.time(),'service':service,'method':method,'path':'/api/v1/'+path,
                'request':body,'headers':headers or {},'status':status,'response':result},ensure_ascii=False)+'\n')
    return status,result


def wait_for(action,predicate,timeout=25):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result=action()
            if predicate(result):return result
        except (URLError,TimeoutError):pass
        time.sleep(.2)
    raise AssertionError('Timed out waiting for asynchronous result')


def provider_event(body):
    raw=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    signature=hmac.new(os.getenv('PROVIDER_SECRET','local-study-secret').encode(),raw,hashlib.sha256).hexdigest()
    return request('payments','provider-events','POST',body,{'X-Provider-Signature':signature})
