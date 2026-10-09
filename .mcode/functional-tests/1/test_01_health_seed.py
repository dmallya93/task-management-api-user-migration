"""GET /health and seed data (must run first against a freshly started server)."""
import re

import requests

from conftest import BASE_URL, TASKS, TIMEOUT

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Origin, Content-Type, Accept, Authorization",
}


class TestHealth:
    def test_health_ok(self, observe):
        r = observe(requests.get(BASE_URL + "/health", timeout=TIMEOUT))
        assert r.status_code == 200
        assert r.headers["Content-Type"] == "application/json; charset=utf-8"
        for k, v in CORS.items():
            assert r.headers[k] == v
        # Go map -> sorted keys, RFC3339 seconds timestamp
        assert re.fullmatch(
            r'\{"service":"task-api-go","status":"healthy","timestamp":"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(Z|[+-]\d\d:\d\d)","version":"1\.0\.0"\}',
            r.text,
        ), r.text


class TestSeed:
    def test_seed_tasks_present(self, observe):
        r = observe(requests.get(TASKS, params={"sort_order": "created_at_asc"}, timeout=TIMEOUT))
        assert r.status_code == 200
        body = r.json()
        assert body["total_count"] == 2
        assert "next_page_token" not in body
        t1, t2 = body["tasks"]
        assert t1["title"] == "Implement Go REST API"
        assert t1["description"] == "Create REST API with Gin framework"
        assert t1["priority"] == "high"
        assert t1["tags"] == ["go", "rest", "api"]
        assert t2["title"] == "Add gRPC support"
        assert t2["description"] == "Implement gRPC server with native Go support"
        assert t2["priority"] == "medium"
        assert t2["tags"] == ["go", "grpc", "protobuf"]
        for t in (t1, t2):
            assert t["status"] == "pending"
            assert t["created_by"] == "system"
            assert list(t.keys()) == ["id", "title", "description", "status", "priority", "tags",
                                      "created_by", "created_at", "updated_at"]
            assert t["created_at"] == t["updated_at"]
