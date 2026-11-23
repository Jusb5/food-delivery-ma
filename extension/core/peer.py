import json
import os
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from extension.core.domain import DomainError
from extension.core.telemetry import trace_peer

PORTS = {'orders':8001,'notifications':8002,'profiles':8003,'catalog':8004,'kitchen':8005,'delivery':8006,'payments':8007,'reviews':8008}


class ServiceClient:
    def __init__(self, source):
        self.source = source
        self.opener = build_opener(ProxyHandler({}))

    def get(self, service, path):
        base = os.getenv(service.upper()+'_URL', f'http://127.0.0.1:{PORTS[service]}')
        headers = {'X-From-Service': self.source}
        with trace_peer(service, headers) as span:
            return self._get(service, path, Request(base+'/api/v1/'+path, headers=headers), span)

    def _get(self, service, path, request, span):
        try:
            with self.opener.open(request, timeout=5) as response:
                result = json.load(response)
                span.set_attribute('http.response.status_code', response.status)
                print(f'PEER {self.source} -> {service} GET /api/v1/{path} HTTP {response.status}', flush=True)
                return result
        except HTTPError as error:
            span.set_attribute('http.response.status_code', error.code)
            if error.code == 404:
                raise DomainError(422, 'peer_resource_not_found', f'Объект не найден в {service}')
            raise DomainError(503, 'peer_unavailable', f'{service}: HTTP {error.code}')
        except (URLError, TimeoutError, OSError, ValueError):
            raise DomainError(503, 'peer_unavailable', f'Сервис {service} недоступен')
