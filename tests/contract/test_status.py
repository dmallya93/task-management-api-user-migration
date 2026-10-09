"""PATCH /api/v1/tasks/:id/status."""

from __future__ import annotations

import json
import time

import pytest

from tests.contract.helpers import TASKS, Client, assert_error, assert_json, assert_now, task_body

V = "VALIDATION_ERROR"
REQUIRED = (
    "Key: 'UpdateTaskStatusRequest.Status' Error:Field validation for 'Status' failed on the 'required' tag"
)


def _path(task_id: str) -> str:
    return f"{TASKS}/{task_id}/status"


@pytest.mark.parametrize("status", ["pending", "in_progress", "cancelled", "on_hold"])
def test_patch_status_valid(client: Client, status: str) -> None:
    task = client.create(title="s", tags=["k"])
    time.sleep(0.005)
    resp = client.patch(_path(task["id"]), json.dumps({"status": status}))
    assert_json(resp, 200, task_body(title="s", tags=["k"], status=status))
    assert resp.json()["updated_at"] != task["updated_at"]
    assert_json(client.get(f"{TASKS}/{task['id']}"), 200, task_body(title="s", tags=["k"], status=status))


def test_patch_completed_sets_completed_at_once(client: Client) -> None:
    task = client.create(title="c")
    resp = client.patch(_path(task["id"]), b'{"status":"completed"}')
    assert_json(resp, 200, task_body(title="c", status="completed", completed=True))
    completed_at = resp.json()["completed_at"]
    assert_now(completed_at)
    time.sleep(0.005)
    for status in ("in_progress", "completed", "pending"):
        resp = client.patch(_path(task["id"]), json.dumps({"status": status}))
        assert resp.status_code == 200
        assert resp.json()["completed_at"] == completed_at
    assert_json(client.get(f"{TASKS}/{task['id']}"), 200, task_body(title="c", completed=True))


def test_patch_status_key_case_insensitive(client: Client) -> None:
    task = client.create(title="ci")
    resp = client.patch(_path(task["id"]), b'{"STATUS":"on_hold","x":1}')
    assert_json(resp, 200, task_body(title="ci", status="on_hold"))


def test_patch_ignores_other_fields(client: Client) -> None:
    task = client.create(title="only status")
    resp = client.patch(_path(task["id"]), b'{"status":"in_progress","title":"nope","priority":"bad"}')
    assert_json(resp, 200, task_body(title="only status", status="in_progress"))


@pytest.mark.parametrize("body", [b"{}", b'{"status":""}', b'{"status":null}', b"null", b'{"other":"x"}'])
def test_patch_missing_status_required_error(client: Client, body: bytes) -> None:
    task = client.create(title="r")
    assert_error(client.patch(_path(task["id"]), body), 400, V, REQUIRED)
    assert client.task(task["id"]) == task


@pytest.mark.parametrize(("status", "message"), [
    ("bad", "invalid status: bad"), ("COMPLETED", "invalid status: COMPLETED"), (" pending", "invalid status:  pending"),
])
def test_patch_invalid_status_no_mutation(client: Client, status: str, message: str) -> None:
    task = client.create(title="i")
    assert_error(client.patch(_path(task["id"]), json.dumps({"status": status})), 400, V, message)
    assert client.task(task["id"]) == task


def test_patch_invalid_status_checked_before_existence(client: Client) -> None:
    assert_error(client.patch(_path("nope"), b'{"status":"bad"}'), 400, V, "invalid status: bad")


def test_patch_missing_status_checked_before_existence(client: Client) -> None:
    assert_error(client.patch(_path("nope"), b"{}"), 400, V, REQUIRED)


def test_patch_unknown_id_valid_status_404(client: Client) -> None:
    resp = client.patch(_path("nope"), b'{"status":"completed"}')
    assert_error(resp, 404, "NOT_FOUND", "task with ID nope not found")


@pytest.mark.parametrize(("body", "message"), [
    (b"", "EOF"),
    (b"{", "unexpected EOF"),
    (b'{"status":}', "invalid character '}' looking for beginning of value"),
    (b"[]", "json: cannot unmarshal array into Go value of type models.UpdateTaskStatusRequest"),
    (b'{"status":1}', "json: cannot unmarshal number into Go struct field UpdateTaskStatusRequest.status of type models.TaskStatus"),
    (b'{"status":["completed"]}', "json: cannot unmarshal array into Go struct field UpdateTaskStatusRequest.status of type models.TaskStatus"),
])
def test_patch_bind_errors(client: Client, body: bytes, message: str) -> None:
    task = client.create(title="b")
    assert_error(client.patch(_path(task["id"]), body), 400, V, message)
    assert_error(client.patch(_path("nope"), body), 400, V, message)


def test_patch_ignores_data_after_json_value(client: Client) -> None:
    task = client.create(title="t")
    resp = client.patch(_path(task["id"]), b'{"status":"on_hold"} trailing')
    assert_json(resp, 200, task_body(title="t", status="on_hold"))
