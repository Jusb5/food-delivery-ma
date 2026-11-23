"""Run the PR8 test suites and fail the pipeline on skipped cases."""
import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

import pika


def wait_for_broker():
    """Wait for an AMQP handshake from the test container."""
    params = pika.URLParameters(os.environ['TEST_AMQP_URL'])
    params.socket_timeout = 3
    params.stack_timeout = 5
    deadline = monotonic() + 60

    while True:
        try:
            connection = pika.BlockingConnection(params)
            connection.close()
            return
        except pika.exceptions.AMQPConnectionError as error:
            if monotonic() >= deadline:
                raise SystemExit('RabbitMQ did not accept an AMQP connection within 60 seconds') from error
            sleep(2)


wait_for_broker()

subprocess.run([sys.executable, 'run_tests.py'], check=True)
summary = json.loads(Path('evidence/test-summary.json').read_text())
expected = {'unit': 10, 'integration': 21}
if set(summary) != set(expected) or any(
    summary[suite] != dict(run=count, failures=0, errors=0, skipped=0)
    for suite, count in expected.items()
):
    raise SystemExit(f'Unexpected results: {summary}')
print('CI: unit 10/10; integration 21/21; skipped 0')
