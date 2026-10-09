"""Shared helpers for the black-box contract suite.

The suite talks HTTP to whatever server ``BASE_URL`` points at (the Go
reference binary or ``python app.py``). Server-generated values (UUIDs and
"now" timestamps) differ between runs and implementations, so responses are
compared after normalizing them to placeholders -- but only once their format
has been asserted.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import requests

BASE_URL: str = os.environ.get("BASE_URL", "").rstrip("/")
TASKS = "/api/v1/tasks"

JSON_CT = "application/json; charset=utf-8"
PLAIN_404_CT = "text/plain"
PLAIN_404_BODY = b"404 page not found"
REDIRECT_301_CT = "text/html; charset=utf-8"

CORS_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Origin, Content-Type, Accept, Authorization",
}

UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_UUID_ANY_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Go time.RFC3339Nano output: up to 9 fractional digits, trailing zeros trimmed.
RFC3339NANO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{0,8}[1-9])?(?:Z|[+-]\d{2}:\d{2})$"
)
# Go time.RFC3339 output (health check): whole seconds only.
RFC3339_SECONDS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")
# Task fields holding server-generated "now" timestamps.
_NOW_TS_RE = re.compile(r'"(created_at|updated_at|completed_at|timestamp)":"([^"]*)"')

UUID_PH = "<UUID>"
TS_PH = "<TS>"


def url(path: str) -> str:
    return BASE_URL + path


def unique_tag(prefix: str = "ct") -> str:
    """A tag no other test (or seed task) uses; isolates list tests."""
    return f"{prefix}-{uuid.uuid4().hex}"


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339(Nano) string into an aware datetime (µs precision)."""
    m = re.match(r"^(.{19})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", value)
    assert m, f"not RFC3339: {value!r}"
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")


def assert_now(value: str, tolerance: timedelta = timedelta(minutes=2)) -> None:
    """Assert a server-generated timestamp is close to the current time."""
    delta = abs(parse_rfc3339(value) - datetime.now(timezone.utc))
    assert delta < tolerance, f"timestamp {value!r} is {delta} away from now"


def normalize(raw: bytes | str, *, ts_regex: re.Pattern[str] = RFC3339NANO_RE,
              check_now: bool = True) -> str:
    """Replace UUIDs and "now" timestamps by placeholders after checking formats.

    * every UUID-shaped substring must be a lowercase v4 UUID -> ``<UUID>``
    * values of created_at/updated_at/completed_at/timestamp must match
      ``ts_regex`` and (unless ``check_now`` is false) be close to now -> ``<TS>``
    Everything else (key order, escaping, due_date) is left byte-identical.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

    def _ts(m: re.Match[str]) -> str:
        value = m.group(2)
        assert ts_regex.match(value), f"{m.group(1)}={value!r} has wrong format"
        if check_now:
            assert_now(value)
        return f'"{m.group(1)}":"{TS_PH}"'

    def _uuid(m: re.Match[str]) -> str:
        assert UUID4_RE.match(m.group(0)), f"{m.group(0)!r} is not a lowercase v4 UUID"
        return UUID_PH

    text = _NOW_TS_RE.sub(_ts, text)
    return _UUID_ANY_RE.sub(_uuid, text)


def go_str(value: str) -> str:
    """Encode a string the way Go's encoding/json does (HTML-safe, UTF-8)."""
    out = json.dumps(value, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    (" ", "\\u2028"), (" ", "\\u2029")):
        out = out.replace(ch, esc)
    return out


def task_body(
    *,
    title: str,
    description: str = "",
    status: str = "pending",
    priority: str = "medium",
    tags: list[str] | None = None,
    assigned_to: str | None = None,
    due_date: str | None = None,
    completed: bool = False,
) -> str:
    """Expected normalized raw JSON for a Task, in Go struct field order."""
    parts = [
        f'"id":"{UUID_PH}"',
        f'"title":{go_str(title)}',
        f'"description":{go_str(description)}',
        f'"status":{go_str(status)}',
        f'"priority":{go_str(priority)}',
        '"tags":[' + ",".join(go_str(t) for t in (tags or [])) + "]",
        '"created_by":"system"',
    ]
    if assigned_to is not None:
        parts.append(f'"assigned_to":{go_str(assigned_to)}')
    parts += [f'"created_at":"{TS_PH}"', f'"updated_at":"{TS_PH}"']
    if due_date is not None:
        parts.append(f'"due_date":{go_str(due_date)}')
    if completed:
        parts.append(f'"completed_at":"{TS_PH}"')
    return "{" + ",".join(parts) + "}"


def error_body(code: str, message: str) -> str:
    """Expected raw JSON of an ErrorResponse (details omitted)."""
    return '{"error":{"code":' + go_str(code) + ',"message":' + go_str(message) + "}}"


def assert_cors(resp: requests.Response) -> None:
    for name, value in CORS_HEADERS.items():
        assert resp.headers.get(name) == value, f"{name} on {resp.status_code}: {resp.headers}"


def assert_no_cors(resp: requests.Response) -> None:
    for name in CORS_HEADERS:
        assert name not in resp.headers, f"unexpected {name} on {resp.status_code}"


def assert_json(resp: requests.Response, status: int, expected_raw: str, *,
                check_now: bool = True) -> None:
    """Status + exact Content-Type + CORS + normalized raw body equality."""
    assert resp.status_code == status, (resp.status_code, resp.content)
    assert resp.headers.get("Content-Type") == JSON_CT
    assert_cors(resp)
    assert normalize(resp.content, check_now=check_now) == expected_raw


def assert_error(resp: requests.Response, status: int, code: str, message: str) -> None:
    assert resp.status_code == status, (resp.status_code, resp.content)
    assert resp.headers.get("Content-Type") == JSON_CT
    assert_cors(resp)
    assert resp.content.decode("utf-8") == error_body(code, message)


def assert_plain_404(resp: requests.Response, *, head: bool = False) -> None:
    assert resp.status_code == 404, (resp.status_code, resp.content)
    assert resp.headers.get("Content-Type") == PLAIN_404_CT
    assert resp.headers.get("Content-Length") == str(len(PLAIN_404_BODY))
    assert resp.content == (b"" if head else PLAIN_404_BODY)
    assert_cors(resp)


def assert_empty_204(resp: requests.Response) -> None:
    assert resp.status_code == 204, (resp.status_code, resp.content)
    assert resp.content == b""
    assert "Content-Type" not in resp.headers
    assert_cors(resp)


class Client:
    """Thin requests wrapper that never follows redirects and tracks created tasks."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.created: list[str] = []

    def request(self, method: str, path: str, *, data: bytes | str | None = None,
                headers: dict[str, str] | None = None, params: Any = None) -> requests.Response:
        if isinstance(data, str):
            data = data.encode("utf-8")
        resp = self.session.request(method, url(path), data=data, headers=headers,
                                    params=params, allow_redirects=False, timeout=10)
        if method == "POST" and resp.status_code == 201 and path.split("?")[0] == TASKS:
            self.created.append(resp.json()["id"])
        return resp

    def get(self, path: str, **kw: Any) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, data: bytes | str | None = None, **kw: Any) -> requests.Response:
        return self.request("POST", path, data=data, **kw)

    def put(self, path: str, data: bytes | str | None = None, **kw: Any) -> requests.Response:
        return self.request("PUT", path, data=data, **kw)

    def patch(self, path: str, data: bytes | str | None = None, **kw: Any) -> requests.Response:
        return self.request("PATCH", path, data=data, **kw)

    def delete(self, path: str, **kw: Any) -> requests.Response:
        return self.request("DELETE", path, **kw)

    def create(self, **fields: Any) -> dict[str, Any]:
        """POST a task (JSON-encoded ``fields``) and return the decoded body."""
        resp = self.post(TASKS, json.dumps(fields))
        assert resp.status_code == 201, (resp.status_code, resp.content)
        return resp.json()

    def task(self, task_id: str) -> dict[str, Any]:
        resp = self.get(f"{TASKS}/{task_id}")
        assert resp.status_code == 200, (resp.status_code, resp.content)
        return resp.json()

    def list(self, **params: Any) -> dict[str, Any]:
        resp = self.get(TASKS, params=params)
        assert resp.status_code == 200, (resp.status_code, resp.content)
        return resp.json()

    def iter_all(self, **params: Any) -> Iterator[dict[str, Any]]:
        """Walk every page via next_page_token."""
        token: str | None = None
        while True:
            query = dict(params, page_size=100)
            if token is not None:
                query["page_token"] = token
            page = self.list(**query)
            yield from page["tasks"] or []
            token = page.get("next_page_token")
            if token is None:
                return

    def cleanup(self) -> None:
        for task_id in self.created:
            self.session.delete(url(f"{TASKS}/{task_id}"), timeout=10)
        self.created.clear()
        self.session.close()
