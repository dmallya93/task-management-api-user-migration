"""GET /health (rest/server/handlers.go healthCheck)."""

from __future__ import annotations

import re

from tests.contract.helpers import (
    JSON_CT, RFC3339_SECONDS_RE, TS_PH, Client, assert_cors, assert_now, normalize,
)


def test_health_status_and_content_type(client: Client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == JSON_CT
    assert_cors(resp)


def test_health_raw_body_sorted_keys(client: Client) -> None:
    resp = client.get("/health")
    body = normalize(resp.content, ts_regex=RFC3339_SECONDS_RE)
    assert body == (
        '{"service":"task-api-go","status":"healthy","timestamp":"' + TS_PH + '","version":"1.0.0"}'
    )


def test_health_timestamp_is_rfc3339_seconds(client: Client) -> None:
    timestamp = client.get("/health").json()["timestamp"]
    assert RFC3339_SECONDS_RE.match(timestamp), timestamp
    assert not re.search(r"\.\d", timestamp)
    assert_now(timestamp)
