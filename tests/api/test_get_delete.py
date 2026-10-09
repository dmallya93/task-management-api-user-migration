"""GET and DELETE /api/v1/tasks/:id (``getTask``, ``deleteTask``)."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from tests.api.helpers import (
    SEED_TITLES,
    TASKS,
    FakeClock,
    assert_empty,
    assert_error,
    assert_json,
    assert_plain_404,
    assert_redirect,
    create,
    list_json,
    seed_ids,
    task_json,
)


def test_get_task_exact_bytes(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "Get me", "tags": ["x"], "assigned_to": "al"})
    resp = client.get(f"{TASKS}/{task['id']}")
    assert_json(resp, 200)
    assert resp.data.decode() == task_json(
        id=task["id"], title="Get me", tags=["x"], assigned_to="al", created_at=clock.stamp(2)
    )


def test_get_seed_tasks_exact_bytes(client: FlaskClient, clock: FakeClock) -> None:
    ids = seed_ids(client)
    resp = client.get(f"{TASKS}/{ids['Implement Go REST API']}")
    assert_json(resp, 200)
    assert resp.data.decode() == task_json(
        id=ids["Implement Go REST API"], title="Implement Go REST API",
        description="Create REST API with Gin framework", priority="high",
        tags=["go", "rest", "api"], created_at=clock.stamp(0),
    )
    resp = client.get(f"{TASKS}/{ids['Add gRPC support']}")
    assert resp.data.decode() == task_json(
        id=ids["Add gRPC support"], title="Add gRPC support",
        description="Implement gRPC server with native Go support", priority="medium",
        tags=["go", "grpc", "protobuf"], created_at=clock.stamp(1),
    )


@pytest.mark.parametrize(
    ("target", "task_id"),
    [
        ("nope", "nope"),
        ("00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000000"),
        ("%20", " "),
        ("caf%C3%A9", "café"),
        ("a%3Cb", "a<b"),
    ],
)
def test_get_unknown_id_message_uses_decoded_id(client: FlaskClient, target: str, task_id: str) -> None:
    assert_error(client.get(f"{TASKS}/{target}"), 404, "NOT_FOUND", f"task with ID {task_id} not found")


def test_get_id_is_case_sensitive(client: FlaskClient) -> None:
    task = create(client, {"title": "c"})
    upper = task["id"].upper()
    assert_error(client.get(f"{TASKS}/{upper}"), 404, "NOT_FOUND", f"task with ID {upper} not found")


def test_get_extra_segment_and_encoded_slash_are_plain_404(client: FlaskClient) -> None:
    assert_plain_404(client.get(f"{TASKS}/a/b"))
    assert_plain_404(client.get(f"{TASKS}/a%2Fb"))


def test_delete_returns_empty_204_then_404(client: FlaskClient) -> None:
    task = create(client, {"title": "Doomed"})
    resp = client.delete(f"{TASKS}/{task['id']}")
    assert_empty(resp, 204)
    assert "Content-Length" not in resp.headers
    msg = f"task with ID {task['id']} not found"
    assert_error(client.get(f"{TASKS}/{task['id']}"), 404, "NOT_FOUND", msg)
    assert_error(client.delete(f"{TASKS}/{task['id']}"), 404, "NOT_FOUND", msg)
    assert list_json(client)["total_count"] == 2


def test_delete_seed_task(client: FlaskClient) -> None:
    ids = seed_ids(client)
    assert_empty(client.delete(f"{TASKS}/{ids[SEED_TITLES[0]]}"), 204)
    body = list_json(client)
    assert body["total_count"] == 1
    assert [t["title"] for t in body["tasks"]] == [SEED_TITLES[1]]


def test_delete_unknown_id(client: FlaskClient) -> None:
    assert_error(client.delete(f"{TASKS}/nope"), 404, "NOT_FOUND", "task with ID nope not found")


def test_delete_ignores_body(client: FlaskClient) -> None:
    task = create(client, {"title": "b"})
    assert_empty(client.delete(f"{TASKS}/{task['id']}", data=b"{garbage"), 204)


def test_get_and_delete_trailing_slash_redirects_without_running_handler(client: FlaskClient) -> None:
    task = create(client, {"title": "Stay"})
    path = f"{TASKS}/{task['id']}"
    assert_redirect(client.get(path + "/"), 301, path)
    assert_redirect(client.delete(path + "/"), 307, path)
    assert_json(client.get(path), 200)  # the redirected DELETE did not delete


def test_wrong_methods_on_item_are_plain_404(client: FlaskClient) -> None:
    task = create(client, {"title": "m"})
    for method in ("POST", "PATCH"):
        assert_plain_404(client.open(f"{TASKS}/{task['id']}", method=method, data=b"{}"))
    assert_plain_404(client.head(f"{TASKS}/{task['id']}"), head=True)
