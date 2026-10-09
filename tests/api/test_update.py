"""PUT /api/v1/tasks/:id (``updateTask``): partial updates, ordering side effects, errors."""

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

TITLE_MIN = "Key: 'UpdateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'min' tag"
TITLE_MAX = "Key: 'UpdateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'max' tag"


def put(client: FlaskClient, task_id: str, body: str | bytes | dict):  # type: ignore[no-untyped-def,type-arg]
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    return client.put(f"{TASKS}/{task_id}", data=data)


@pytest.fixture
def task(client: FlaskClient) -> dict:  # type: ignore[type-arg]
    return create(client, {"title": "Orig", "description": "d", "tags": ["a"], "priority": "low"})


def test_update_all_fields_exact_bytes(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "Orig"})  # now() #2
    resp = put(client, task["id"], {
        "title": "New", "description": "nd", "status": "in_progress", "priority": "critical",
        "tags": ["x", "y"], "assigned_to": "bob", "due_date": "2030-05-06T07:08:09.5+02:00",
    })  # now() #3 for updated_at
    assert_json(resp, 200)
    expected = task_json(
        id=task["id"], title="New", description="nd", status="in_progress", priority="critical",
        tags=["x", "y"], assigned_to="bob", created_at=clock.stamp(2), updated_at=clock.stamp(3),
        due_date="2030-05-06T07:08:09.5+02:00",
    )
    assert resp.data.decode() == expected
    assert client.get(f"{TASKS}/{task['id']}").data.decode() == expected


def test_update_partial_keeps_other_fields(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    updated = assert_json(put(client, task["id"], {"description": "only"}), 200)
    assert updated["description"] == "only"
    for key in ("title", "status", "priority", "tags", "created_at", "id"):
        assert updated[key] == task[key]
    assert updated["updated_at"] > task["updated_at"]


@pytest.mark.parametrize("body", ["{}", "null", '{"tags":null,"title":null,"status":null}', '{"unknown":1}'])
def test_update_noop_still_bumps_updated_at(client: FlaskClient, clock: FakeClock, body: str) -> None:
    task = create(client, {"title": "Orig", "tags": ["a"]})
    resp = put(client, task["id"], body)
    assert_json(resp, 200)
    assert resp.data.decode() == task_json(
        id=task["id"], title="Orig", tags=["a"], created_at=clock.stamp(2), updated_at=clock.stamp(3)
    )


def test_update_empty_tags_clears_but_null_keeps(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    assert assert_json(put(client, task["id"], '{"tags":null}'), 200)["tags"] == ["a"]
    resp = put(client, task["id"], '{"tags":[]}')
    assert assert_json(resp, 200)["tags"] == []
    assert b'"tags":[]' in resp.data


def test_update_empty_strings_for_description_and_assigned_to(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    updated = assert_json(put(client, task["id"], '{"description":"","assigned_to":""}'), 200)
    assert updated["description"] == ""
    assert updated["assigned_to"] == ""


def test_update_assigned_to_and_due_date_cannot_be_cleared_with_null(client: FlaskClient) -> None:
    task = create(client, {"title": "t", "assigned_to": "al", "due_date": "2030-01-01T00:00:00Z"})
    updated = assert_json(put(client, task["id"], '{"assigned_to":null,"due_date":null}'), 200)
    assert updated["assigned_to"] == "al"
    assert updated["due_date"] == "2030-01-01T00:00:00Z"


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed", "cancelled", "on_hold"])
def test_update_each_valid_status(client: FlaskClient, task: dict, status: str) -> None:  # type: ignore[type-arg]
    updated = assert_json(put(client, task["id"], {"status": status}), 200)
    assert updated["status"] == status
    assert ("completed_at" in updated) == (status == "completed")


def test_completed_at_set_once_and_never_cleared(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "c"})  # #2
    first = assert_json(put(client, task["id"], {"status": "completed"}), 200)  # #3 completed_at, #4 updated_at
    assert first["completed_at"] == clock.stamp(3)
    assert first["updated_at"] == clock.stamp(4)
    back = assert_json(put(client, task["id"], {"status": "pending"}), 200)
    assert back["status"] == "pending" and back["completed_at"] == clock.stamp(3)
    again = assert_json(put(client, task["id"], {"status": "completed"}), 200)
    assert again["completed_at"] == clock.stamp(3)
    assert get(client, task["id"])["completed_at"] == clock.stamp(3)
    # completed_at is the last key (Go struct order).
    assert client.get(f"{TASKS}/{task['id']}").data.endswith(
        f',"completed_at":"{clock.stamp(3)}"}}'.encode()
    )


@pytest.mark.parametrize("ch", ["a", "é", "😀"])
def test_update_title_rune_limits(client: FlaskClient, task: dict, ch: str) -> None:  # type: ignore[type-arg]
    assert assert_json(put(client, task["id"], {"title": ch * 200}), 200)["title"] == ch * 200
    assert_validation(put(client, task["id"], {"title": ch * 201}), TITLE_MAX)
    assert get(client, task["id"])["title"] == ch * 200


def test_update_title_min(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    assert_validation(put(client, task["id"], {"title": ""}), TITLE_MIN)
    assert get(client, task["id"]) == task  # validation happens before anything is applied


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"status":"bad"}', "invalid status: bad"),
        ('{"status":""}', "invalid status: "),
        ('{"status":"COMPLETED"}', "invalid status: COMPLETED"),
        ('{"priority":"bad"}', "invalid priority: bad"),
        ('{"priority":""}', "invalid priority: "),
    ],
)
def test_update_invalid_enums(client: FlaskClient, task: dict, body: str, message: str) -> None:  # type: ignore[type-arg]
    assert_validation(put(client, task["id"], body), message)


def test_update_invalid_status_keeps_earlier_fields_without_bumping(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    resp = put(client, task["id"], {"title": "Changed", "description": "nd", "status": "bad",
                                     "priority": "high", "tags": ["z"]})
    assert_validation(resp, "invalid status: bad")
    after = get(client, task["id"])
    assert after["title"] == "Changed" and after["description"] == "nd"  # applied before status
    assert after["priority"] == "low" and after["tags"] == ["a"]  # after status: not applied
    assert after["updated_at"] == task["updated_at"]  # not bumped


def test_update_invalid_priority_keeps_status_and_completed_at(client: FlaskClient, clock: FakeClock) -> None:
    task = create(client, {"title": "Orig", "tags": ["a"]})  # #2
    resp = put(client, task["id"], {"title": "T2", "status": "completed", "priority": "bad",
                                    "tags": ["z"], "assigned_to": "x", "due_date": "2030-01-01T00:00:00Z"})
    assert_validation(resp, "invalid priority: bad")
    assert client.get(f"{TASKS}/{task['id']}").data.decode() == task_json(
        id=task["id"], title="T2", status="completed", tags=["a"], created_at=clock.stamp(2),
        completed_at=clock.stamp(3),
    )


def test_update_unknown_id(client: FlaskClient) -> None:
    assert_error(put(client, "nope", {"title": "x"}), 404, "NOT_FOUND", "task with ID nope not found")
    assert_error(put(client, "nope", "{}"), 404, "NOT_FOUND", "task with ID nope not found")


@pytest.mark.parametrize("body", ['{"status":"bad"}', '{"priority":"bad"}'])
def test_update_unknown_id_checked_before_enum_validity(client: FlaskClient, body: str) -> None:
    assert_error(put(client, "nope", body), 404, "NOT_FOUND", "task with ID nope not found")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "EOF"),
        ("{", "unexpected EOF"),
        ('{"title":""}', TITLE_MIN),
        ("[]", "json: cannot unmarshal array into Go value of type models.UpdateTaskRequest"),
        ('"s"', "json: cannot unmarshal string into Go value of type models.UpdateTaskRequest"),
        ("1", "json: cannot unmarshal number into Go value of type models.UpdateTaskRequest"),
        ('{"status":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.status of type models.TaskStatus"),
        ('{"priority":true}', "json: cannot unmarshal bool into Go struct field UpdateTaskRequest.priority of type models.TaskPriority"),
        ('{"tags":"x"}', "json: cannot unmarshal string into Go struct field UpdateTaskRequest.tags of type []string"),
        ('{"title":5}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.title of type string"),
        ('{"due_date":5}', "Time.UnmarshalJSON: input is not a JSON string"),
        ('{"due_date":"x"}', 'parsing time "x" as "2006-01-02T15:04:05Z07:00": cannot parse "x" as "2006"'),
    ],
)
def test_update_binding_errors_are_400_even_for_unknown_id(client: FlaskClient, task: dict,  # type: ignore[type-arg]
                                                            body: str, message: str) -> None:
    assert_validation(put(client, "nope", body), message)
    assert_validation(put(client, task["id"], body), message)
    assert get(client, task["id"]) == task


def test_update_returns_live_task(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    updated = assert_json(put(client, task["id"], {"title": "Live"}), 200)
    assert get(client, task["id"]) == updated


def test_update_trailing_slash_is_307_without_update(client: FlaskClient, task: dict) -> None:  # type: ignore[type-arg]
    path = f"{TASKS}/{task['id']}"
    assert_redirect(client.put(path + "/", data='{"title":"no"}'), 307, path)
    assert get(client, task["id"])["title"] == "Orig"


def test_put_on_collection_is_plain_404(client: FlaskClient) -> None:
    assert_plain_404(client.put(TASKS, data="{}"))
