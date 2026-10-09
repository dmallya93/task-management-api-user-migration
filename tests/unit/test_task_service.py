"""Unit tests for task_api.services.task_service (port of internal/services/task_service.go)."""

from __future__ import annotations

import re
import threading

import pytest

from task_api.go_compat import GoTime
from task_api.models import CreateTaskRequest, ListTasksQuery, Task, UpdateTaskRequest
from task_api.services.task_service import (
    InvalidPriorityError,
    InvalidStatusError,
    SliceBoundsPanic,
    TaskError,
    TaskNotFoundError,
    TaskService,
    go_trim_space,
)

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def t(year: int, hour: int = 0, offset_h: int = 0) -> GoTime:
    return GoTime.from_parts(year, 1, 1, hour, offset=offset_h * 3600)


@pytest.fixture
def svc() -> TaskService:
    return TaskService()


@pytest.fixture
def empty() -> TaskService:
    s = TaskService()
    for task in list(s._tasks.values()):
        s.delete_task(task.id)
    return s


def titles(resp_tasks: list[Task] | None) -> list[str]:
    return [x.title for x in resp_tasks or []]


def add(s: TaskService, title: str, **kw: object) -> Task:
    return s.create_task(CreateTaskRequest(title=title, **kw))  # type: ignore[arg-type]


# -- seeds & create -----------------------------------------------------------


def test_seed_tasks(svc: TaskService) -> None:
    resp = svc.list_tasks(ListTasksQuery())
    assert resp.total_count == 2 and resp.next_page_token == ""
    first, second = resp.tasks or []
    assert (first.title, first.description, first.priority, first.tags) == (
        "Implement Go REST API",
        "Create REST API with Gin framework",
        "high",
        ["go", "rest", "api"],
    )
    assert (second.title, second.description, second.priority, second.tags) == (
        "Add gRPC support",
        "Implement gRPC server with native Go support",
        "medium",
        ["go", "grpc", "protobuf"],
    )
    for task in (first, second):
        assert task.status == "pending" and task.created_by == "system"
        assert task.assigned_to is None and task.due_date is None and task.completed_at is None
        assert task.created_at == task.updated_at
        assert UUID4.match(task.id)
    assert list(first.to_go()) == [
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


def test_create_defaults(empty: TaskService) -> None:
    before = GoTime.now()
    task = add(empty, "x")
    assert UUID4.match(task.id)
    assert task.status == "pending" and type(task.status) is str
    assert task.priority == "medium" and type(task.priority) is str
    assert task.tags == [] and task.created_by == "system" and task.description == ""
    assert task.created_at is task.updated_at or task.created_at == task.updated_at
    assert not task.created_at.before(before)
    assert empty.get_task(task.id) is task


def test_create_keeps_fields(empty: TaskService) -> None:
    due = t(2030, offset_h=-7)
    task = add(empty, "x", description="d", priority="critical", tags=["a"], assigned_to="", due_date=due)
    assert (task.priority, task.tags, task.assigned_to, task.due_date) == ("critical", ["a"], "", due)
    assert task.to_go()["assigned_to"] == ""


def test_create_invalid_priority_not_stored(empty: TaskService) -> None:
    with pytest.raises(InvalidPriorityError) as exc:
        add(empty, "x", priority="HIGH")
    assert str(exc.value) == "invalid priority: HIGH"
    assert isinstance(exc.value, TaskError)
    assert empty.list_tasks(ListTasksQuery()).total_count == 0


def test_unique_ids(empty: TaskService) -> None:
    assert len({add(empty, str(i)).id for i in range(50)}) == 50


# -- get / delete -------------------------------------------------------------


def test_get_missing(svc: TaskService) -> None:
    with pytest.raises(TaskNotFoundError) as exc:
        svc.get_task("nope")
    assert str(exc.value) == "task with ID nope not found"
    assert exc.value.task_id == "nope"


def test_delete(svc: TaskService) -> None:
    task = add(svc, "x")
    svc.delete_task(task.id)
    with pytest.raises(TaskNotFoundError):
        svc.get_task(task.id)
    with pytest.raises(TaskNotFoundError) as exc:
        svc.delete_task(task.id)
    assert str(exc.value) == f"task with ID {task.id} not found"
    assert svc.list_tasks(ListTasksQuery()).total_count == 2


# -- update -------------------------------------------------------------------


def test_update_all_fields(empty: TaskService) -> None:
    task = add(empty, "x")
    due = t(2031)
    old_updated = task.updated_at
    got = empty.update_task(
        task.id,
        UpdateTaskRequest(
            title="y",
            description="d2",
            status="in_progress",
            priority="low",
            tags=["z"],
            assigned_to="bob",
            due_date=due,
        ),
    )
    assert got is task
    assert (got.title, got.description, got.status, got.priority, got.tags) == (
        "y",
        "d2",
        "in_progress",
        "low",
        ["z"],
    )
    assert got.assigned_to == "bob" and got.due_date == due
    assert got.completed_at is None
    assert not got.updated_at.before(old_updated)
    assert got.created_at == old_updated


def test_update_empty_request_bumps_updated_at(empty: TaskService) -> None:
    task = add(empty, "x", assigned_to="bob", tags=["a"], due_date=t(2030))
    task.updated_at = t(2000)
    empty.update_task(task.id, UpdateTaskRequest())
    assert task.updated_at.after(t(2000))
    assert (task.title, task.assigned_to, task.tags, task.due_date) == ("x", "bob", ["a"], t(2030))


def test_update_tags_empty_list_replaces_none_keeps(empty: TaskService) -> None:
    task = add(empty, "x", tags=["a", "b"])
    empty.update_task(task.id, UpdateTaskRequest(tags=None))
    assert task.tags == ["a", "b"]
    empty.update_task(task.id, UpdateTaskRequest(tags=[]))
    assert task.tags == []


def test_update_assigned_to_empty_string(empty: TaskService) -> None:
    task = add(empty, "x", assigned_to="bob")
    empty.update_task(task.id, UpdateTaskRequest(assigned_to=None))
    assert task.assigned_to == "bob"
    empty.update_task(task.id, UpdateTaskRequest(assigned_to=""))
    assert task.assigned_to == "" and task.to_go()["assigned_to"] == ""


def test_update_missing_checked_before_fields(empty: TaskService) -> None:
    with pytest.raises(TaskNotFoundError) as exc:
        empty.update_task("nope", UpdateTaskRequest(status="bogus", priority="bogus"))
    assert str(exc.value) == "task with ID nope not found"


def test_update_invalid_priority_keeps_earlier_fields(empty: TaskService) -> None:
    task = add(empty, "x", tags=["a"])
    stamp = task.updated_at
    with pytest.raises(InvalidPriorityError) as exc:
        empty.update_task(
            task.id,
            UpdateTaskRequest(
                title="new", description="nd", status="completed", priority="urgent", tags=["b"]
            ),
        )
    assert str(exc.value) == "invalid priority: urgent"
    assert (task.title, task.description, task.status) == ("new", "nd", "completed")
    assert task.completed_at is not None  # status applied before priority
    assert task.priority == "medium" and task.tags == ["a"]  # later fields untouched
    assert task.updated_at is stamp


def test_update_invalid_status_keeps_title(empty: TaskService) -> None:
    task = add(empty, "x")
    stamp = task.updated_at
    with pytest.raises(InvalidStatusError) as exc:
        empty.update_task(task.id, UpdateTaskRequest(title="new", status="", priority="low"))
    assert str(exc.value) == "invalid status: "
    assert task.title == "new" and task.status == "pending" and task.priority == "medium"
    assert task.updated_at is stamp
    with pytest.raises(InvalidPriorityError) as exc2:
        empty.update_task(task.id, UpdateTaskRequest(priority=""))
    assert str(exc2.value) == "invalid priority: "


def test_completed_at_set_once_never_cleared(empty: TaskService) -> None:
    task = add(empty, "x")
    empty.update_task_status(task.id, "completed")
    first = task.completed_at
    assert first is not None
    empty.update_task_status(task.id, "pending")
    assert task.completed_at is first
    empty.update_task_status(task.id, "completed")
    assert task.completed_at is first
    assert list(task.to_go())[-1] == "completed_at"


def test_update_task_status(empty: TaskService) -> None:
    task = add(empty, "x")
    task.updated_at = t(2000)
    got = empty.update_task_status(task.id, "pending")  # same status still bumps
    assert got is task and task.status == "pending" and task.updated_at.after(t(2000))
    with pytest.raises(TaskNotFoundError):
        empty.update_task_status("nope", "completed")
    with pytest.raises(InvalidStatusError):
        empty.update_task_status(task.id, "bad")
    for status in ("in_progress", "cancelled", "on_hold"):
        assert empty.update_task_status(task.id, status).status == status
    assert task.completed_at is None


# -- list: filters --------------------------------------------------------------


def test_filter_status(empty: TaskService) -> None:
    a = add(empty, "a")
    add(empty, "b")
    empty.update_task_status(a.id, "completed")
    assert titles(empty.list_tasks(ListTasksQuery(status="completed")).tasks) == ["a"]
    assert titles(empty.list_tasks(ListTasksQuery(status="pending")).tasks) == ["b"]
    resp = empty.list_tasks(ListTasksQuery(status="COMPLETED"))
    assert resp.tasks is None and resp.total_count == 0
    assert empty.list_tasks(ListTasksQuery(status="bogus")).tasks is None


def test_filter_assigned_to(empty: TaskService) -> None:
    add(empty, "bob", assigned_to="bob")
    add(empty, "blank", assigned_to="")
    add(empty, "nil")
    assert titles(empty.list_tasks(ListTasksQuery(assigned_to="bob")).tasks) == ["bob"]
    assert empty.list_tasks(ListTasksQuery(assigned_to="Bob")).tasks is None
    assert empty.list_tasks(ListTasksQuery(assigned_to="")).total_count == 3


def test_filter_tags_all_of_trimmed(svc: TaskService) -> None:
    def tagged(tags: str) -> list[str]:
        return titles(svc.list_tasks(ListTasksQuery(tags=tags)).tasks)

    seed1, seed2 = "Implement Go REST API", "Add gRPC support"
    assert tagged("go") == [seed1, seed2]
    assert tagged("go, rest ") == [seed1]
    assert tagged(" grpc,protobuf") == [seed2]
    assert tagged("rest,grpc") == []
    assert tagged("Go") == []
    assert tagged("") == [seed1, seed2]
    assert tagged("go,") == []  # empty element must match an empty tag
    add(svc, "blank-tag", tags=["go", ""])
    assert tagged("go,") == ["blank-tag"]
    assert tagged(" ,go") == ["blank-tag"]


def test_filters_combine(empty: TaskService) -> None:
    add(empty, "a", assigned_to="bob", tags=["x"])
    add(empty, "b", assigned_to="bob", tags=["y"])
    c = add(empty, "c", assigned_to="bob", tags=["x"])
    empty.update_task_status(c.id, "completed")
    got = empty.list_tasks(ListTasksQuery(status="pending", assigned_to="bob", tags="x"))
    assert titles(got.tasks) == ["a"] and got.total_count == 1


def test_go_trim_space() -> None:
    assert go_trim_space("\t\n\v\f\r \x85\xa0a b　 ") == "a b"
    assert go_trim_space("\x1ca\x1f") == "\x1ca\x1f"  # not Go whitespace (str.strip removes it)


# -- list: sorting ------------------------------------------------------------


@pytest.fixture
def sortable(empty: TaskService) -> TaskService:
    specs = [
        ("n1", "low", None, 3),
        ("d2030", "critical", t(2030), 1),
        ("d2025", "bogus-prio", t(2025), 2),
        ("n2", "high", None, 0),
        ("d2028", "medium", t(2028), 4),
    ]
    for title, prio, due, created_year_off in specs:
        task = add(empty, title, due_date=due)
        task.priority = prio  # unvalidated priorities can only exist via direct mutation
        task.created_at = t(2000 + created_year_off)
    return empty


def sorted_titles(s: TaskService, order: str) -> list[str]:
    return titles(s.list_tasks(ListTasksQuery(sort_order=order)).tasks)


def test_sort_created_at(sortable: TaskService) -> None:
    assert sorted_titles(sortable, "created_at_asc") == ["n2", "d2030", "d2025", "n1", "d2028"]
    assert sorted_titles(sortable, "created_at_desc") == ["d2028", "n1", "d2025", "d2030", "n2"]


def test_sort_due_date_nil_last(sortable: TaskService) -> None:
    asc = sorted_titles(sortable, "due_date_asc")
    desc = sorted_titles(sortable, "due_date_desc")
    assert asc[:3] == ["d2025", "d2028", "d2030"] and set(asc[3:]) == {"n1", "n2"}
    assert desc[:3] == ["d2030", "d2028", "d2025"] and set(desc[3:]) == {"n1", "n2"}


def test_sort_due_date_offset_aware(empty: TaskService) -> None:
    # 2030-01-01T05:00+08:00 (= 2029-12-31T21:00Z) is earlier than 2030-01-01T00:00Z.
    add(empty, "utc", due_date=t(2030, hour=0))
    add(empty, "plus8", due_date=t(2030, hour=5, offset_h=8))
    add(empty, "minus5", due_date=GoTime.from_parts(2029, 12, 31, 22, offset=-5 * 3600))  # 03:00Z
    assert sorted_titles(empty, "due_date_asc") == ["plus8", "utc", "minus5"]
    assert sorted_titles(empty, "due_date_desc") == ["minus5", "utc", "plus8"]


def test_sort_due_date_nanoseconds(empty: TaskService) -> None:
    add(empty, "later", due_date=GoTime.from_parts(2030, 1, 1, nanosecond=2))
    add(empty, "earlier", due_date=GoTime.from_parts(2030, 1, 1, nanosecond=1))
    assert sorted_titles(empty, "due_date_asc") == ["earlier", "later"]


def test_sort_priority_unknown_counts_as_medium(sortable: TaskService) -> None:
    asc = sorted_titles(sortable, "priority_asc")
    desc = sorted_titles(sortable, "priority_desc")
    assert asc[0] == "n1" and set(asc[1:3]) == {"d2025", "d2028"} and asc[3:] == ["n2", "d2030"]
    assert desc[:2] == ["d2030", "n2"] and set(desc[2:4]) == {"d2025", "d2028"} and desc[4] == "n1"


@pytest.mark.parametrize("order", ["", "PRIORITY_ASC", "random", "created_at"])
def test_unknown_sort_keeps_insertion_order(sortable: TaskService, order: str) -> None:
    assert sorted_titles(sortable, order) == ["n1", "d2030", "d2025", "n2", "d2028"]


def test_sort_does_not_reorder_store(sortable: TaskService) -> None:
    sorted_titles(sortable, "priority_desc")
    assert sorted_titles(sortable, "") == ["n1", "d2030", "d2025", "n2", "d2028"]


# -- list: pagination ---------------------------------------------------------


@pytest.fixture
def five(empty: TaskService) -> TaskService:
    for i in range(5):
        add(empty, f"t{i}")
    return empty


def page(s: TaskService, size: int = 20, token: str = "") -> tuple[list[str] | None, str, int]:
    resp = s.list_tasks(ListTasksQuery(page_size=size, page_token=token))
    return (None if resp.tasks is None else titles(resp.tasks)), resp.next_page_token, resp.total_count


def test_pagination_walk(five: TaskService) -> None:
    assert page(five, 2) == (["t0", "t1"], "2", 5)
    assert page(five, 2, "2") == (["t2", "t3"], "4", 5)
    assert page(five, 2, "4") == (["t4"], "", 5)
    assert page(five, 5) == (["t0", "t1", "t2", "t3", "t4"], "", 5)
    assert page(five, 4, "1") == (["t1", "t2", "t3", "t4"], "", 5)


def test_pagination_past_end_is_null(five: TaskService) -> None:
    assert page(five, 2, "5") == (None, "", 5)
    assert page(five, 2, "100") == (None, "", 5)


def test_empty_result_is_null(empty: TaskService) -> None:
    assert page(empty) == (None, "", 0)


@pytest.mark.parametrize(
    "token", ["", "abc", "1.5", "0x1", " 1", "1 ", "99999999999999999999", "-0", "+0", "0"]
)
def test_bad_tokens_mean_zero(five: TaskService, token: str) -> None:
    assert page(five, 2, token) == (["t0", "t1"], "2", 5)


def test_signed_token(five: TaskService) -> None:
    assert page(five, 2, "+1") == (["t1", "t2"], "3", 5)


@pytest.mark.parametrize("token", ["-1", "-5", "-9223372036854775808"])
def test_negative_token_panics(five: TaskService, token: str) -> None:
    with pytest.raises(SliceBoundsPanic) as exc:
        page(five, 2, token)
    assert isinstance(exc.value, IndexError) and not isinstance(exc.value, TaskError)


def test_negative_token_panics_even_when_empty(empty: TaskService) -> None:
    with pytest.raises(SliceBoundsPanic):
        empty.list_tasks(ListTasksQuery(status="none", page_token="-1"))


def test_page_size_clamp(empty: TaskService) -> None:
    for i in range(130):
        add(empty, f"t{i}")
    assert page(empty, 1000)[1:] == ("100", 130)
    assert len(page(empty, 1000)[0] or []) == 100
    assert page(empty, 0)[1] == "20"  # Go: pageSize <= 0 -> 20
    assert page(empty, -3)[1] == "20"
    assert len(page(empty, 1)[0] or []) == 1


def test_token_int64_overflow_wraps(svc: TaskService) -> None:
    # Oracle: start+page_size overflows int64 -> negative end -> negative next token.
    assert page(svc, 20, "9223372036854775807") == (None, "-9223372036854775789", 2)
    assert page(svc, 100, "9223372036854775790") == (None, "-9223372036854775726", 2)
    assert page(svc, 100, "9223372036854775700") == (None, "", 2)


def test_list_returns_copies(empty: TaskService) -> None:
    task = add(empty, "x")
    listed = (empty.list_tasks(ListTasksQuery()).tasks or [])[0]
    assert listed is not task and listed == task
    empty.update_task(task.id, UpdateTaskRequest(title="y"))
    assert listed.title == "x"


def test_concurrent_creates(empty: TaskService) -> None:
    def worker() -> None:
        for i in range(50):
            add(empty, f"t{i}")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert empty.list_tasks(ListTasksQuery()).total_count == 400
