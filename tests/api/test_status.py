"""PATCH /api/v1/tasks/:id/status (``updateTaskStatus``)."""

from __future__ import annotations

import json

import pytest
from flask.testing import FlaskClient

from tests.api.helpers import (
    TASKS,
    FakeClock,
    assert_error,
    assert_json,
    assert_plain_404,
    assert_redirect,
    assert_validation,
    create,
    get,
    task_json,
)

REQUIRED = "Key: 'UpdateTaskStatusRequest.Status' Error:Field validation for 'Status' failed on the 'required' tag"


def patch(client: FlaskClient, task_id: str, body: str | bytes | dict):  # type: ignore[no-untyped-def,type-arg]
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    return client.patch(f"{TASKS}/{task_id}/status", data=data)


@pytest.fixture
def task(client: FlaskClient) -> dict:  # type: ignore[type-arg]
    return create(client, {"title": "S", "priority": "high", "tags": ["t"]})


def test_patch_status_exact_bytes(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "S"})  # #2
    resp = patch(client, task["id"], {"status": "in_progress"})  # #3
    assert_json(resp, 200)
    assert resp.data.decode() == task_json(
        id=task["id"], title="S", status="in_progress", created_at=clock.stamp(2), updated_at=clock.stamp(3)
    )


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed", "cancelled", "on_hold"])
def test_patch_each_valid_status(client: FlaskClient, task: dict, status: str) -> None:  # type: ignore[type-arg]
    updated = assert_json(patch(client, task["id"], {"status": status}), 200)
    assert updated["status"] == status
    assert get(client, task["id"]) == updated
    for key in ("title", "priority", "tags", "created_at"):
        assert updated[key] == task[key]


def test_patch_completed_sets_completed_at_once(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "S"})  # #2
    first = assert_json(patch(client, task["id"], {"status": "completed"}), 200)
    assert first["completed_at"] == clock.stamp(3) and first["updated_at"] == clock.stamp(4)
    assert assert_json(patch(client, task["id"], {"status": "on_hold"}), 200)["completed_at"] == clock.stamp(3)
    again = assert_json(patch(client, task["id"], {"status": "completed"}), 200)
    assert again["completed_at"] == clock.stamp(3)
    assert again["updated_at"] == clock.stamp(6)


def test_patch_same_status_bumps_updated_at(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "S"})
    updated = assert_json(patch(client, task["id"], {"status": "pending"}), 200)
    assert updated["updated_at"] == clock.stamp(3) != task["updated_at"]


def test_patch_ignores_other_fields(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    updated = assert_json(patch(client, task["id"], {"status": "cancelled", "title": "no", "priority": "low"}), 200)
    assert updated["title"] == "S" and updated["priority"] == "high"


@pytest.mark.parametrize("body", ["{}", '{"status":""}', '{"status":null}', "null", '{"title":"x"}'])
def test_patch_status_required(client: FlaskClient, task: dict, body: str) -> None:  # type: ignore[type-arg]
    assert_validation(patch(client, task["id"], body), REQUIRED)
    assert_validation(patch(client, "nope", body), REQUIRED)


@pytest.mark.parametrize("status", ["bad", "COMPLETED", "done", " pending"])
def test_patch_invalid_status_checked_before_existence(client: FlaskClient, task: dict, status: str) -> None:  # type: ignore[type-arg]
    assert_validation(patch(client, task["id"], {"status": status}), f"invalid status: {status}")
    assert_validation(patch(client, "nope", {"status": status}), f"invalid status: {status}")
    assert get(client, task["id"]) == task


def test_patch_unknown_id(client: FlaskClient) -> None:
    assert_error(patch(client, "nope", {"status": "completed"}), 404, "NOT_FOUND", "task with ID nope not found")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "EOF"),
        ("   ", "EOF"),
        ('{"status":', "unexpected EOF"),
        ("x", "invalid character 'x' looking for beginning of value"),
        ("[]", "json: cannot unmarshal array into Go value of type models.UpdateTaskStatusRequest"),
        ("1", "json: cannot unmarshal number into Go value of type models.UpdateTaskStatusRequest"),
        ('{"status":1}', "json: cannot unmarshal number into Go struct field UpdateTaskStatusRequest.status of type models.TaskStatus"),
        ('{"status":["completed"]}', "json: cannot unmarshal array into Go struct field UpdateTaskStatusRequest.status of type models.TaskStatus"),
    ],
)
def test_patch_binding_errors(client: FlaskClient, task: dict, body: str, message: str) -> None:  # type: ignore[type-arg]
    assert_validation(patch(client, task["id"], body), message)
    assert_validation(patch(client, "nope", body), message)


def test_patch_status_key_case_insensitive(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    assert assert_json(patch(client, task["id"], '{"STATUS":"completed"}'), 200)["status"] == "completed"


def test_patch_empty_id_segment_runs_handler(client: FlaskClient) -> None:
    """Gin quirk: ``:id`` matches the empty string here, so the handler runs with id ``""``."""
    assert_validation(client.patch(f"{TASKS}//status", data=b""), "EOF")
    assert_error(client.patch(f"{TASKS}//status", data='{"status":"completed"}'),
                 404, "NOT_FOUND", "task with ID  not found")


def test_patch_trailing_slash_redirect(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    path = f"{TASKS}/{task['id']}/status"
    assert_redirect(client.patch(path + "/", data='{"status":"completed"}'), 307, path)
    assert get(client, task["id"])["status"] == "pending"
    assert_plain_404(client.get(path + "/"))


def test_patch_item_without_status_redirects_quirk(client: FlaskClient) -> None:
    """Gin quirk: ``PATCH /api/v1/tasks/x/`` recommends ``/api/v1/tasks/x`` (which has no PATCH route)."""
    assert_redirect(client.patch(f"{TASKS}/x/", data=b""), 307, f"{TASKS}/x")


@pytest.mark.parametrize("method", ["GET", "PUT", "POST", "DELETE"])
def test_status_wrong_methods_are_plain_404(client: FlaskClient, task: dict, method: str) -> None:  # type: ignore[type-arg]
    assert_plain_404(client.open(f"{TASKS}/{task['id']}/status", method=method, data=b"{}"))
