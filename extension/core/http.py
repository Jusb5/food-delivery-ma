import json
import re
import traceback
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from extension.core.domain import DomainError, require
from extension.core.registry import CLASSES
from extension.core.telemetry import observe_http


def make_server(service, host='127.0.0.1', port=8000):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self.dispatch('GET')
        def do_POST(self): self.dispatch('POST')
        def do_PATCH(self): self.dispatch('PATCH')

        @observe_http(service.ROUTES)
        def dispatch(self, method):
            try:
                target = urlsplit(self.path)
                query = {k: v[0] for k,v in parse_qs(target.query).items()}
                if target.path == '/health' and method == 'GET':
                    with service.store.transaction() as tx:
                        database = tx.conn.execute('SELECT current_database() AS name').fetchone()['name']
                    ready = all(service.bus_status.values())
                    status, result = (200 if ready else 503), {'service':CLASSES[service.name],
                        'status':'ok' if ready else 'starting', 'database':database,
                        'business_endpoints':len(service.ROUTES), 'broker':dict(service.bus_status)}
                else:
                    status, result = self.route(method, target.path, query)
            except DomainError as error:
                status, result = error.status, {'code':error.code, 'message':error.message, 'trace_id':str(uuid4())}
            except Exception:
                traceback.print_exc()
                status, result = 500, {'code':'internal_error', 'message':'Внутренняя ошибка', 'trace_id':str(uuid4())}
            raw = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
            return status

        def route(self, method, path, query):
            for verb, template, function, _ in service.ROUTES:
                pattern = re.sub(r'{(\w+)}', r'(?P<\1>[^/]+)', '/api/v1'+template)
                match = re.fullmatch(pattern, path)
                if verb == method and match:
                    body = {}
                    if method in {'POST','PATCH'}:
                        try:
                            size = int(self.headers.get('Content-Length', '0'))
                            require(0 <= size <= 1048576, 'body_too_large', 'Тело до 1 МБ', 413)
                            body = json.loads(self.rfile.read(size).decode()) if size else {}
                        except (ValueError, UnicodeError):
                            raise DomainError(400, 'invalid_json', 'Некорректный JSON')
                        require(isinstance(body, dict), 'invalid_body', 'Тело должно быть объектом')
                    return getattr(service, function)(body=body, headers=self.headers, query=query, **match.groupdict())
            raise DomainError(404, 'route_not_found', 'Маршрут не найден')

    return ThreadingHTTPServer((host, port), Handler)
