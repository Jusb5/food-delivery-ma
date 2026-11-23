"""Validate documented examples; no HTTP request is sent by operation()."""
import json
import re

def operation(method, path, request, response):
    status, body = response
    assert method in {"GET", "POST", "PATCH", "DELETE"}
    assert path.startswith("/api/v1/")
    assert 200 <= status < 300
    assert isinstance(body, dict)
    assert request is None or isinstance(request, dict)
    json.loads(json.dumps({"request": request, "response": body}, ensure_ascii=False))
    assert all(re.fullmatch(r"[a-z_]+", name) for name in re.findall(r"{([^}]+)}", path))
    print(f"CONTRACT {method} {path}: response {status}; JSON OK")
