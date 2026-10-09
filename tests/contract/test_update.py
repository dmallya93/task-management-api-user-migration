"""PUT /api/v1/tasks/:id (TaskService.UpdateTask semantics)."""

from __future__ import annotations

import json
import time

import pytest

from tests.contract.helpers import TASKS, Client, assert_error, assert_json, assert_now, task_body

V = "VALIDATION_ERROR"
MIN_TITLE = "Key: 'UpdateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'min' tag"
MAX_TITLE = "Key: 'UpdateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'max' tag"
LAYOUT = "2006-01-02T15:04:05Z07:00"

FULL = dict(title="orig", description="d", priority="low", tags=["a"], assigned_to="bob",
            due_date="2030-01-01T00:00:00Z")


def _full(client: Client) -> dict:
    task = client.create(**FULL)
    time.sleep(0.005)  # make a bumped updated_at distinguishable
    return task


def test_put_updates_all_fields(client: Client) -> None:
    task = _full(client)
    body = {"title": "new", "description": "d2", "status": "in_progress", "priority": "critical",
            "tags": ["x", "y"], "assigned_to": "carol", "due_date": "2031-05-06T07:08:09.5+01:00"}
    resp = client.put(f"{TASKS}/{task['id']}", json.dumps(body))
    expected = task_body(title="new", description="d2", status="in_progress", priority="critical",
                         tags=["x", "y"], assigned_to="carol", due_date="2031-05-06T07:08:09.5+01:00")
    assert_json(resp, 200, expected)
    updated = resp.json()
    assert updated["id"] == task["id"]
    assert updated["created_at"] == task["created_at"]
    assert updated["updated_at"] != task["updated_at"]
    assert_now(updated["updated_at"])
    assert_json(client.get(f"{TASKS}/{task['id']}"), 200, expected)


def test_put_single_field_leaves_others(client: Client) -> None:
    task = _full(client)
    resp = client.put(f"{TASKS}/{task['id']}", b'{"description":""}')
    assert_json(resp, 200, task_body(**dict(FULL, description="")))


def test_put_empty_tags_list_clears_tags(client: Client) -> None:
    task = _full(client)
    assert_json(client.put(f"{TASKS}/{task['id']}", b'{"tags":[]}'), 200, task_body(**dict(FULL, tags=[])))


@pytest.mark.parametrize("body", [
    b'{"tags":null}', b'{"assigned_to":null}', b'{"due_date":null}',
    b'{"title":null,"description":null,"status":null,"priority":null}',
    b'{"tags":null,"assigned_to":null,"due_date":null}', b"{}", b"null",
])
def test_put_null_or_absent_fields_unchanged_but_updated_at_bumped(client: Client, body: bytes) -> None:
    task = _full(client)
    resp = client.put(f"{TASKS}/{task['id']}", body)
    assert_json(resp, 200, task_body(**FULL))
    assert resp.json()["updated_at"] != task["updated_at"]


def test_put_assigned_to_empty_string_serialized(client: Client) -> None:
    task = _full(client)
    resp = client.put(f"{TASKS}/{task['id']}", b'{"assigned_to":""}')
    assert_json(resp, 200, task_body(**dict(FULL, assigned_to="")))


def test_put_sets_optional_fields_on_bare_task(client: Client) -> None:
    task = client.create(title="bare")
    resp = client.put(f"{TASKS}/{task['id']}", b'{"assigned_to":"z","due_date":"2030-06-01T12:00:00.000000100+09:00"}')
    assert_json(resp, 200, task_body(title="bare", assigned_to="z", due_date="2030-06-01T12:00:00.0000001+09:00"))


def test_put_keys_case_insensitive_and_unknown_ignored(client: Client) -> None:
    task = _full(client)
    resp = client.put(f"{TASKS}/{task['id']}", b'{"Title":"ci","STATUS":"on_hold","bogus":1,"id":"x"}')
    assert_json(resp, 200, task_body(**dict(FULL, title="ci", status="on_hold")))


@pytest.mark.parametrize("title", ["a" * 200, "é" * 200, "😀" * 200, "x"])
def test_put_title_length_ok(client: Client, title: str) -> None:
    task = _full(client)
    resp = client.put(f"{TASKS}/{task['id']}", json.dumps({"title": title}, ensure_ascii=False).encode())
    assert_json(resp, 200, task_body(**dict(FULL, title=title)))


@pytest.mark.parametrize(("title", "message"), [
    ("", MIN_TITLE), ("a" * 201, MAX_TITLE), ("é" * 201, MAX_TITLE), ("😀" * 201, MAX_TITLE),
])
def test_put_title_validation_error_no_mutation(client: Client, title: str, message: str) -> None:
    task = _full(client)
    body = json.dumps({"title": title, "description": "changed"}, ensure_ascii=False).encode()
    assert_error(client.put(f"{TASKS}/{task['id']}", body), 400, V, message)
    after = client.task(task["id"])
    assert after == task


@pytest.mark.parametrize(("body", "message", "changed"), [
    (b'{"title":"new","status":"bad"}', "invalid status: bad", {"title": "new"}),
    (b'{"title":"new","description":"nd","status":""}', "invalid status: ", {"title": "new", "description": "nd"}),
    (b'{"title":"new","priority":"bad","tags":["z"]}', "invalid priority: bad", {"title": "new"}),
    (b'{"description":"d2","status":"in_progress","priority":"","assigned_to":"zed"}', "invalid priority: ",
     {"description": "d2", "status": "in_progress"}),
])
def test_put_invalid_enum_keeps_partial_mutation_without_bumping_updated_at(
    client: Client, body: bytes, message: str, changed: dict,
) -> None:
    task = _full(client)
    assert_error(client.put(f"{TASKS}/{task['id']}", body), 400, V, message)
    after = client.task(task["id"])
    assert after == dict(task, **changed)
    assert after["updated_at"] == task["updated_at"]


def test_put_invalid_priority_after_completed_status_sets_completed_at(client: Client) -> None:
    task = _full(client)
    body = b'{"description":"d2","status":"completed","priority":"bad","tags":["z"]}'
    assert_error(client.put(f"{TASKS}/{task['id']}", body), 400, V, "invalid priority: bad")
    after = client.task(task["id"])
    assert after["status"] == "completed"
    assert after["description"] == "d2"
    assert after["tags"] == ["a"]
    assert after["updated_at"] == task["updated_at"]
    assert_now(after["completed_at"])


def test_put_completed_at_set_once_and_never_cleared(client: Client) -> None:
    task = _full(client)
    path = f"{TASKS}/{task['id']}"
    resp = client.put(path, b'{"status":"completed"}')
    assert_json(resp, 200, task_body(**dict(FULL, status="completed"), completed=True))
    completed_at = resp.json()["completed_at"]
    time.sleep(0.005)
    for status in ("pending", "completed", "cancelled", "completed"):
        resp = client.put(path, json.dumps({"status": status}))
        assert resp.status_code == 200
        assert resp.json()["status"] == status
        assert resp.json()["completed_at"] == completed_at
    assert_json(client.get(path), 200, task_body(**dict(FULL, status="completed"), completed=True))


def test_put_non_completed_status_does_not_set_completed_at(client: Client) -> None:
    task = _full(client)
    for status in ("in_progress", "cancelled", "on_hold", "pending"):
        resp = client.put(f"{TASKS}/{task['id']}", json.dumps({"status": status}))
        assert_json(resp, 200, task_body(**dict(FULL, status=status)))


@pytest.mark.parametrize("body", [
    b'{"title":"x"}', b'{"status":"bad"}', b'{"priority":"bad"}', b"{}",
])
def test_put_unknown_id_404(client: Client, body: bytes) -> None:
    assert_error(client.put(f"{TASKS}/nope", body), 404, "NOT_FOUND", "task with ID nope not found")


@pytest.mark.parametrize(("body", "message"), [
    (b"", "EOF"),
    (b"{", "unexpected EOF"),
    (b"abc", "invalid character 'a' looking for beginning of value"),
    (b'{"title":""}', MIN_TITLE),
    (b'{"title":"' + b"a" * 201 + b'"}', MAX_TITLE),
])
def test_put_bind_errors_take_precedence_over_404(client: Client, body: bytes, message: str) -> None:
    assert_error(client.put(f"{TASKS}/nope", body), 400, V, message)


@pytest.mark.parametrize(("body", "message"), [
    (b"", "EOF"),
    (b" ", "EOF"),
    (b"{", "unexpected EOF"),
    (b'{"title":}', "invalid character '}' looking for beginning of value"),
    (b"[]", "json: cannot unmarshal array into Go value of type models.UpdateTaskRequest"),
    (b'{"title":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.title of type string"),
    (b'{"description":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.description of type string"),
    (b'{"status":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.status of type models.TaskStatus"),
    (b'{"priority":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.priority of type models.TaskPriority"),
    (b'{"tags":"x"}', "json: cannot unmarshal string into Go struct field UpdateTaskRequest.tags of type []string"),
    (b'{"tags":[1]}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.tags of type string"),
    (b'{"assigned_to":1}', "json: cannot unmarshal number into Go struct field UpdateTaskRequest.assigned_to of type string"),
    (b'{"due_date":5}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"due_date":"x"}', f'parsing time "x" as "{LAYOUT}": cannot parse "x" as "2006"'),
])
def test_put_bind_errors_no_mutation(client: Client, body: bytes, message: str) -> None:
    task = _full(client)
    assert_error(client.put(f"{TASKS}/{task['id']}", body), 400, V, message)
    assert client.task(task["id"]) == task
