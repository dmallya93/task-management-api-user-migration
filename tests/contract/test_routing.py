"""Gin routing semantics: CORS, OPTIONS short-circuit, 404 (never 405), trailing-slash redirects."""

from __future__ import annotations

import pytest

from tests.contract.helpers import (
    REDIRECT_301_CT, TASKS, Client, assert_cors, assert_empty_204, assert_no_cors, assert_plain_404,
)

ID = "00000000-0000-4000-8000-000000000000"


def test_cors_on_success_and_error_responses(client: Client) -> None:
    created = client.post(TASKS, b'{"title":"cors"}')
    task_id = created.json()["id"]
    responses = [
        client.get("/health"),                                   # 200
        created,                                                 # 201
        client.get(TASKS, params={"page_size": "0"}),            # 400
        client.get(f"{TASKS}/nope"),                             # 404 json
        client.get("/nope"),                                     # 404 plain
        client.get(TASKS, params={"page_token": "-1"}),          # 500
        client.request("OPTIONS", TASKS),                        # 204 options
        client.delete(f"{TASKS}/{task_id}"),                     # 204 delete
    ]
    assert [r.status_code for r in responses] == [200, 201, 400, 404, 404, 500, 204, 204]
    for resp in responses:
        assert_cors(resp)


def test_cors_ignores_request_origin(client: Client) -> None:
    resp = client.get("/health", headers={"Origin": "http://example.com"})
    assert_cors(resp)
    assert "Access-Control-Allow-Credentials" not in resp.headers
    assert "Vary" not in resp.headers


@pytest.mark.parametrize("path", [
    "/health", TASKS, f"{TASKS}/{ID}", f"{TASKS}/{ID}/status", f"{TASKS}/", "/health/", "/nope",
    "/", "/api/v1/tasks/a/b/c", f"{TASKS}?page_token=-1",
])
def test_options_any_path_204_empty(client: Client, path: str) -> None:
    assert_empty_204(client.request("OPTIONS", path))


def test_options_preflight_204(client: Client) -> None:
    resp = client.request("OPTIONS", TASKS, headers={
        "Origin": "http://example.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert_empty_204(resp)


def test_options_does_not_execute_handler(client: Client) -> None:
    task_id = client.create(title="keep")["id"]
    assert_empty_204(client.request("OPTIONS", f"{TASKS}/{task_id}", data=b'{"x":1}'))
    assert client.get(f"{TASKS}/{task_id}").status_code == 200


@pytest.mark.parametrize(("method", "path"), [
    ("DELETE", TASKS), ("PUT", TASKS), ("PATCH", TASKS),
    ("POST", "/health"), ("PUT", "/health"), ("DELETE", "/health"), ("PATCH", "/health"),
    ("PATCH", f"{TASKS}/{ID}"), ("POST", f"{TASKS}/{ID}"),
    ("GET", f"{TASKS}/{ID}/status"), ("PUT", f"{TASKS}/{ID}/status"),
    ("POST", f"{TASKS}/{ID}/status"), ("DELETE", f"{TASKS}/{ID}/status"),
    ("PUT", f"{TASKS}/"), ("DELETE", f"{TASKS}/"), ("PATCH", f"{TASKS}/"),
    ("POST", "/health/"), ("GET", f"{TASKS}/{ID}/status/"),
])
def test_wrong_method_is_plain_404(client: Client, method: str, path: str) -> None:
    assert_plain_404(client.request(method, path, data=b'{"title":"x","status":"completed"}'))


@pytest.mark.parametrize("path", ["/health", TASKS, f"{TASKS}/{ID}"])
def test_head_is_not_routed(client: Client, path: str) -> None:
    assert_plain_404(client.request("HEAD", path), head=True)


@pytest.mark.parametrize("path", [
    "/", "/nope", "/nope/", "/api", "/api/v1", "/api/v1/", "/api/v2/tasks", "/API/v1/tasks", "/Health",
    "/healthz", f"{TASKS}/a/b", f"{TASKS}/a/b/", f"{TASKS}/{ID}/status/x", "/api/v1/tasksx",
])
def test_unknown_path_is_plain_404(client: Client, path: str) -> None:
    assert_plain_404(client.get(path))
    assert_plain_404(client.post(path, b"{}"))


def test_wrong_method_does_not_mutate(client: Client) -> None:
    task_id = client.create(title="stay")["id"]
    assert_plain_404(client.request("PATCH", f"{TASKS}/{task_id}", data=b'{"title":"x"}'))
    assert_plain_404(client.request("POST", f"{TASKS}/{task_id}", data=b'{"title":"x"}'))
    assert client.task(task_id)["title"] == "stay"


def _assert_301(resp, location: str, href: str | None = None) -> None:
    assert resp.status_code == 301, (resp.status_code, resp.content)
    assert resp.headers["Location"] == location
    assert resp.headers["Content-Type"] == REDIRECT_301_CT
    assert resp.content == f'<a href="{href or location}">Moved Permanently</a>.\n\n'.encode()
    assert_no_cors(resp)


def _assert_307(resp, location: str) -> None:
    assert resp.status_code == 307, (resp.status_code, resp.content)
    assert resp.headers["Location"] == location
    assert resp.content == b""
    assert "Content-Type" not in resp.headers
    assert resp.headers.get("Content-Length") == "0"
    assert_no_cors(resp)


@pytest.mark.parametrize(("path", "location"), [
    (f"{TASKS}/", TASKS),
    ("/health/", "/health"),
    (f"{TASKS}/{ID}/", f"{TASKS}/{ID}"),
    (f"{TASKS}/?page_size=1", f"{TASKS}?page_size=1"),
    (f"{TASKS}/?page_token=-1", f"{TASKS}?page_token=-1"),
])
def test_get_trailing_slash_301(client: Client, path: str, location: str) -> None:
    _assert_301(client.get(path), location)


def test_get_trailing_slash_301_html_escapes_href(client: Client) -> None:
    _assert_301(client.get(f"{TASKS}/?a=b&c=d"), f"{TASKS}?a=b&c=d", href=f"{TASKS}?a=b&amp;c=d")


@pytest.mark.parametrize(("method", "path", "location"), [
    ("POST", f"{TASKS}/", TASKS),
    ("POST", f"{TASKS}/?a=b", f"{TASKS}?a=b"),
    ("PUT", f"{TASKS}/{ID}/", f"{TASKS}/{ID}"),
    ("DELETE", f"{TASKS}/{ID}/", f"{TASKS}/{ID}"),
    ("PATCH", f"{TASKS}/{ID}/status/", f"{TASKS}/{ID}/status"),
])
def test_non_get_trailing_slash_307(client: Client, method: str, path: str, location: str) -> None:
    _assert_307(client.request(method, path, data=b'{"title":"x","status":"completed"}'), location)


def test_redirect_does_not_execute_handler(client: Client) -> None:
    before = {t["id"] for t in client.iter_all()}
    _assert_307(client.post(f"{TASKS}/", b'{"title":"not created"}'), TASKS)
    task_id = client.create(title="survivor")["id"]
    _assert_307(client.delete(f"{TASKS}/{task_id}/"), f"{TASKS}/{task_id}")
    _assert_307(client.patch(f"{TASKS}/{task_id}/status/", b'{"status":"completed"}'),
                f"{TASKS}/{task_id}/status")
    task = client.task(task_id)
    assert task["status"] == "pending"
    assert {t["id"] for t in client.iter_all()} - before == {task_id}


def test_plain_404_headers_exact(client: Client) -> None:
    resp = client.get("/nope")
    assert_plain_404(resp)
    assert "Location" not in resp.headers
