"""POST /api/v1/tasks (``createTask``): binding, validation, defaults, serialization."""

from __future__ import annotations

import json

import pytest
from flask.testing import FlaskClient

from tests.api.helpers import (
    RFC3339NANO_RE,
    TASKS,
    UUID4_RE,
    FakeClock,
    assert_json,
    assert_validation,
    create,
    get,
    list_json,
    task_json,
)

TITLE_REQUIRED = "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'required' tag"
TITLE_MAX = "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'max' tag"


def post(client: FlaskClient, data: str | bytes, **kwargs):  # type: ignore[no-untyped-def]
    return client.post(TASKS, data=data, **kwargs)


# -- success ----------------------------------------------------------------------


def test_create_minimal_exact_bytes_and_location(client: FlaskClient, clock: FakeClock) -> None:
    resp = post(client, '{"title":"Write tests"}')
    task = assert_json(resp, 201)
    assert UUID4_RE.match(task["id"])
    assert resp.headers["Location"] == f"/api/v1/tasks/{task['id']}"
    # Seeds took T0 and T0+1s; one now() is shared by created_at and updated_at.
    assert resp.data.decode() == task_json(id=task["id"], title="Write tests", created_at=clock.stamp(2))
    assert resp.data.decode() == (
        '{"id":"' + task["id"] + '","title":"Write tests","description":"","status":"pending",'
        '"priority":"medium","tags":[],"created_by":"system",'
        '"created_at":"2026-01-02T03:04:07.123456789Z","updated_at":"2026-01-02T03:04:07.123456789Z"}'
    )


def test_create_full_body_key_order(client: FlaskClient, clock: FakeClock) -> None:
    resp = post(client, json.dumps({
        "due_date": "2024-06-01T12:00:00+05:30", "assigned_to": "alice", "tags": ["a", "b"],
        "priority": "critical", "description": "desc", "title": "Full",
    }))
    task = assert_json(resp, 201)
    assert resp.data.decode() == task_json(
        id=task["id"], title="Full", description="desc", priority="critical", tags=["a", "b"],
        assigned_to="alice", created_at=clock.stamp(2), due_date="2024-06-01T12:00:00+05:30",
    )


def test_created_task_is_retrievable_and_listed(client: FlaskClient) -> None:
    task = create(client, {"title": "Persisted"})
    assert get(client, task["id"]) == task
    body = list_json(client)
    assert body["total_count"] == 3
    assert task in body["tasks"]


def test_create_timestamps_are_now_rfc3339nano(client: FlaskClient) -> None:
    task = create(client, {"title": "t"})
    assert RFC3339NANO_RE.match(task["created_at"])
    assert task["created_at"] == task["updated_at"]


def test_create_ignores_server_owned_and_unknown_fields(client: FlaskClient) -> None:
    task = create(client, {
        "title": "x", "id": "mine", "status": "completed", "created_by": "me",
        "created_at": "2020-01-01T00:00:00Z", "completed_at": "2020-01-01T00:00:00Z", "extra": 1,
    })
    assert task["id"] != "mine" and UUID4_RE.match(task["id"])
    assert task["status"] == "pending"
    assert task["created_by"] == "system"
    assert "completed_at" not in task
    assert task["created_at"] != "2020-01-01T00:00:00Z"


@pytest.mark.parametrize("priority", ["low", "medium", "high", "critical"])
def test_create_each_valid_priority(client: FlaskClient, priority: str) -> None:
    assert create(client, {"title": "p", "priority": priority})["priority"] == priority


@pytest.mark.parametrize("raw", ['"priority":""', '"priority":null', '"x":1'])
def test_create_priority_defaults_to_medium(client: FlaskClient, raw: str) -> None:
    assert create(client, '{"title":"p",' + raw + "}")["priority"] == "medium"


@pytest.mark.parametrize("priority", ["HIGH", "urgent", " low", "Medium"])
def test_create_invalid_priority_not_stored(client: FlaskClient, priority: str) -> None:
    assert_validation(post(client, json.dumps({"title": "p", "priority": priority})),
                      f"invalid priority: {priority}")
    assert list_json(client)["total_count"] == 2


@pytest.mark.parametrize("raw", ['"tags":null', '"tags":[]', '"x":0'])
def test_create_tags_default_to_empty_array(client: FlaskClient, raw: str) -> None:
    resp = post(client, '{"title":"t",' + raw + "}")
    assert assert_json(resp, 201)["tags"] == []
    assert b'"tags":[]' in resp.data


def test_create_null_tag_element_becomes_empty_string(client: FlaskClient) -> None:
    assert create(client, '{"title":"t","tags":["x",null]}')["tags"] == ["x", ""]


def test_create_null_fields_are_absent(client: FlaskClient) -> None:
    task = create(client, '{"title":"t","assigned_to":null,"due_date":null,"description":null}')
    assert "assigned_to" not in task and "due_date" not in task
    assert task["description"] == ""


def test_create_empty_assigned_to_is_kept(client: FlaskClient) -> None:
    resp = post(client, '{"title":"t","assigned_to":""}')
    assert assert_json(resp, 201)["assigned_to"] == ""
    assert b'"assigned_to":"",' in resp.data


def test_create_keys_case_insensitive_last_wins(client: FlaskClient) -> None:
    task = create(client, '{"TITLE":"first","Title":"second","Assigned_To":"bob"}')
    assert task["title"] == "second"
    assert task["assigned_to"] == "bob"


def test_create_content_type_is_ignored(client: FlaskClient) -> None:
    for ct in ("text/plain", "application/x-www-form-urlencoded", None):
        headers = {"Content-Type": ct} if ct else {}
        assert_json(post(client, '{"title":"ct"}', headers=headers), 201)


@pytest.mark.parametrize("trailer", [" x", "{", "]", '{"title":1}'])
def test_create_only_first_json_value_is_decoded(client: FlaskClient, trailer: str) -> None:
    assert create(client, '{"title":"a"}' + trailer)["title"] == "a"


def test_create_trailing_garbage_after_type_error(client: FlaskClient) -> None:
    assert_validation(post(client, '{"title":1} x'),
                      "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string")


# -- serialization ------------------------------------------------------------------


def test_create_html_and_control_chars_escaped_like_go(client: FlaskClient) -> None:
    title = "<b>&\"\\/\n\t\x01  é😀"
    resp = post(client, json.dumps({"title": title}))
    assert assert_json(resp, 201)["title"] == title
    assert (
        b'"title":"\\u003cb\\u003e\\u0026\\"\\\\/\\n\\t\\u0001\\u2028\\u2029'
        + "é😀".encode() + b'"'
    ) in resp.data


def test_create_invalid_utf8_and_lone_surrogate_become_replacement(client: FlaskClient) -> None:
    assert create(client, b'{"title":"a\xffb"}')["title"] == "a�b"
    assert create(client, b'{"title":"a\\ud800"}')["title"] == "a�"


@pytest.mark.parametrize(
    ("given", "rendered"),
    [
        ("2024-01-02T03:04:05Z", "2024-01-02T03:04:05Z"),
        ("2024-01-02T03:04:05.120Z", "2024-01-02T03:04:05.12Z"),
        ("2024-01-02T03:04:05.000Z", "2024-01-02T03:04:05Z"),
        ("2024-01-02T03:04:05+00:00", "2024-01-02T03:04:05Z"),
        ("2024-01-02T03:04:05-00:00", "2024-01-02T03:04:05Z"),
        ("2024-01-02T03:04:05+05:30", "2024-01-02T03:04:05+05:30"),
        ("2024-01-02T03:04:05.5-07:00", "2024-01-02T03:04:05.5-07:00"),
        ("2024-01-02T03:04:05.1234567891Z", "2024-01-02T03:04:05.123456789Z"),
        ("0000-01-01T00:00:00Z", "0000-01-01T00:00:00Z"),
        ("9999-12-31T23:59:59Z", "9999-12-31T23:59:59Z"),
    ],
)
def test_create_due_date_round_trip(client: FlaskClient, given: str, rendered: str) -> None:
    resp = post(client, json.dumps({"title": "d", "due_date": given}))
    assert assert_json(resp, 201)["due_date"] == rendered
    assert get(client, resp.json["id"])["due_date"] == rendered


def test_create_unmarshalable_due_date_offset_gives_empty_201(client: FlaskClient) -> None:
    """Go quirk: ``+24:00`` parses but cannot be marshalled; the task is stored anyway."""
    resp = post(client, '{"title":"a","due_date":"2024-01-01T00:00:00+24:00"}')
    assert resp.status_code == 201
    assert resp.data == b""
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"
    assert resp.headers["Content-Length"] == "0"
    assert resp.headers["Location"].startswith("/api/v1/tasks/")
    task_id = resp.headers["Location"].rsplit("/", 1)[1]
    assert UUID4_RE.match(task_id)
    stored = client.get(f"{TASKS}/{task_id}")
    assert stored.status_code == 200 and stored.data == b""


# -- validation (binding tags) ---------------------------------------------------


@pytest.mark.parametrize("body", ["{}", '{"title":""}', '{"title":null}', "null", '{"description":"d"}'])
def test_create_title_required(client: FlaskClient, body: str) -> None:
    assert_validation(post(client, body), TITLE_REQUIRED)


@pytest.mark.parametrize("ch", ["a", "é", "😀"])
def test_create_title_max_counts_runes(client: FlaskClient, ch: str) -> None:
    assert create(client, {"title": ch * 200})["title"] == ch * 200
    assert_validation(post(client, json.dumps({"title": ch * 201})), TITLE_MAX)


def test_create_whitespace_title_is_accepted(client: FlaskClient) -> None:
    assert create(client, {"title": " "})["title"] == " "


def test_create_validation_runs_before_priority_check(client: FlaskClient) -> None:
    assert_validation(post(client, '{"priority":"bogus"}'), TITLE_REQUIRED)


# -- binding errors -------------------------------------------------------------


@pytest.mark.parametrize("body", [b"", b" ", b"\n\t \r"])
def test_create_empty_body_is_eof(client: FlaskClient, body: bytes) -> None:
    assert_validation(post(client, body), "EOF")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{", "unexpected EOF"),
        (b'{"title":"a"', "unexpected EOF"),
        (b'{"title":1,', "unexpected EOF"),
        (b'{"title":}', "invalid character '}' looking for beginning of value"),
        (b"abc", "invalid character 'a' looking for beginning of value"),
        (b"title=x", "invalid character 'i' in literal true (expecting 'r')"),
        (b'{"title":"a",}', "invalid character '}' looking for beginning of object key string"),
        (b'{"title":"a" "b"}', "invalid character '\"' after object key:value pair"),
        (b"\xff", "invalid character 'ÿ' looking for beginning of value"),
        (b'{"due_date":"bad","title":1,"x":}', "invalid character '}' looking for beginning of value"),
    ],
)
def test_create_syntax_errors(client: FlaskClient, body: bytes, message: str) -> None:
    assert_validation(post(client, body), message)


@pytest.mark.parametrize(
    ("body", "kind"),
    [("[]", "array"), ('"x"', "string"), ("1", "number"), ("true", "bool")],
)
def test_create_non_object_top_level(client: FlaskClient, body: str, kind: str) -> None:
    assert_validation(post(client, body),
                      f"json: cannot unmarshal {kind} into Go value of type models.CreateTaskRequest")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"title":1}', "number into Go struct field CreateTaskRequest.title of type string"),
        ('{"title":true}', "bool into Go struct field CreateTaskRequest.title of type string"),
        ('{"title":[]}', "array into Go struct field CreateTaskRequest.title of type string"),
        ('{"title":{}}', "object into Go struct field CreateTaskRequest.title of type string"),
        ('{"title":"t","description":5}', "number into Go struct field CreateTaskRequest.description of type string"),
        ('{"title":"t","tags":"x"}', "string into Go struct field CreateTaskRequest.tags of type []string"),
        ('{"title":"t","tags":[1]}', "number into Go struct field CreateTaskRequest.tags of type string"),
        ('{"title":"t","priority":1}', "number into Go struct field CreateTaskRequest.priority of type models.TaskPriority"),
        ('{"title":"t","assigned_to":false}', "bool into Go struct field CreateTaskRequest.assigned_to of type string"),
        ('{"tags":"x","title":1}', "string into Go struct field CreateTaskRequest.tags of type []string"),
        ('{"title":1,"title":"ok"}', "number into Go struct field CreateTaskRequest.title of type string"),
    ],
)
def test_create_type_errors_first_in_document_order(client: FlaskClient, body: str, message: str) -> None:
    assert_validation(post(client, body), "json: cannot unmarshal " + message)


def test_create_type_error_field_uses_json_tag_name(client: FlaskClient) -> None:
    assert_validation(post(client, '{"title":"t","ASSIGNED_TO":1}'),
                      "json: cannot unmarshal number into Go struct field CreateTaskRequest.assigned_to of type string")


@pytest.mark.parametrize("raw", ["5", "true", "{}", "[]"])
def test_create_due_date_not_a_string(client: FlaskClient, raw: str) -> None:
    assert_validation(post(client, '{"title":"t","due_date":' + raw + "}"),
                      "Time.UnmarshalJSON: input is not a JSON string")


L = "2006-01-02T15:04:05Z07:00"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("bad", f'parsing time "bad" as "{L}": cannot parse "bad" as "2006"'),
        ("", f'parsing time "" as "{L}": cannot parse "" as "2006"'),
        ("2024-01-01", f'parsing time "2024-01-01" as "{L}": cannot parse "" as "T"'),
        ("2024-01-01T00:00:00", f'parsing time "2024-01-01T00:00:00" as "{L}": cannot parse "" as "Z07:00"'),
        ("2024-01-02 03:04:05Z", f'parsing time "2024-01-02 03:04:05Z" as "{L}": cannot parse " 03:04:05Z" as "T"'),
        ("2024-1-02T03:04:05Z", f'parsing time "2024-1-02T03:04:05Z" as "{L}": cannot parse "1-02T03:04:05Z" as "01"'),
        ("2024-01-02T03:04Z", f'parsing time "2024-01-02T03:04Z" as "{L}": cannot parse "Z" as ":"'),
        ("2024-01-02T03:04:05+0530", f'parsing time "2024-01-02T03:04:05+0530" as "{L}": cannot parse "+0530" as "Z07:00"'),
        ("2024-13-01T00:00:00Z", 'parsing time "2024-13-01T00:00:00Z": month out of range'),
        ("2023-02-29T00:00:00Z", 'parsing time "2023-02-29T00:00:00Z": day out of range'),
        ("2024-01-01T24:00:00Z", 'parsing time "2024-01-01T24:00:00Z": hour out of range'),
        ("2024-01-01T00:60:00Z", 'parsing time "2024-01-01T00:60:00Z": minute out of range'),
        ("2024-01-01T00:00:60Z", 'parsing time "2024-01-01T00:00:60Z": second out of range'),
        ("2024-01-02T03:04:05Zjunk", 'parsing time "2024-01-02T03:04:05Zjunk": extra text: "junk"'),
    ],
)
def test_create_due_date_parse_errors(client: FlaskClient, value: str, message: str) -> None:
    assert_validation(post(client, json.dumps({"title": "t", "due_date": value})), message)


def test_create_due_date_error_overrides_earlier_type_error(client: FlaskClient) -> None:
    assert_validation(post(client, '{"title":1,"due_date":"bad"}'),
                      f'parsing time "bad" as "{L}": cannot parse "bad" as "2006"')


def test_create_duplicate_due_date_each_value_decoded(client: FlaskClient) -> None:
    """Every occurrence is decoded: the last good one is kept, any bad one aborts at once."""
    task = create(client, '{"title":"t","due_date":"2024-01-01T00:00:00Z","due_date":"2025-01-01T00:00:00Z"}')
    assert task["due_date"] == "2025-01-01T00:00:00Z"
    assert_validation(post(client, '{"title":"t","due_date":"2024-01-01T00:00:00Z","due_date":5}'),
                      "Time.UnmarshalJSON: input is not a JSON string")
    assert_validation(post(client, '{"title":"t","due_date":"bad","due_date":"2024-01-01T00:00:00Z"}'),
                      f'parsing time "bad" as "{L}": cannot parse "bad" as "2006"')


def test_create_binding_error_does_not_store(client: FlaskClient) -> None:
    for body in ("", "{", "[]", '{"title":1}', "{}"):
        post(client, body)
    assert list_json(client)["total_count"] == 2
