"""POST /api/v1/tasks: binding, defaults, serialization and errors."""

from __future__ import annotations

import json

import pytest

from tests.contract.helpers import (
    JSON_CT, TASKS, UUID4_RE, Client, assert_cors, assert_error, assert_json, normalize, task_body,
)

V = "VALIDATION_ERROR"
REQUIRED_TITLE = (
    "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'required' tag"
)
MAX_TITLE = "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'max' tag"
LAYOUT = "2006-01-02T15:04:05Z07:00"


def test_create_returns_201_location_and_raw_body(client: Client) -> None:
    resp = client.post(TASKS, b'{"title":"contract create"}')
    assert_json(resp, 201, task_body(title="contract create"))
    task = resp.json()
    assert UUID4_RE.match(task["id"])
    assert resp.headers["Location"] == f"{TASKS}/{task['id']}"


def test_create_defaults(client: Client) -> None:
    task = client.create(title="defaults")
    assert task["status"] == "pending"
    assert task["priority"] == "medium"
    assert task["tags"] == []
    assert task["description"] == ""
    assert task["created_by"] == "system"
    assert task["created_at"] == task["updated_at"]


def test_create_all_fields_raw_key_order(client: Client) -> None:
    body = {
        "due_date": "2030-01-02T03:04:05Z", "assigned_to": "alice", "tags": ["x", "y"],
        "priority": "critical", "description": "desc", "title": "full",
    }
    resp = client.post(TASKS, json.dumps(body))
    assert_json(resp, 201, task_body(
        title="full", description="desc", priority="critical", tags=["x", "y"],
        assigned_to="alice", due_date="2030-01-02T03:04:05Z",
    ))
    assert_json(client.get(resp.headers["Location"]), 200, task_body(
        title="full", description="desc", priority="critical", tags=["x", "y"],
        assigned_to="alice", due_date="2030-01-02T03:04:05Z",
    ))


def test_create_omits_empty_optional_fields(client: Client) -> None:
    resp = client.post(TASKS, b'{"title":"omit"}')
    raw = resp.content.decode()
    for key in ("assigned_to", "due_date", "completed_at"):
        assert f'"{key}"' not in raw


def test_create_assigned_to_empty_string_is_serialized(client: Client) -> None:
    resp = client.post(TASKS, b'{"title":"a","assigned_to":""}')
    assert_json(resp, 201, task_body(title="a", assigned_to=""))


def test_create_null_fields_behave_as_absent(client: Client) -> None:
    resp = client.post(
        TASKS,
        b'{"title":"n","description":null,"priority":null,"tags":null,"assigned_to":null,"due_date":null}',
    )
    assert_json(resp, 201, task_body(title="n"))


def test_create_empty_priority_defaults_to_medium(client: Client) -> None:
    assert_json(client.post(TASKS, b'{"title":"p","priority":""}'), 201, task_body(title="p"))


def test_create_null_tag_element_becomes_empty_string(client: Client) -> None:
    resp = client.post(TASKS, b'{"title":"t","tags":["x",null]}')
    assert_json(resp, 201, task_body(title="t", tags=["x", ""]))


@pytest.mark.parametrize(("given", "rendered"), [
    ("2024-01-02T03:04:05+05:30", "2024-01-02T03:04:05+05:30"),
    ("2024-01-02T03:04:05.123456789Z", "2024-01-02T03:04:05.123456789Z"),
    ("2024-01-02T03:04:05.120Z", "2024-01-02T03:04:05.12Z"),
    ("2024-01-02T03:04:05.000Z", "2024-01-02T03:04:05Z"),
    ("2024-01-02T03:04:05+00:00", "2024-01-02T03:04:05Z"),
    ("2024-01-02T03:04:05-00:00", "2024-01-02T03:04:05Z"),
    ("2024-01-02T03:04:05.1234567891Z", "2024-01-02T03:04:05.123456789Z"),
    ("2024-01-02T03:04:05.000000001-07:00", "2024-01-02T03:04:05.000000001-07:00"),
    ("2031-05-06T07:08:09.5+01:00", "2031-05-06T07:08:09.5+01:00"),
    ("2024-02-29T00:00:00Z", "2024-02-29T00:00:00Z"),
    ("9999-12-31T23:59:59Z", "9999-12-31T23:59:59Z"),
])
def test_create_due_date_round_trip(client: Client, given: str, rendered: str) -> None:
    resp = client.post(TASKS, json.dumps({"title": "dd", "due_date": given}))
    assert_json(resp, 201, task_body(title="dd", due_date=rendered))
    assert_json(client.get(resp.headers["Location"]), 200, task_body(title="dd", due_date=rendered))


def test_create_html_characters_escaped_in_raw_body(client: Client) -> None:
    resp = client.post(TASKS, '{"title":"<b>&</b>","description":"a\\u2028b","tags":["<t>"]}')
    assert resp.status_code == 201
    raw = resp.content.decode()
    assert '"title":"\\u003cb\\u003e\\u0026\\u003c/b\\u003e"' in raw
    assert '"description":"a\\u2028b"' in raw
    assert '"tags":["\\u003ct\\u003e"]' in raw
    assert "<" not in raw and ">" not in raw and "&" not in raw


def test_create_unicode_not_escaped(client: Client) -> None:
    resp = client.post(TASKS, '{"title":"é✓😀 日本"}'.encode())
    assert resp.status_code == 201
    assert '"title":"é✓😀 日本"'.encode() in resp.content
    assert_json(resp, 201, task_body(title="é✓😀 日本"))


def test_create_control_characters_escaping(client: Client) -> None:
    resp = client.post(TASKS, b'{"title":"c","description":"line\\nbreak\\t\\"q\\" \\\\ \\u0001 /"}')
    assert resp.status_code == 201
    assert b'"description":"line\\nbreak\\t\\"q\\" \\\\ \\u0001 /"' in resp.content


@pytest.mark.parametrize("body", [
    b'{"TITLE":"ci","PRIORITY":"high","TAGS":["a"],"DESCRIPTION":"d","ASSIGNED_TO":"u","DUE_DATE":"2030-01-01T00:00:00Z"}',
    b'{"Title":"ci","Priority":"high","Tags":["a"],"Description":"d","Assigned_To":"u","Due_Date":"2030-01-01T00:00:00Z"}',
    b'{"tItLe":"ci","pRiOrItY":"high","tags":["a"],"description":"d","assigned_TO":"u","due_DATE":"2030-01-01T00:00:00Z"}',
])
def test_create_keys_match_case_insensitively(client: Client, body: bytes) -> None:
    assert_json(client.post(TASKS, body), 201, task_body(
        title="ci", description="d", priority="high", tags=["a"], assigned_to="u",
        due_date="2030-01-01T00:00:00Z",
    ))


@pytest.mark.parametrize(("body", "title"), [
    (b'{"title":"first","TITLE":"last"}', "last"),
    (b'{"TITLE":"first","title":"last"}', "last"),
])
def test_create_duplicate_keys_last_wins(client: Client, body: bytes, title: str) -> None:
    assert_json(client.post(TASKS, body), 201, task_body(title=title))


def test_create_unknown_keys_ignored(client: Client) -> None:
    body = b'{"title":"u","id":"x","status":"completed","created_by":"me","extra":{"z":[1,2,{"q":null}]}}'
    assert_json(client.post(TASKS, body), 201, task_body(title="u"))


@pytest.mark.parametrize("content_type", [
    None, "text/plain", "application/x-www-form-urlencoded", "application/json",
])
def test_create_parses_json_regardless_of_content_type(client: Client, content_type: str | None) -> None:
    headers = {"Content-Type": content_type} if content_type else None
    assert_json(client.post(TASKS, b'{"title":"ct"}', headers=headers), 201, task_body(title="ct"))


@pytest.mark.parametrize("body", [
    b'{"title":"a"} x', b'{"title":"a"}{', b'{"title":"a"}  ', b'\n{"title":"a"}\n', b'{"title":"a"}]',
])
def test_create_ignores_data_after_first_json_value(client: Client, body: bytes) -> None:
    assert_json(client.post(TASKS, body), 201, task_body(title="a"))


@pytest.mark.parametrize("title", ["a" * 200, "é" * 200, "😀" * 200, "日" * 199 + "x"])
def test_create_title_up_to_200_runes_accepted(client: Client, title: str) -> None:
    resp = client.post(TASKS, json.dumps({"title": title}, ensure_ascii=False).encode())
    assert_json(resp, 201, task_body(title=title))


@pytest.mark.parametrize("title", ["a" * 201, "é" * 201, "😀" * 201])
def test_create_title_over_200_runes_rejected(client: Client, title: str) -> None:
    resp = client.post(TASKS, json.dumps({"title": title}, ensure_ascii=False).encode())
    assert_error(resp, 400, V, MAX_TITLE)


@pytest.mark.parametrize(("body", "message"), [
    # body / syntax
    (b"", "EOF"),
    (b" ", "EOF"),
    (b"\n\t ", "EOF"),
    (b"{", "unexpected EOF"),
    (b'{"title":"a"', "unexpected EOF"),
    (b'{"title":1,', "unexpected EOF"),
    (b'{"title":}', "invalid character '}' looking for beginning of value"),
    (b"abc", "invalid character 'a' looking for beginning of value"),
    (b"title=x", "invalid character 'i' in literal true (expecting 'r')"),
    (b'{"title":"a",}', "invalid character '}' looking for beginning of object key string"),
    (b'{"title":"a" "b"}', "invalid character '\"' after object key:value pair"),
    (b'{"due_date":"bad","title":1,"x":}', "invalid character '}' looking for beginning of value"),
    # top-level value of wrong kind
    (b"null", REQUIRED_TITLE),
    (b"[]", "json: cannot unmarshal array into Go value of type models.CreateTaskRequest"),
    (b'"x"', "json: cannot unmarshal string into Go value of type models.CreateTaskRequest"),
    (b"1", "json: cannot unmarshal number into Go value of type models.CreateTaskRequest"),
    # field type errors
    (b'{"title":1}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":1.5}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":true}', "json: cannot unmarshal bool into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":{}}', "json: cannot unmarshal object into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":[]}', "json: cannot unmarshal array into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":"a","description":1}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.description of type string"),
    (b'{"tags":"x"}', "json: cannot unmarshal string into Go struct field CreateTaskRequest.tags of type []string"),
    (b'{"title":"a","tags":{}}', "json: cannot unmarshal object into Go struct field CreateTaskRequest.tags of type []string"),
    (b'{"title":"a","tags":[1]}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.tags of type string"),
    (b'{"title":"a","priority":5}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.priority of type models.TaskPriority"),
    (b'{"title":"a","assigned_to":true}', "json: cannot unmarshal bool into Go struct field CreateTaskRequest.assigned_to of type string"),
    (b'{"title":"a","assigned_to":["x"]}', "json: cannot unmarshal array into Go struct field CreateTaskRequest.assigned_to of type string"),
    # first type error wins (document order), keeps decoding
    (b'{"title":1,"tags":"x"}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"),
    (b'{"tags":"x","title":1}', "json: cannot unmarshal string into Go struct field CreateTaskRequest.tags of type []string"),
    (b'{"title":"a","priority":5,"tags":[1]}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.priority of type models.TaskPriority"),
    (b'{"title":1,"title":"ok"}', "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"),
    (b'{"title":1} x', "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"),
    # due_date (time.Time UnmarshalJSON) errors abort immediately and win over type errors
    (b'{"title":"a","due_date":5}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"title":"a","due_date":true}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"title":"a","due_date":{}}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"title":"a","due_date":[]}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"due_date":5,"title":1}', "Time.UnmarshalJSON: input is not a JSON string"),
    (b'{"title":1,"due_date":"bad"}', f'parsing time "bad" as "{LAYOUT}": cannot parse "bad" as "2006"'),
    (b'{"title":"a","due_date":"2024-01-01T00:00:00Z","due_date":5}', "Time.UnmarshalJSON: input is not a JSON string"),
    # validation
    (b"{}", REQUIRED_TITLE),
    (b'{"title":""}', REQUIRED_TITLE),
    (b'{"title":null}', REQUIRED_TITLE),
    (b'{"title":"a","title":""}', REQUIRED_TITLE),
    (b'{"description":"only"}', REQUIRED_TITLE),
    (b'{"title":"a","priority":"urgent"}', "invalid priority: urgent"),
    (b'{"title":"a","priority":"HIGH"}', "invalid priority: HIGH"),
    (b'{"title":"a","priority":"<x>"}', "invalid priority: <x>"),
])
def test_create_errors(client: Client, body: bytes, message: str) -> None:
    assert_error(client.post(TASKS, body), 400, V, message)


@pytest.mark.parametrize(("due", "message"), [
    ("bad", f'parsing time "bad" as "{LAYOUT}": cannot parse "bad" as "2006"'),
    ("", f'parsing time "" as "{LAYOUT}": cannot parse "" as "2006"'),
    ("2024-01-01", f'parsing time "2024-01-01" as "{LAYOUT}": cannot parse "" as "T"'),
    ("2024-01-01T00:00:00", f'parsing time "2024-01-01T00:00:00" as "{LAYOUT}": cannot parse "" as "Z07:00"'),
    ("2024-01-02 03:04:05Z", f'parsing time "2024-01-02 03:04:05Z" as "{LAYOUT}": cannot parse " 03:04:05Z" as "T"'),
    ("2024-01-02t03:04:05z", f'parsing time "2024-01-02t03:04:05z" as "{LAYOUT}": cannot parse "t03:04:05z" as "T"'),
    ("2024-1-02T03:04:05Z", f'parsing time "2024-1-02T03:04:05Z" as "{LAYOUT}": cannot parse "1-02T03:04:05Z" as "01"'),
    ("2024-01-02T03:04Z", f'parsing time "2024-01-02T03:04Z" as "{LAYOUT}": cannot parse "Z" as ":"'),
    ("2024-01-02T03:04:05+5:30", f'parsing time "2024-01-02T03:04:05+5:30" as "{LAYOUT}": cannot parse "+5:30" as "Z07:00"'),
    ("2024-01-02T03:04:05+0530", f'parsing time "2024-01-02T03:04:05+0530" as "{LAYOUT}": cannot parse "+0530" as "Z07:00"'),
    ("2024-01-02T03:04:05.Z", f'parsing time "2024-01-02T03:04:05.Z" as "{LAYOUT}": cannot parse ".Z" as "Z07:00"'),
    (" 2024-01-02T03:04:05Z", f'parsing time " 2024-01-02T03:04:05Z" as "{LAYOUT}": cannot parse " 2024-01-02T03:04:05Z" as "2006"'),
    ("2024-13-01T00:00:00Z", 'parsing time "2024-13-01T00:00:00Z": month out of range'),
    ("2024-02-30T03:04:05Z", 'parsing time "2024-02-30T03:04:05Z": day out of range'),
    ("2023-02-29T00:00:00Z", 'parsing time "2023-02-29T00:00:00Z": day out of range'),
    ("2024-01-02T25:04:05Z", 'parsing time "2024-01-02T25:04:05Z": hour out of range'),
    ("2024-01-02T03:60:05Z", 'parsing time "2024-01-02T03:60:05Z": minute out of range'),
    ("2024-01-02T03:04:60Z", 'parsing time "2024-01-02T03:04:60Z": second out of range'),
    ("2024-01-02T03:04:05Zjunk", 'parsing time "2024-01-02T03:04:05Zjunk": extra text: "junk"'),
    ("2024-01-02T03:04:05+05:30junk", 'parsing time "2024-01-02T03:04:05+05:30junk": extra text: "junk"'),
])
def test_create_due_date_parse_errors(client: Client, due: str, message: str) -> None:
    resp = client.post(TASKS, json.dumps({"title": "a", "due_date": due}))
    assert_error(resp, 400, V, message)


def test_create_error_response_headers(client: Client) -> None:
    resp = client.post(TASKS, b"")
    assert resp.headers["Content-Type"] == JSON_CT
    assert "Location" not in resp.headers
    assert_cors(resp)
