"""Compatibility test: legacy /api/health callers continue to receive
200 + a 'status' key. Detailed contract tests live in
tests/api/test_health.py.
"""
def test_health_returns_200_with_status(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded", "down")
