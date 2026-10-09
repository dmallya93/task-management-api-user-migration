"""Shared constants and assertions for the API tests (Flask test client responses)."""

from __future__ import annotations

import json
import re
from typing import Any

from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from task_api.go_compat import GoTime

TASKS = "/api/v1/tasks"

JSON_CT = "application/json; charset=utf-8"
PLAIN_404_CT = "text/plain"
PLAIN_404_BODY = b"404 page not found"
REDIRECT_CT = "text/html; charset=utf-8"

CORS_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Origin, Content-Type, Accept, Authorization",
}

SEED_TITLES = ("Implement Go REST API", "Add gRPC support")

UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Go time.RFC3339Nano: up to 9 fractional digits with trailing zeros trimmed.
RFC3339NANO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{0,8}[1-9])?(?:Z|[+-]\d{2}:\d{2})$"
)
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")

# 2026-01-02T03:04:05.123456789Z
T0_UNIX = 1767323045
T0_NSEC = 123456789


class FakeClock:
    """Each :meth:`now` returns ``T0 + n seconds`` (n = 0, 1, 2, ...), in UTC."""

    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> GoTime:
        t = GoTime(T0_UNIX + self.calls, T0_NSEC, 0)
        self.calls += 1
        return t

    @staticmethod
    def stamp(n: int) -> str:
        """RFC3339Nano text of the ``n``-th ``now()`` value."""
        return GoTime(T0_UNIX + n, T0_NSEC, 0).format_rfc3339nano()


def go_str(value: str) -> str:
    """A JSON string literal as Go's encoding/json writes it (HTML-safe, raw UTF-8)."""
    out = json.dumps(value, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    (" ", "\\u2028"), (" ", "\\u2029")):
        out = out.replace(ch, esc)
    return out


def task_json(
    *,
    id: str,  # noqa: A002
    title: str,
    created_at: str,
    updated_at: str | None = None,
    description: str = "",
    status: str = "pending",
    priority: str = "medium",
    tags: list[str] | None = None,
    assigned_to: str | None = None,
    due_date: str | None = None,
    completed_at: str | None = None,
) -> str:
    """Raw JSON of a ``models.Task`` in Go struct field order (omitempty pointers dropped)."""
    parts = [
        f'"id":{go_str(id)}',
        f'"title":{go_str(title)}',
        f'"description":{go_str(description)}',
        f'"status":{go_str(status)}',
        f'"priority":{go_str(priority)}',
        '"tags":[' + ",".join(go_str(t) for t in (tags or [])) + "]",
        '"created_by":"system"',
    ]
    if assigned_to is not None:
        parts.append(f'"assigned_to":{go_str(assigned_to)}')
    parts.append(f'"created_at":{go_str(created_at)}')
    parts.append(f'"updated_at":{go_str(updated_at or created_at)}')
    if due_date is not None:
        parts.append(f'"due_date":{go_str(due_date)}')
    if completed_at is not None:
        parts.append(f'"completed_at":{go_str(completed_at)}')
    return "{" + ",".join(parts) + "}"


def error_json(code: str, message: str) -> bytes:
    """Raw JSON of ``models.ErrorResponse`` (``details`` omitted)."""
    return ('{"error":{"code":' + go_str(code) + ',"message":' + go_str(message) + "}}").encode()


def assert_cors(resp: TestResponse) -> None:
    for name, value in CORS_HEADERS.items():
        assert resp.headers.get(name) == value, f"{name} missing on {resp.status}: {resp.headers}"


def assert_no_cors(resp: TestResponse) -> None:
    for name in CORS_HEADERS:
        assert name not in resp.headers, f"unexpected {name} on {resp.status}"


def assert_json(resp: TestResponse, status: int) -> Any:
    """Status, exact JSON Content-Type, Content-Length and CORS; returns the decoded body."""
    assert resp.status_code == status, (resp.status_code, resp.data)
    assert resp.headers.get("Content-Type") == JSON_CT
    assert resp.headers.get("Content-Length") == str(len(resp.data))
    assert_cors(resp)
    return json.loads(resp.data)


def assert_error(resp: TestResponse, status: int, code: str, message: str) -> None:
    """An ``ErrorResponse`` with exactly this code and message (byte-exact body)."""
    assert_json(resp, status)
    assert resp.data == error_json(code, message), resp.data


def assert_validation(resp: TestResponse, message: str) -> None:
    assert_error(resp, 400, "VALIDATION_ERROR", message)


def assert_plain_404(resp: TestResponse, *, head: bool = False) -> None:
    """Gin's router 404: ``text/plain`` without charset, 18-byte body, CORS present."""
    assert resp.status_code == 404, (resp.status_code, resp.data)
    assert resp.status == "404 Not Found"
    assert resp.headers.get("Content-Type") == PLAIN_404_CT
    assert resp.headers.get("Content-Length") == "18"
    assert resp.data == (b"" if head else PLAIN_404_BODY)
    assert_cors(resp)


def assert_empty(resp: TestResponse, status: int) -> None:
    """``c.Status``/``AbortWithStatus``: empty body, no Content-Type, CORS present."""
    assert resp.status_code == status, (resp.status_code, resp.data)
    assert resp.data == b""
    assert "Content-Type" not in resp.headers
    assert resp.headers.get("Content-Length", "0") == "0"
    assert_cors(resp)


def assert_redirect(resp: TestResponse, status: int, location: str, body: bytes | None = None) -> None:
    """Router-level trailing-slash redirect: no CORS; 301 has an HTML body, 307 none."""
    assert resp.status_code == status, (resp.status_code, resp.data)
    assert resp.headers.get("Location") == location
    assert_no_cors(resp)
    if status == 301:
        assert resp.status == "301 Moved Permanently"
        assert resp.headers.get("Content-Type") == REDIRECT_CT
        href = location.replace("&", "&amp;").replace("'", "&#39;").replace("<", "&lt;")
        expected = body if body is not None else f'<a href="{href}">Moved Permanently</a>.\n\n'.encode()
        assert resp.data == expected
    else:
        assert resp.status == "307 Temporary Redirect"
        assert "Content-Type" not in resp.headers
        assert resp.data == b""
    assert resp.headers.get("Content-Length") == str(len(resp.data))


def create(client: FlaskClient, body: dict[str, Any] | str | bytes) -> dict[str, Any]:
    """POST a task (expects 201) and return the decoded task."""
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    resp = client.post(TASKS, data=data)
    task = assert_json(resp, 201)
    assert resp.headers["Location"] == f"{TASKS}/{task['id']}"
    return task


def get(client: FlaskClient, task_id: str) -> dict[str, Any]:
    return assert_json(client.get(f"{TASKS}/{task_id}"), 200)


def list_json(client: FlaskClient, query: str = "") -> dict[str, Any]:
    """GET /api/v1/tasks with a raw query string (expects 200)."""
    path = TASKS + (f"?{query}" if query else "")
    return assert_json(client.get(path), 200)


def all_titles(client: FlaskClient, query: str = "") -> list[str]:
    body = list_json(client, "page_size=100" + (f"&{query}" if query else ""))
    return [t["title"] for t in body["tasks"] or []]


def seed_ids(client: FlaskClient) -> dict[str, str]:
    """Title -> id of the two seed tasks."""
    body = list_json(client, "sort_order=created_at_asc")
    return {t["title"]: t["id"] for t in body["tasks"] if t["title"] in SEED_TITLES}


def open_raw(client: FlaskClient, method: str, target: str, **kwargs: Any) -> TestResponse:
    """Send ``target`` as the literal request-line target (as ``app.py``'s server does).

    Needed where Werkzeug's test client would rebuild the target differently, e.g. a bare
    trailing ``?``, which it drops.
    """
    path, _, query = target.partition("?")
    overrides = {"RAW_URI": target, "REQUEST_URI": target}
    return client.open(path, method=method, query_string=query or None,
                       environ_overrides=overrides, **kwargs)
