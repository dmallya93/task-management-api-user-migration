"""GET and DELETE /api/v1/tasks/:id."""

from __future__ import annotations

import pytest

from tests.contract.helpers import TASKS, Client, assert_empty_204, assert_error, assert_json, task_body


def test_get_existing_task(client: Client) -> None:
    created = client.post(TASKS, b'{"title":"get me","tags":["a"]}')
    resp = client.get(created.headers["Location"])
    assert_json(resp, 200, task_body(title="get me", tags=["a"]))
    assert resp.content == created.content


@pytest.mark.parametrize("task_id", ["nope", "00000000-0000-4000-8000-000000000000", "%20"])
def test_get_unknown_task_404(client: Client, task_id: str) -> None:
    shown = " " if task_id == "%20" else task_id
    assert_error(client.get(f"{TASKS}/{task_id}"), 404, "NOT_FOUND", f"task with ID {shown} not found")


def test_delete_returns_204_empty_then_404(client: Client) -> None:
    task_id = client.create(title="delete me")["id"]
    assert_empty_204(client.delete(f"{TASKS}/{task_id}"))
    message = f"task with ID {task_id} not found"
    assert_error(client.get(f"{TASKS}/{task_id}"), 404, "NOT_FOUND", message)
    assert_error(client.delete(f"{TASKS}/{task_id}"), 404, "NOT_FOUND", message)


def test_delete_unknown_task_404(client: Client) -> None:
    assert_error(client.delete(f"{TASKS}/nope"), 404, "NOT_FOUND", "task with ID nope not found")


def test_delete_removes_task_from_listing(client: Client) -> None:
    task_id = client.create(title="gone")["id"]
    client.delete(f"{TASKS}/{task_id}")
    assert task_id not in {t["id"] for t in client.iter_all()}
