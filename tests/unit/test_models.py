"""Unit tests for task_api.models (port of internal/models/task.go)."""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from task_api.go_compat import GoError, GoNumError, GoTime, GoValidationError, go_dumps
from task_api.models import (
    CreateTaskRequest,
    ErrorDetail,
    ErrorResponse,
    ListTasksQuery,
    ListTasksResponse,
    SortOrder,
    Task,
    TaskPriority,
    TaskStatus,
    UpdateTaskRequest,
    UpdateTaskStatusRequest,
)

T0 = GoTime.from_parts(2026, 10, 9, 5, 7, 9, 188_476_000)
DUE = GoTime.from_parts(2024, 1, 2, 3, 4, 5, 120_000_000, offset=5 * 3600 + 1800)


def msg(struct: str, field: str, tag: str) -> str:
    return f"Key: '{struct}.{field}' Error:Field validation for '{field}' failed on the '{tag}' tag"


def make_task(**overrides: object) -> Task:
    values: dict[str, object] = dict(
        id="abc",
        title="t",
        description="d",
        status="pending",
        priority="high",
        tags=["go"],
        created_by="system",
        assigned_to=None,
        created_at=T0,
        updated_at=T0,
    )
    values.update(overrides)
    return Task(**values)  # type: ignore[arg-type]


# -- enums ------------------------------------------------------------------


def test_status_values_and_validity() -> None:
    assert [s.value for s in TaskStatus] == [
        "pending",
        "in_progress",
        "completed",
        "cancelled",
        "on_hold",
    ]
    for s in TaskStatus:
        assert TaskStatus.is_valid(s.value)
    for bad in ("", "PENDING", "done", " pending"):
        assert not TaskStatus.is_valid(bad)


def test_priority_values_and_validity() -> None:
    assert [p.value for p in TaskPriority] == ["low", "medium", "high", "critical"]
    assert [TaskPriority.get_value(p) for p in ("low", "medium", "high", "critical")] == [
        1,
        2,
        3,
        4,
    ]
    for unknown in ("", "HIGH", "urgent"):
        assert TaskPriority.get_value(unknown) == 2
        assert not TaskPriority.is_valid(unknown)
    assert TaskPriority.is_valid("critical")


def test_sort_order_constants() -> None:
    assert [s.value for s in SortOrder] == [
        "created_at_asc",
        "created_at_desc",
        "due_date_asc",
        "due_date_desc",
        "priority_asc",
        "priority_desc",
    ]


# -- Task serialization -------------------------------------------------------


def test_task_key_order_minimal() -> None:
    d = make_task().to_go()
    assert list(d) == [
        "id",
        "title",
        "description",
        "status",
        "priority",
        "tags",
        "created_by",
        "created_at",
        "updated_at",
    ]


def test_task_key_order_full() -> None:
    d = make_task(assigned_to="bob", due_date=DUE, completed_at=T0).to_go()
    assert list(d) == [
        "id",
        "title",
        "description",
        "status",
        "priority",
        "tags",
        "created_by",
        "assigned_to",
        "created_at",
        "updated_at",
        "due_date",
        "completed_at",
    ]


def test_task_go_dumps_exact() -> None:
    task = make_task(title="<a&b>", tags=[], assigned_to="", due_date=DUE)
    assert go_dumps(task) == (
        '{"id":"abc","title":"\\u003ca\\u0026b\\u003e","description":"d","status":"pending",'
        '"priority":"high","tags":[],"created_by":"system","assigned_to":"",'
        '"created_at":"2026-10-09T05:07:09.188476Z","updated_at":"2026-10-09T05:07:09.188476Z",'
        '"due_date":"2024-01-02T03:04:05.12+05:30"}'
    )


def test_task_empty_assignee_serialized_none_omitted() -> None:
    assert make_task(assigned_to="").to_go()["assigned_to"] == ""
    assert "assigned_to" not in make_task(assigned_to=None).to_go()


def test_task_nil_tags_is_null() -> None:
    assert '"tags":null' in go_dumps(make_task(tags=None))


def test_task_enum_members_serialize_as_strings() -> None:
    d = make_task(status=TaskStatus.COMPLETED, priority=TaskPriority.LOW).to_go()
    assert type(d["status"]) is str and d["status"] == "completed"
    assert type(d["priority"]) is str and d["priority"] == "low"


# -- request DTOs -------------------------------------------------------------


def test_create_from_bound_defaults() -> None:
    req = CreateTaskRequest.from_bound({"title": "x"})
    assert req == CreateTaskRequest(title="x")
    assert req.priority == "" and req.tags is None and req.assigned_to is None
    assert req.due_date is None and req.description == ""


def test_create_from_bound_all_fields() -> None:
    req = CreateTaskRequest.from_bound(
        {
            "title": "x",
            "description": "d",
            "priority": "low",
            "tags": ["a", ""],
            "assigned_to": "",
            "due_date": DUE,
        }
    )
    assert req.tags == ["a", ""] and req.assigned_to == "" and req.due_date is DUE


def test_create_title_required() -> None:
    with pytest.raises(GoValidationError) as exc:
        CreateTaskRequest().validate()
    assert str(exc.value) == msg("CreateTaskRequest", "Title", "required")
    assert exc.value.errors == [("CreateTaskRequest", "Title", "required")]


@pytest.mark.parametrize("ch", ["a", "é", "😀"])
def test_create_title_max_counts_runes(ch: str) -> None:
    CreateTaskRequest(title=ch * 200).validate()
    with pytest.raises(GoValidationError) as exc:
        CreateTaskRequest(title=ch * 201).validate()
    assert str(exc.value) == msg("CreateTaskRequest", "Title", "max")


def test_create_validation_ignores_priority() -> None:
    # Invalid priority is a service error, not a binding failure.
    assert CreateTaskRequest(title="x", priority="HIGH").validation_errors() == []


def test_update_from_bound_all_absent() -> None:
    req = UpdateTaskRequest.from_bound({})
    assert all(
        getattr(req, f) is None
        for f in ("title", "description", "status", "priority", "tags", "assigned_to", "due_date")
    )
    req.validate()


def test_update_title_rules() -> None:
    UpdateTaskRequest(title=None).validate()
    UpdateTaskRequest(title="x").validate()
    UpdateTaskRequest(title="é" * 200).validate()
    with pytest.raises(GoValidationError) as exc:
        UpdateTaskRequest(title="").validate()
    assert str(exc.value) == msg("UpdateTaskRequest", "Title", "min")
    with pytest.raises(GoValidationError) as exc:
        UpdateTaskRequest(title="x" * 201).validate()
    assert str(exc.value) == msg("UpdateTaskRequest", "Title", "max")


def test_update_does_not_validate_enums() -> None:
    assert UpdateTaskRequest(status="", priority="bogus").validation_errors() == []


def test_status_request_required() -> None:
    with pytest.raises(GoValidationError) as exc:
        UpdateTaskStatusRequest.from_bound({}).validate()
    assert str(exc.value) == msg("UpdateTaskStatusRequest", "Status", "required")
    # Invalid values pass binding; the handler checks IsValid.
    UpdateTaskStatusRequest(status="bogus").validate()


def test_bind_schemas() -> None:
    def schema(cls: type) -> list[tuple[str, str, str]]:
        return [(f.json_name, f.kind, f.go_type) for f in cls.BIND_FIELDS]  # type: ignore[attr-defined]

    def values(cls: type) -> list[str]:
        return [f.json_name for f in cls.BIND_FIELDS if not f.pointer]  # type: ignore[attr-defined]

    assert values(CreateTaskRequest) == ["title", "description", "priority"]
    assert values(UpdateTaskRequest) == []
    assert values(UpdateTaskStatusRequest) == ["status"]
    assert CreateTaskRequest.GO_STRUCT == "CreateTaskRequest"
    assert schema(CreateTaskRequest) == [
        ("title", "string", "string"),
        ("description", "string", "string"),
        ("priority", "string", "models.TaskPriority"),
        ("tags", "strings", "[]string"),
        ("assigned_to", "string", "string"),
        ("due_date", "time", "time.Time"),
    ]
    assert UpdateTaskRequest.GO_STRUCT == "UpdateTaskRequest"
    assert schema(UpdateTaskRequest) == [
        ("title", "string", "string"),
        ("description", "string", "string"),
        ("status", "string", "models.TaskStatus"),
        ("priority", "string", "models.TaskPriority"),
        ("tags", "strings", "[]string"),
        ("assigned_to", "string", "string"),
        ("due_date", "time", "time.Time"),
    ]
    assert UpdateTaskStatusRequest.GO_STRUCT == "UpdateTaskStatusRequest"
    assert schema(UpdateTaskStatusRequest) == [("status", "string", "models.TaskStatus")]


# -- ShouldBindJSON end-to-end (bind_json + validation); strings from the Go oracle ----


def test_create_from_json() -> None:
    req = CreateTaskRequest.from_json(
        b'{"TITLE":"x","tags":["a",null],"assigned_to":"","due_date":"2024-01-02T03:04:05.120+05:30"}'
    )
    assert req.title == "x" and req.tags == ["a", ""] and req.assigned_to == ""
    assert req.due_date is not None and str(req.due_date) == "2024-01-02T03:04:05.12+05:30"
    assert req.priority == "" and req.description == ""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "EOF"),
        (b"null", msg("CreateTaskRequest", "Title", "required")),
        (b'{"title":null}', msg("CreateTaskRequest", "Title", "required")),
        (b'{"title":""}', msg("CreateTaskRequest", "Title", "required")),
        ("{\"title\":\"" .encode() + "é".encode() * 201 + b'"}', msg("CreateTaskRequest", "Title", "max")),
        (
            b'{"title":1}',
            "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string",
        ),
        (
            b'{"title":"a","priority":5}',
            "json: cannot unmarshal number into Go struct field CreateTaskRequest.priority"
            " of type models.TaskPriority",
        ),
        (
            b'{"tags":"x","title":1}',
            "json: cannot unmarshal string into Go struct field CreateTaskRequest.tags of type []string",
        ),
        (b"[]", "json: cannot unmarshal array into Go value of type models.CreateTaskRequest"),
    ],
)
def test_create_from_json_errors(body: bytes, message: str) -> None:
    with pytest.raises(GoError) as exc:
        CreateTaskRequest.from_json(body)
    assert str(exc.value) == message


def test_create_from_json_value_field_null_keeps_earlier() -> None:
    assert CreateTaskRequest.from_json(b'{"title":"a","title":null}').title == "a"


def test_update_from_json() -> None:
    assert UpdateTaskRequest.from_json(b"null") == UpdateTaskRequest()
    req = UpdateTaskRequest.from_json(b'{"tags":[],"assigned_to":"","status":"","due_date":null}')
    assert req.tags == [] and req.assigned_to == "" and req.status == "" and req.due_date is None
    with pytest.raises(GoValidationError) as exc:
        UpdateTaskRequest.from_json(b'{"title":""}')
    assert str(exc.value) == msg("UpdateTaskRequest", "Title", "min")
    with pytest.raises(GoError) as exc2:
        UpdateTaskRequest.from_json(b'{"status":true}')
    assert str(exc2.value) == (
        "json: cannot unmarshal bool into Go struct field UpdateTaskRequest.status"
        " of type models.TaskStatus"
    )


@pytest.mark.parametrize("body", [b"{}", b'{"status":""}', b'{"status":null}', b"null"])
def test_status_from_json_required(body: bytes) -> None:
    with pytest.raises(GoValidationError) as exc:
        UpdateTaskStatusRequest.from_json(body)
    assert str(exc.value) == msg("UpdateTaskStatusRequest", "Status", "required")


def test_status_from_json_ok() -> None:
    assert UpdateTaskStatusRequest.from_json(b'{"Status":"bogus"}').status == "bogus"


# -- list query binding -------------------------------------------------------


def q(**kwargs: list[str]) -> ListTasksQuery:
    return ListTasksQuery.from_query(kwargs)


def test_query_defaults() -> None:
    assert q() == ListTasksQuery(
        page_size=20, page_token="", status="", assigned_to="", tags="", sort_order=""
    )


def test_query_all_fields() -> None:
    got = q(
        page_size=["5"],
        page_token=["3"],
        status=["completed"],
        assigned_to=["bob"],
        tags=["a,b"],
        sort_order=["priority_desc"],
    )
    assert got == ListTasksQuery(5, "3", "completed", "bob", "a,b", "priority_desc")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 1), ("100", 100), ("005", 5), ("+5", 5), ("20", 20)],
)
def test_query_page_size_ok(value: str, expected: int) -> None:
    assert q(page_size=[value]).page_size == expected


@pytest.mark.parametrize(
    ("value", "tag"),
    [
        ("", "min"),
        ("0", "min"),
        ("-0", "min"),
        ("-1", "min"),
        ("-9223372036854775808", "min"),
        ("101", "max"),
        ("3000000000", "max"),
        ("99999999999", "max"),
        ("9223372036854775807", "max"),
    ],
)
def test_query_page_size_range(value: str, tag: str) -> None:
    with pytest.raises(GoValidationError) as exc:
        q(page_size=[value])
    assert str(exc.value) == msg("ListTasksQuery", "PageSize", tag)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("abc", 'strconv.ParseInt: parsing "abc": invalid syntax'),
        ("1.5", 'strconv.ParseInt: parsing "1.5": invalid syntax'),
        ("1e2", 'strconv.ParseInt: parsing "1e2": invalid syntax'),
        ("0x10", 'strconv.ParseInt: parsing "0x10": invalid syntax'),
        (" 5", 'strconv.ParseInt: parsing " 5": invalid syntax'),
        (" ", 'strconv.ParseInt: parsing " ": invalid syntax'),
        (
            "9223372036854775808",
            'strconv.ParseInt: parsing "9223372036854775808": value out of range',
        ),
        (
            "99999999999999999999",
            'strconv.ParseInt: parsing "99999999999999999999": value out of range',
        ),
    ],
)
def test_query_page_size_parse_errors(value: str, message: str) -> None:
    with pytest.raises(GoNumError) as exc:
        q(page_size=[value])
    assert str(exc.value) == message


def test_query_first_value_wins() -> None:
    assert q(page_size=["1", "abc"]).page_size == 1
    with pytest.raises(GoNumError):
        q(page_size=["abc", "1"])
    assert q(tags=["a", "b"]).tags == "a"


def test_query_accepts_multidict_and_empty_lists() -> None:
    md = MultiDict([("page_size", "7"), ("page_size", "x"), ("status", "pending")])
    got = ListTasksQuery.from_query(md)
    assert got.page_size == 7 and got.status == "pending"
    assert q(page_size=[]).page_size == 20


def test_query_keys_case_sensitive() -> None:
    assert ListTasksQuery.from_query({"PAGE_SIZE": ["abc"]}).page_size == 20


def test_query_tokens_are_not_parsed_at_bind_time() -> None:
    assert q(page_token=["-1"]).page_token == "-1"
    assert q(page_token=["abc"]).page_token == "abc"


# -- responses ----------------------------------------------------------------


def test_list_response_null_tasks_and_omitted_token() -> None:
    assert go_dumps(ListTasksResponse(None, "", 0)) == '{"tasks":null,"total_count":0}'


def test_list_response_with_token_key_order() -> None:
    resp = ListTasksResponse([make_task()], "1", 2)
    d = resp.to_go()
    assert list(d) == ["tasks", "next_page_token", "total_count"]
    assert go_dumps(resp).startswith('{"tasks":[{"id":"abc",')
    assert go_dumps(resp).endswith('}],"next_page_token":"1","total_count":2}')


def test_error_response() -> None:
    assert (
        go_dumps(ErrorResponse.of("NOT_FOUND", "task with ID x not found"))
        == '{"error":{"code":"NOT_FOUND","message":"task with ID x not found"}}'
    )
    assert go_dumps(ErrorDetail("C", "m", {"k": 1})) == '{"code":"C","message":"m","details":{"k":1}}'
