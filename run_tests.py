"""Run the two requested suites locally; keep the actual console transcript."""
from pathlib import Path
import io
import json
import sys
import unittest

root = Path(__file__).resolve().parent
import os
os.environ.setdefault('TEST_POSTGRES_URL','postgresql://lab_test:lab_test@127.0.0.1:15433/food_tests')
os.environ.setdefault('TEST_AMQP_URL','amqp://student:student@127.0.0.1:5672/')
summary = {}
success = True
for level in ['unit', 'integration']:
    print('\n=== ' + level.upper() + ' ===', flush=True)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(root/'tests'/level), top_level_dir=str(root))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    print(output, flush=True)
    (root/'evidence').mkdir(exist_ok=True)
    (root/'evidence'/f'{level}-output.txt').write_text(output, encoding='utf-8')
    summary[level] = {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}
    success = result.wasSuccessful() and success
(root/'evidence'/'test-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
sys.exit(0 if success else 1)
