"""Run the PR8 test suites and fail the pipeline on skipped cases."""
import json
import subprocess
import sys
from pathlib import Path

subprocess.run([sys.executable, 'run_tests.py'], check=True)
summary = json.loads(Path('evidence/test-summary.json').read_text())
expected = {'unit': 10, 'integration': 21}
if set(summary) != set(expected) or any(
    summary[suite] != dict(run=count, failures=0, errors=0, skipped=0)
    for suite, count in expected.items()
):
    raise SystemExit(f'Unexpected results: {summary}')
print('CI: unit 10/10; integration 21/21; skipped 0')
