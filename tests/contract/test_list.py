"""GET /api/v1/tasks: filters, sorting, pagination and query validation.

Every test tags its tasks with a unique tag and filters on it, so tasks created
by other tests (or left over on a shared server) never interfere.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tests.contract.helpers import (
    CORS_HEADERS, TASKS, Client, assert_cors, assert_error, assert_json, task_body, unique_tag,
)

V = "VALIDATION_ERROR"
PAGE_MIN = "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'min' tag"
PAGE_MAX = "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'max' tag"


def _make(client: Client, tag: str, n: int, **extra) -> list[str]:
    return [client.create(title=f"{tag}-{i}", tags=[tag], **extra)["id"] for i in range(n)]


def _ids(page: dict) -> list[str]:
    return [t["id"] for t in page["tasks"] or []]


def test_list_raw_body_shape(client: Client) -> None:
    tag = unique_tag()
    client.create(title="only", tags=[tag])
    assert_json(client.get(TASKS, params={"tags": tag}), 200,
                '{"tasks":[' + task_body(title="only", tags=[tag]) + '],"total_count":1}')


def test_list_empty_result_is_null_raw(client: Client) -> None:
    resp = client.get(TASKS, params={"tags": unique_tag()})
    assert_json(resp, 200, '{"tasks":null,"total_count":0}')


def test_list_filter_status(client: Client) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 3)
    client.patch(f"{TASKS}/{ids[1]}/status", b'{"status":"completed"}')
    client.patch(f"{TASKS}/{ids[2]}/status", b'{"status":"on_hold"}')
    page = client.list(tags=tag, status="completed")
    assert _ids(page) == [ids[1]] and page["total_count"] == 1
    assert set(_ids(client.list(tags=tag, status="pending"))) == {ids[0]}
    assert set(_ids(client.list(tags=tag, status=""))) == set(ids)
    assert_json(client.get(TASKS, params={"tags": tag, "status": "bogus"}), 200,
                '{"tasks":null,"total_count":0}')
    assert client.list(tags=tag, status="COMPLETED")["tasks"] is None


def test_list_filter_assigned_to(client: Client) -> None:
    tag = unique_tag()
    alice = f"alice-{tag}"
    a = client.create(title="a", tags=[tag], assigned_to=alice)["id"]
    client.create(title="b", tags=[tag], assigned_to="bob")
    client.create(title="c", tags=[tag])
    client.create(title="d", tags=[tag], assigned_to="")
    page = client.list(assigned_to=alice)
    assert _ids(page) == [a] and page["total_count"] == 1
    assert len(_ids(client.list(tags=tag, assigned_to=""))) == 4  # empty filter = no filter
    assert client.list(tags=tag, assigned_to=alice.upper())["tasks"] is None


def test_list_filter_tags_all_of_trimmed(client: Client) -> None:
    t1, t2, t3 = unique_tag("a"), unique_tag("b"), unique_tag("c")
    both = client.create(title="both", tags=[t1, t2])["id"]
    only1 = client.create(title="one", tags=[t1, t3])["id"]
    client.create(title="two", tags=[t2])
    assert set(_ids(client.list(tags=t1))) == {both, only1}
    assert _ids(client.list(tags=f"{t1},{t2}")) == [both]
    assert _ids(client.list(tags=f"  {t2} ,\t{t1}  ")) == [both]
    assert client.list(tags=f"{t1},{t2},{t3}")["tasks"] is None
    # an empty element after splitting must match an empty tag -> nothing matches
    assert client.list(tags=f"{t1},")["tasks"] is None
    assert client.list(tags=f"{t1}, ")["tasks"] is None
    # case sensitive
    assert client.list(tags=t1.upper())["tasks"] is None


def test_list_filter_tag_empty_string_matches_empty_tag(client: Client) -> None:
    tag = unique_tag()
    weird = client.create(title="w", tags=[tag, ""])["id"]
    client.create(title="n", tags=[tag])
    assert _ids(client.list(tags=f"{tag},")) == [weird]


def test_list_filters_combined_and_total_count(client: Client) -> None:
    tag = unique_tag()
    target = client.create(title="t", tags=[tag], assigned_to="u1")["id"]
    client.create(title="x", tags=[tag], assigned_to="u2")
    other = client.create(title="y", tags=[tag], assigned_to="u1")["id"]
    client.patch(f"{TASKS}/{target}/status", b'{"status":"in_progress"}')
    page = client.list(tags=tag, assigned_to="u1", status="in_progress")
    assert _ids(page) == [target] and page["total_count"] == 1
    assert set(_ids(client.list(tags=tag, assigned_to="u1"))) == {target, other}


@pytest.fixture
def sort_set(client: Client) -> tuple[str, list[dict]]:
    """Five tasks in creation order with mixed priorities and due dates.

    The +05:00 due date is earlier in absolute time than the lexically smaller
    UTC one, so sorting must compare instants, not strings.
    """
    tag = unique_tag("sort")
    specs = [
        ("low", "2030-03-01T00:00:00Z"),
        ("critical", None),
        ("high", "2030-01-01T06:00:00Z"),
        ("medium", "2030-01-01T10:00:00+05:00"),  # 05:00Z
        ("low", None),
    ]
    tasks = []
    for i, (prio, due) in enumerate(specs):
        body = {"title": f"{i}-{prio}", "priority": prio, "tags": [tag]}
        if due:
            body["due_date"] = due
        tasks.append(client.create(**body))
    return tag, tasks


def test_list_sort_created_at_asc(client: Client, sort_set) -> None:
    tag, tasks = sort_set
    assert _ids(client.list(tags=tag, sort_order="created_at_asc")) == [t["id"] for t in tasks]


def test_list_sort_created_at_desc(client: Client, sort_set) -> None:
    tag, tasks = sort_set
    assert _ids(client.list(tags=tag, sort_order="created_at_desc")) == [t["id"] for t in reversed(tasks)]


def test_list_sort_due_date_asc_nil_last(client: Client, sort_set) -> None:
    tag, tasks = sort_set
    ids = _ids(client.list(tags=tag, sort_order="due_date_asc"))
    assert ids[:3] == [tasks[3]["id"], tasks[2]["id"], tasks[0]["id"]]
    assert set(ids[3:]) == {tasks[1]["id"], tasks[4]["id"]}


def test_list_sort_due_date_desc_nil_last(client: Client, sort_set) -> None:
    tag, tasks = sort_set
    ids = _ids(client.list(tags=tag, sort_order="due_date_desc"))
    assert ids[:3] == [tasks[0]["id"], tasks[2]["id"], tasks[3]["id"]]
    assert set(ids[3:]) == {tasks[1]["id"], tasks[4]["id"]}


def _priority_groups(page: dict) -> list[set[str]]:
    groups: list[tuple[str, set[str]]] = []
    for t in page["tasks"]:
        if groups and groups[-1][0] == t["priority"]:
            groups[-1][1].add(t["id"])
        else:
            groups.append((t["priority"], {t["id"]}))
    return [g for _, g in groups]


def test_list_sort_priority_asc_ties_unordered(client: Client, sort_set) -> None:
    tag, t = sort_set
    groups = _priority_groups(client.list(tags=tag, sort_order="priority_asc"))
    assert groups == [{t[0]["id"], t[4]["id"]}, {t[3]["id"]}, {t[2]["id"]}, {t[1]["id"]}]


def test_list_sort_priority_desc_ties_unordered(client: Client, sort_set) -> None:
    tag, t = sort_set
    groups = _priority_groups(client.list(tags=tag, sort_order="priority_desc"))
    assert groups == [{t[1]["id"]}, {t[2]["id"]}, {t[3]["id"]}, {t[0]["id"], t[4]["id"]}]


@pytest.mark.parametrize("order", ["bogus", "CREATED_AT_ASC", "", "created_at"])
def test_list_unknown_sort_order_accepted(client: Client, sort_set, order: str) -> None:
    tag, tasks = sort_set
    page = client.list(tags=tag, sort_order=order)
    assert Counter(_ids(page)) == Counter(t["id"] for t in tasks)
    assert page["total_count"] == 5 and "next_page_token" not in page


def test_list_page_size_default_20(client: Client) -> None:
    tag = unique_tag()
    _make(client, tag, 21)
    resp = client.get(TASKS, params={"tags": tag})
    page = resp.json()
    assert len(page["tasks"]) == 20
    assert page["next_page_token"] == "20" and page["total_count"] == 21
    assert list(page) == ["tasks", "next_page_token", "total_count"]
    assert resp.content.endswith(b'],"next_page_token":"20","total_count":21}')
    last = client.list(tags=tag, page_token="20")
    assert len(last["tasks"]) == 1 and "next_page_token" not in last
    assert last["total_count"] == 21


def test_list_page_size_bounds_accepted(client: Client) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 3)
    assert len(client.list(tags=tag, page_size=1)["tasks"]) == 1
    assert len(client.list(tags=tag, page_size=100)["tasks"]) == 3
    assert len(client.list(tags=tag, page_size="005")["tasks"]) == 3
    assert len(client.list(tags=tag, page_size="+2")["tasks"]) == 2  # %2B2 -> "+2"
    assert len(ids) == 3


def test_list_pagination_walk_with_tokens(client: Client) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 5)
    seen: list[str] = []
    tokens: list[str | None] = []
    token = None
    while True:
        params = {"tags": tag, "page_size": 2, "sort_order": "created_at_asc"}
        if token is not None:
            params["page_token"] = token
        page = client.list(**params)
        assert page["total_count"] == 5
        seen += _ids(page)
        token = page.get("next_page_token")
        tokens.append(token)
        if token is None:
            break
    assert seen == ids
    assert tokens == ["2", "4", None]


def test_list_exact_last_page_has_no_next_token(client: Client) -> None:
    tag = unique_tag()
    _make(client, tag, 4)
    page = client.list(tags=tag, page_size=2, page_token="2")
    assert len(page["tasks"]) == 2 and "next_page_token" not in page


@pytest.mark.parametrize("token", ["4", "5", "999"])
def test_list_token_at_or_past_end_returns_null_tasks(client: Client, token: str) -> None:
    tag = unique_tag()
    _make(client, tag, 4)
    resp = client.get(TASKS, params={"tags": tag, "page_token": token})
    assert_json(resp, 200, '{"tasks":null,"total_count":4}')


@pytest.mark.parametrize("token", ["abc", "1.5", "0x1", "", "-0", "99999999999999999999", " 1", "1 "])
def test_list_unparseable_token_treated_as_zero(client: Client, token: str) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 3)
    page = client.list(tags=tag, page_token=token, sort_order="created_at_asc", page_size=2)
    assert _ids(page) == ids[:2] and page["next_page_token"] == "2"


def test_list_plus_sign_token_is_atoi_valid(client: Client) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 3)
    page = client.list(tags=tag, page_token="+1", sort_order="created_at_asc")  # sent as %2B1
    assert _ids(page) == ids[1:]


def test_list_raw_plus_in_token_decodes_to_space(client: Client) -> None:
    tag = unique_tag()
    ids = _make(client, tag, 3)
    resp = client.get(f"{TASKS}?tags={tag}&sort_order=created_at_asc&page_token=+1")
    assert _ids(resp.json()) == ids


@pytest.mark.parametrize("token", ["-1", "-5"])
@pytest.mark.parametrize("has_tasks", [True, False])
def test_list_negative_token_500_empty_body(client: Client, token: str, has_tasks: bool) -> None:
    tag = unique_tag()
    if has_tasks:
        _make(client, tag, 2)
    resp = client.get(TASKS, params={"tags": tag, "page_token": token})
    assert resp.status_code == 500
    assert resp.content == b""
    assert "Content-Type" not in resp.headers
    assert_cors(resp)


@pytest.mark.parametrize(("raw_value", "message"), [
    ("0", PAGE_MIN),
    ("-1", PAGE_MIN),
    ("-0", PAGE_MIN),
    ("", PAGE_MIN),
    ("101", PAGE_MAX),
    ("1000", PAGE_MAX),
    ("abc", 'strconv.ParseInt: parsing "abc": invalid syntax'),
    ("1.5", 'strconv.ParseInt: parsing "1.5": invalid syntax'),
    ("1e2", 'strconv.ParseInt: parsing "1e2": invalid syntax'),
    ("0x10", 'strconv.ParseInt: parsing "0x10": invalid syntax'),
    ("+5", 'strconv.ParseInt: parsing " 5": invalid syntax'),
    ("%205", 'strconv.ParseInt: parsing " 5": invalid syntax'),
    ("99999999999999999999", 'strconv.ParseInt: parsing "99999999999999999999": value out of range'),
])
def test_list_page_size_validation_errors(client: Client, raw_value: str, message: str) -> None:
    resp = client.get(f"{TASKS}?page_size={raw_value}")
    assert_error(resp, 400, V, message)


def test_list_repeated_param_first_wins(client: Client) -> None:
    tag, other = unique_tag(), unique_tag()
    _make(client, tag, 3)
    resp = client.get(f"{TASKS}?tags={tag}&page_size=1&page_size=abc")
    assert resp.status_code == 200 and len(resp.json()["tasks"]) == 1
    assert_error(client.get(f"{TASKS}?tags={tag}&page_size=abc&page_size=1"), 400, V,
                 'strconv.ParseInt: parsing "abc": invalid syntax')
    assert client.list(tags=[tag, other])["total_count"] == 3
    assert client.list(tags=[other, tag])["tasks"] is None


def test_list_query_keys_case_sensitive(client: Client) -> None:
    tag = unique_tag()
    _make(client, tag, 2)
    page = client.list(tags=tag, PAGE_SIZE="abc", Status="completed", TAGS="zzz")
    assert page["total_count"] == 2


def test_list_response_headers(client: Client) -> None:
    resp = client.get(TASKS, params={"tags": unique_tag()})
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"
    for name, value in CORS_HEADERS.items():
        assert resp.headers[name] == value


def test_list_tasks_serialized_like_get(client: Client) -> None:
    tag = unique_tag()
    created = client.post(TASKS, json.dumps({"title": "<s>", "tags": [tag], "assigned_to": "",
                                             "due_date": "2030-01-01T00:00:00.5+02:00"}))
    listed = client.get(TASKS, params={"tags": tag})
    assert listed.content == b'{"tasks":[' + created.content + b'],"total_count":1}'
