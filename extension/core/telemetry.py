"""HTTP metrics and trace propagation for the two HTTP handler families."""
import os
import re
from contextlib import contextmanager
from functools import wraps
from time import perf_counter
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST, generate_latest


registry = CollectorRegistry()
requests = Counter(
    'food_http_requests_total', 'Completed HTTP business requests',
    ('method', 'route', 'status'), registry=registry,
)
duration = Histogram(
    'food_http_request_duration_seconds', 'HTTP business request duration',
    ('method', 'route'), registry=registry,
)
tracer = trace.get_tracer(__name__)
propagator = TraceContextTextMapPropagator()


def setup_tracing(service_name):
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT')
    if endpoint:
        provider = TracerProvider(resource=Resource.create({'service.name': service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=3)))
        trace.set_tracer_provider(provider)


def observe_http(routes):
    patterns = [
        (verb, '/api/v1' + path, re.compile(re.sub(r'\{\w+\}', '[^/]+', '/api/v1' + path)))
        for verb, path, *_ in routes
    ]

    def decorate(dispatch):
        @wraps(dispatch)
        def measured(handler, method):
            path = urlsplit(handler.path).path
            if method == 'GET' and path == '/metrics':
                raw = generate_latest(registry)
                handler.send_response(200)
                handler.send_header('Content-Type', CONTENT_TYPE_LATEST)
                handler.send_header('Content-Length', str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)
                return
            if path == '/health':
                return dispatch(handler, method)
            route = next(
                (template for verb, template, pattern in patterns if method == verb and pattern.fullmatch(path)),
                'unmatched',
            )
            carrier = {key.lower(): value for key, value in handler.headers.items()}
            started = perf_counter()
            with tracer.start_as_current_span(
                f'{method} {route}', context=propagator.extract(carrier), kind=SpanKind.SERVER,
                attributes={'http.request.method': method, 'http.route': route},
            ) as span:
                status = dispatch(handler, method)
                span.set_attribute('http.response.status_code', status)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                requests.labels(method, route, str(status)).inc()
                duration.labels(method, route).observe(perf_counter() - started)
                return status
        return measured
    return decorate


@contextmanager
def trace_peer(service, headers):
    with tracer.start_as_current_span(
        f'GET {service}', kind=SpanKind.CLIENT,
        attributes={'http.request.method': 'GET', 'server.address': service},
    ) as span:
        propagator.inject(headers)
        yield span
