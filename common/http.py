import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
from common.domain import DomainError
from extension.core.telemetry import observe_http


def handler_for(service, name):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.dispatch('GET')

        def do_POST(self):
            self.dispatch('POST')

        def body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1048576:
                    raise ValueError
                return json.loads(self.rfile.read(length).decode('utf-8'))
            except (ValueError, UnicodeError):
                raise DomainError(400, 'invalid_json', 'Некорректное тело JSON')

        @observe_http(getattr(service, 'ROUTES', ()))
        def dispatch(self, method):
            try:
                path = urlsplit(self.path)
                parts = path.path.strip('/').split('/')
                query = {k: v[0] for k, v in parse_qs(path.query).items()}
                if method == 'GET' and path.path == '/health':
                    with service.db.transaction() as tx:
                        tx.one('SELECT 1 AS ok')
                    status, result = 200, {'service': name, 'status': 'ok'}
                elif parts[:2] != ['api', 'v1']:
                    raise DomainError(404, 'route_not_found', 'Маршрут не найден')
                else:
                    status, result = self.route(method, parts[2:], query)
            except DomainError as exc:
                status, result = exc.status, {'code': exc.code, 'message': exc.message, 'trace_id': str(uuid4())}
            except Exception:
                traceback.print_exc()
                status, result = 500, {'code': 'internal_error', 'message': 'Внутренняя ошибка', 'trace_id': str(uuid4())}
            raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return status

        def route(self, method, parts, query):
            resource = 'orders' if name == 'OrderService' else 'notifications'
            if not parts or parts[0] != resource:
                raise DomainError(404, 'route_not_found', 'Маршрут не найден')
            if method == 'GET' and len(parts) == 1:
                return 200, service.list(query.get('limit', 20), query.get('offset', 0), query.get('status'))
            if method == 'GET' and len(parts) == 2:
                return 200, service.get(parts[1])
            if name == 'OrderService' and method == 'POST':
                body = self.body()
                if len(parts) == 1:
                    order, created = service.create(body, self.headers.get('Idempotency-Key'))
                    return (201 if created else 200), order
                if len(parts) == 3 and parts[2] == 'cancel':
                    if not isinstance(body, dict):
                        raise DomainError(422, 'invalid_body', 'Нужен JSON-объект')
                    return 202, service.cancel(parts[1], body.get('reason'))
                if len(parts) == 3 and parts[2] == 'receipt':
                    return 202, service.receipt(parts[1])
            if name == 'NotificationService' and method == 'POST' and len(parts) == 3 and parts[2] == 'read':
                self.body()
                return 200, service.read(parts[1])
            raise DomainError(404, 'route_not_found', 'Маршрут не найден')

    return Handler


def make_server(service, name, host='127.0.0.1', port=0):
    return ThreadingHTTPServer((host, port), handler_for(service, name))
