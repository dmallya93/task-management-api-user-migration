"""Gin routing emulation and ``corsMiddleware``: 404s, OPTIONS, redirects, CORS, raw targets."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from tests.api.helpers import (
    CORS_HEADERS,
    TASKS,
    assert_cors,
    assert_empty,
    assert_json,
    assert_no_cors,
    assert_plain_404,
    assert_redirect,
    create,
    get,
    open_raw,
)

# -- CORS ------------------------------------------------------------------------


def test_cors_on_every_kind_of_handler_response(client: FlaskClient) -> None:
    task = create(client, {"title": "c"})
    responses = [
        client.get("/health"),
        client.get(TASKS),
        client.post(TASKS, data='{"title":"x"}'),
        client.post(TASKS, data=""),
        client.get(f"{TASKS}/nope"),
        client.put(f"{TASKS}/{task['id']}", data="{}"),
        client.patch(f"{TASKS}/{task['id']}/status", data='{"status":"bad"}'),
        client.delete(f"{TASKS}/{task['id']}"),
        client.get(f"{TASKS}?page_token=-1"),
        client.get("/nope"),
        client.options("/nope"),
    ]
    assert [r.status_code for r in responses] == [200, 200, 201, 400, 404, 200, 400, 204, 500, 404, 204]
    for resp in responses:
        assert_cors(resp)
        assert "Vary" not in resp.headers
        assert "Access-Control-Allow-Credentials" not in resp.headers
        assert "Server" not in resp.headers


def test_cors_does_not_depend_on_origin(client: FlaskClient) -> None:
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {}):
        resp = client.get("/health", headers=headers)
        for name, value in CORS_HEADERS.items():
            assert resp.headers[name] == value


# -- OPTIONS -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "/health", "/health/", "/nope", TASKS, f"{TASKS}/", f"{TASKS}/x", f"{TASKS}/x/",
     f"{TASKS}/x/status", f"{TASKS}/x/status/", "/a/b/c", "//"],
)
def test_options_on_any_path_is_empty_204(client: FlaskClient, path: str) -> None:
    resp = client.options(path, headers={"Origin": "https://x", "Access-Control-Request-Method": "DELETE"},
                          data=b"{junk")
    assert_empty(resp, 204)
    assert resp.status == "204 No Content"
    assert "Content-Length" not in resp.headers
    assert "Allow" not in resp.headers


def test_options_does_not_run_handler(client: FlaskClient) -> None:
    task = create(client, {"title": "keep"})
    assert_empty(client.options(f"{TASKS}/{task['id']}"), 204)
    assert get(client, task["id"])["title"] == "keep"


def test_options_asterisk_is_answered_by_net_http(client: FlaskClient) -> None:
    """``OPTIONS *`` never reaches Gin: net/http replies 200 with Content-Length 0 and no CORS."""
    resp = open_raw(client, "OPTIONS", "*")
    assert resp.status == "200 OK"
    assert resp.data == b""
    assert resp.headers.get("Content-Length") == "0"
    assert "Content-Type" not in resp.headers
    assert_no_cors(resp)


def test_get_asterisk_is_plain_404(client: FlaskClient) -> None:
    assert_plain_404(open_raw(client, "GET", "*"))


# -- 404s --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "/nope", "/api", "/api/v1", "/api/v1/", "/Health", "/HEALTH", "/API/v1/tasks", "/api/v1/Tasks",
     "//api/v1/tasks", "/api/v2/tasks", f"{TASKS}/x/status/extra", f"{TASKS}/x/y", "/health/x",
     "/healthz", f"{TASKS}x"],
)
def test_unknown_routes_are_plain_404(client: FlaskClient, path: str) -> None:
    assert_plain_404(client.get(path))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/health"), ("PUT", "/health"), ("PATCH", "/health"), ("DELETE", "/health"),
        ("PUT", TASKS), ("PATCH", TASKS), ("DELETE", TASKS),
        ("POST", f"{TASKS}/x"), ("PATCH", f"{TASKS}/x"),
        ("GET", f"{TASKS}/x/status"), ("PUT", f"{TASKS}/x/status"), ("POST", f"{TASKS}/x/status"),
        ("DELETE", f"{TASKS}/x/status"),
        ("PUT", f"{TASKS}/"), ("DELETE", f"{TASKS}/"), ("PATCH", f"{TASKS}/"),
        ("POST", "/health/"), ("GET", f"{TASKS}/x/status/"),
        ("TRACE", "/health"), ("FOO", "/health"), ("FOO", "/health/"), ("CONNECT", "/health/"),
    ],
)
def test_wrong_method_is_plain_404_never_405(client: FlaskClient, method: str, path: str) -> None:
    assert_plain_404(client.open(path, method=method, data=b"{}"))


@pytest.mark.parametrize("path", ["/health", TASKS, f"{TASKS}/x", f"{TASKS}/x/status", "/health/", "/nope"])
def test_head_is_never_routed(client: FlaskClient, path: str) -> None:
    assert_plain_404(client.head(path), head=True)


# -- trailing-slash redirects --------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status", "location"),
    [
        ("GET", "/health/", 301, "/health"),
        ("GET", f"{TASKS}/", 301, TASKS),
        ("POST", f"{TASKS}/", 307, TASKS),
        ("GET", f"{TASKS}/x/", 301, f"{TASKS}/x"),
        ("PUT", f"{TASKS}/x/", 307, f"{TASKS}/x"),
        ("DELETE", f"{TASKS}/x/", 307, f"{TASKS}/x"),
        ("PATCH", f"{TASKS}/x/", 307, f"{TASKS}/x"),
        ("PATCH", f"{TASKS}/x/status/", 307, f"{TASKS}/x/status"),
        ("GET", f"{TASKS}//", 301, f"{TASKS}/"),
    ],
)
def test_trailing_slash_redirects(client: FlaskClient, method: str, path: str, status: int, location: str) -> None:
    assert_redirect(client.open(path, method=method, data=b'{"title":"x"}'), status, location)


def test_redirected_post_creates_nothing(client: FlaskClient) -> None:
    assert_redirect(client.post(f"{TASKS}/", data='{"title":"x"}'), 307, TASKS)
    assert assert_json(client.get(TASKS), 200)["total_count"] == 2


def test_redirect_body_exact_bytes(client: FlaskClient) -> None:
    resp = client.get(f"{TASKS}/")
    assert resp.data == b'<a href="/api/v1/tasks">Moved Permanently</a>.\n\n'
    assert resp.headers["Content-Length"] == "48"


@pytest.mark.parametrize(
    ("target", "location", "body"),
    [
        (f"{TASKS}/?a=b&c=d", f"{TASKS}?a=b&c=d", b'<a href="/api/v1/tasks?a=b&amp;c=d">Moved Permanently</a>.\n\n'),
        (f"{TASKS}/?q=%22'<", f"{TASKS}?q=%22'<", b'<a href="/api/v1/tasks?q=%22&#39;&lt;">Moved Permanently</a>.\n\n'),
        (f'{TASKS}/?a="b', f'{TASKS}?a="b', b'<a href="/api/v1/tasks?a=&#34;b">Moved Permanently</a>.\n\n'),
        (f"{TASKS}/caf%C3%A9/", f"{TASKS}/caf%C3%A9", b'<a href="/api/v1/tasks/caf%C3%A9">Moved Permanently</a>.\n\n'),
        (f"{TASKS}/a%20b/", f"{TASKS}/a%20b", b'<a href="/api/v1/tasks/a%20b">Moved Permanently</a>.\n\n'),
    ],
)
def test_redirect_preserves_query_and_escapes_html(client: FlaskClient, target: str, location: str,
                                                   body: bytes) -> None:
    resp = open_raw(client, "GET", target)
    assert resp.status_code == 301
    assert resp.headers["Location"] == location
    assert resp.data == body
    assert_no_cors(resp)


def test_redirect_keeps_bare_question_mark(client: FlaskClient) -> None:
    resp = open_raw(client, "GET", f"{TASKS}/?")
    assert_redirect(resp, 301, f"{TASKS}?")


def test_redirect_honours_x_forwarded_prefix(client: FlaskClient) -> None:
    resp = client.get(f"{TASKS}/", headers={"X-Forwarded-Prefix": "/pre"})
    assert_redirect(resp, 301, "/pre/api/v1/tasks")
    resp = client.post(f"{TASKS}/", headers={"X-Forwarded-Prefix": "/a b<>/"})
    assert_redirect(resp, 307, "/ab/api/v1/tasks")


# -- raw request targets -------------------------------------------------------------


@pytest.mark.parametrize("target", [f"{TASKS}/%zz", f"{TASKS}/%", "/health%", "health"])
def test_unparsable_target_is_net_http_400_without_cors(client: FlaskClient, target: str) -> None:
    resp = open_raw(client, "GET", target)
    assert resp.status == "400 Bad Request"
    assert resp.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert resp.data == b"400 Bad Request"
    assert_no_cors(resp)


def test_absolute_form_target_routes_by_path(client: FlaskClient) -> None:
    assert assert_json(open_raw(client, "GET", "http://h/health"), 200)["status"] == "healthy"


def test_percent_encoded_path_is_decoded_for_routing(client: FlaskClient) -> None:
    assert assert_json(client.get("/%68ealth"), 200)["status"] == "healthy"


def test_reason_phrases_match_net_http(client: FlaskClient) -> None:
    assert client.post(TASKS, data='{"title":"x"}').status == "201 Created"
    assert client.post(TASKS, data="").status == "400 Bad Request"
    assert client.get(f"{TASKS}/x/").status == "301 Moved Permanently"
    assert client.put(f"{TASKS}/x/").status == "307 Temporary Redirect"
