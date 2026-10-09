"""GET /health (``healthCheck`` in handlers.go)."""

from __future__ import annotations

from datetime import datetime, timezone

from flask.testing import FlaskClient

from tests.api.helpers import RFC3339_RE, FakeClock, assert_json, assert_plain_404, assert_redirect


def test_health_exact_body_with_sorted_gin_h_keys(client: FlaskClient, clock: FakeClock) -> None:
    resp = client.get("/health")
    assert_json(resp, 200)
    # gin.H is a map: keys are marshalled in sorted order; timestamp is RFC3339 (seconds).
    n = clock.calls - 1
    stamp = clock.stamp(n).split(".")[0] + "Z"
    assert resp.data == (
        b'{"service":"task-api-go","status":"healthy","timestamp":"'
        + stamp.encode() + b'","version":"1.0.0"}'
    )
    assert stamp == f"2026-01-02T03:04:{5 + n:02d}Z"


def test_health_timestamp_is_now_in_whole_seconds(client: FlaskClient) -> None:
    body = assert_json(client.get("/health"), 200)
    assert list(body) == ["service", "status", "timestamp", "version"]
    assert RFC3339_RE.match(body["timestamp"])
    ts = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    assert abs((ts - datetime.now(timezone.utc)).total_seconds()) < 60


def test_health_ignores_query_and_body(client: FlaskClient) -> None:
    body = assert_json(client.get("/health?x=1", data=b"garbage"), 200)
    assert body["status"] == "healthy"


def test_health_wrong_methods_are_plain_404(client: FlaskClient) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert_plain_404(client.open("/health", method=method))


def test_health_head_is_not_routed(client: FlaskClient) -> None:
    assert_plain_404(client.head("/health"), head=True)


def test_health_trailing_slash(client: FlaskClient) -> None:
    assert_redirect(client.get("/health/"), 301, "/health")
    assert_plain_404(client.post("/health/"))
