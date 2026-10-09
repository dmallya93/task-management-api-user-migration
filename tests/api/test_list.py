"""GET /api/v1/tasks (``listTasks``): seeds, filters, sorting, pagination, query binding."""

from __future__ import annotations

import json

import pytest
from flask.testing import FlaskClient

from tests.api.helpers import (
    SEED_TITLES,
    TASKS,
    FakeClock,
    all_titles,
    assert_empty,
    assert_json,
    assert_redirect,
    assert_validation,
    create,
    list_json,
    seed_ids,
    task_json,
)

PS_MIN = "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'min' tag"
PS_MAX = "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'max' tag"


def lst(client: FlaskClient, query: str = ""):  # type: ignore[no-untyped-def]
    return client.get(TASKS + (f"?{query}" if query else ""))


def make_many(client: FlaskClient, n: int, prefix: str = "t", **fields: object) -> list[dict]:  # type: ignore[type-arg]
    return [create(client, {"title": f"{prefix}{i:03d}", **fields}) for i in range(n)]


# -- seeds and raw shape ---------------------------------------------------------


def test_fresh_app_lists_exactly_the_two_seeds(client: FlaskClient, clock: FakeClock) -> None:
    ids = seed_ids(client)
    resp = lst(client, "sort_order=created_at_asc")
    assert_json(resp, 200)
    seed1 = task_json(id=ids[SEED_TITLES[0]], title=SEED_TITLES[0],
                      description="Create REST API with Gin framework", priority="high",
                      tags=["go", "rest", "api"], created_at=clock.stamp(0))
    seed2 = task_json(id=ids[SEED_TITLES[1]], title=SEED_TITLES[1],
                      description="Implement gRPC server with native Go support", priority="medium",
                      tags=["go", "grpc", "protobuf"], created_at=clock.stamp(1))
    assert resp.data.decode() == '{"tasks":[' + seed1 + "," + seed2 + '],"total_count":2}'


def test_default_list_without_sort_contains_seeds(client: FlaskClient) -> None:
    body = list_json(client)
    assert list(body) == ["tasks", "total_count"]
    assert body["total_count"] == 2
    assert sorted(t["title"] for t in body["tasks"]) == sorted(SEED_TITLES)
    for t in body["tasks"]:
        assert t["status"] == "pending" and t["created_by"] == "system"
        assert "assigned_to" not in t and "due_date" not in t and "completed_at" not in t


def test_empty_store_gives_tasks_null(client: FlaskClient) -> None:
    for task_id in seed_ids(client).values():
        assert_empty(client.delete(f"{TASKS}/{task_id}"), 204)
    resp = lst(client)
    assert_json(resp, 200)
    assert resp.data == b'{"tasks":null,"total_count":0}'


def test_no_match_gives_tasks_null_and_zero_total(client: FlaskClient) -> None:
    resp = lst(client, "status=bogus")
    assert_json(resp, 200)
    assert resp.data == b'{"tasks":null,"total_count":0}'


def test_list_returns_copies_in_go_shape(client: FlaskClient) -> None:
    task = create(client, {"title": "<&>", "assigned_to": "a", "due_date": "2030-01-01T00:00:00+01:00"})
    resp = lst(client, "assigned_to=a")
    assert_json(resp, 200)
    assert resp.data.decode() == '{"tasks":[' + client.get(f"{TASKS}/{task['id']}").data.decode() + '],"total_count":1}'
    assert b'"title":"\\u003c\\u0026\\u003e"' in resp.data


# -- pagination ---------------------------------------------------------------


def test_default_page_size_is_20_with_next_token(client: FlaskClient) -> None:
    make_many(client, 23)
    resp = lst(client, "sort_order=created_at_asc")
    body = assert_json(resp, 200)
    assert list(body) == ["tasks", "next_page_token", "total_count"]
    assert len(body["tasks"]) == 20
    assert body["next_page_token"] == "20"
    assert body["total_count"] == 25
    assert resp.data.endswith(b'],"next_page_token":"20","total_count":25}')


def test_walk_pages_with_next_page_token(client: FlaskClient) -> None:
    make_many(client, 5)
    seen: list[str] = []
    token = ""
    tokens: list[str] = []
    while True:
        body = list_json(client, f"page_size=2&sort_order=created_at_asc&page_token={token}")
        assert body["total_count"] == 7
        seen += [t["title"] for t in body["tasks"]]
        if "next_page_token" not in body:
            break
        token = body["next_page_token"]
        tokens.append(token)
    assert tokens == ["2", "4", "6"]
    assert seen == list(SEED_TITLES) + [f"t{i:03d}" for i in range(5)]


def test_exact_last_page_has_no_next_token(client: FlaskClient) -> None:
    make_many(client, 2)
    body = list_json(client, "page_size=2&page_token=2")
    assert len(body["tasks"]) == 2 and "next_page_token" not in body
    body = list_json(client, "page_size=4")
    assert len(body["tasks"]) == 4 and "next_page_token" not in body


@pytest.mark.parametrize("token", ["2", "3", "1000"])
def test_token_at_or_past_end_gives_null_tasks(client: FlaskClient, token: str) -> None:
    resp = lst(client, f"page_token={token}")
    assert_json(resp, 200)
    assert resp.data == b'{"tasks":null,"total_count":2}'


def test_page_size_100_max_and_101_error(client: FlaskClient) -> None:
    make_many(client, 101)
    body = list_json(client, "page_size=100")
    assert len(body["tasks"]) == 100 and body["next_page_token"] == "100" and body["total_count"] == 103
    assert_validation(lst(client, "page_size=101"), PS_MAX)


@pytest.mark.parametrize(("query", "count"), [("page_size=1", 1), ("page_size=005", 2), ("page_size=%2B1", 1)])
def test_page_size_accepted_forms(client: FlaskClient, query: str, count: int) -> None:
    assert len(list_json(client, query)["tasks"]) == count


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("page_size=", PS_MIN),
        ("page_size", PS_MIN),
        ("page_size=0", PS_MIN),
        ("page_size=-0", PS_MIN),
        ("page_size=-1", PS_MIN),
        ("page_size=-9223372036854775808", PS_MIN),
        ("page_size=101", PS_MAX),
        ("page_size=9223372036854775807", PS_MAX),
        ("page_size=abc", 'strconv.ParseInt: parsing "abc": invalid syntax'),
        ("page_size=1.5", 'strconv.ParseInt: parsing "1.5": invalid syntax'),
        ("page_size=1e2", 'strconv.ParseInt: parsing "1e2": invalid syntax'),
        ("page_size=0x10", 'strconv.ParseInt: parsing "0x10": invalid syntax'),
        ("page_size=+5", 'strconv.ParseInt: parsing " 5": invalid syntax'),
        ("page_size=%205", 'strconv.ParseInt: parsing " 5": invalid syntax'),
        ("page_size=99999999999999999999", 'strconv.ParseInt: parsing "99999999999999999999": value out of range'),
        ("page_size=-9223372036854775809", 'strconv.ParseInt: parsing "-9223372036854775809": value out of range'),
        ("page_size=abc&page_size=1", 'strconv.ParseInt: parsing "abc": invalid syntax'),
        ("page_size=%zz&page_size=abc", 'strconv.ParseInt: parsing "abc": invalid syntax'),
    ],
)
def test_page_size_errors(client: FlaskClient, query: str, message: str) -> None:
    assert_validation(lst(client, query), message)


def test_page_size_first_value_wins(client: FlaskClient) -> None:
    assert len(list_json(client, "page_size=1&page_size=abc")["tasks"]) == 1
    assert len(list_json(client, "page_size=1&page_size=0")["tasks"]) == 1


@pytest.mark.parametrize("query", ["PAGE_SIZE=abc", "page_size=%zz", "page_size=1;x=y", "Page_Size=0"])
def test_page_size_ignored_pairs_default_to_20(client: FlaskClient, query: str) -> None:
    """Keys are case-sensitive; Go's ParseQuery drops pairs with a bad escape or a ``;``."""
    make_many(client, 20)
    body = list_json(client, query)
    assert len(body["tasks"]) == 20 and body["next_page_token"] == "20"


@pytest.mark.parametrize(
    ("token", "first_index"),
    [
        ("0", 0), ("1", 1), ("-0", 0), ("%2B1", 1), ("01", 1),
        ("abc", 0), ("", 0), ("1.5", 0), ("0x1", 0), ("%201", 0), ("1%20", 0), ("+1", 0),
        ("99999999999999999999", 0), ("-99999999999999999999", 0),
    ],
)
def test_page_token_atoi_quirks(client: FlaskClient, token: str, first_index: int) -> None:
    body = list_json(client, f"page_size=1&sort_order=created_at_asc&page_token={token}")
    assert body["tasks"][0]["title"] == SEED_TITLES[first_index]


def test_page_token_first_value_wins(client: FlaskClient) -> None:
    body = list_json(client, "page_size=1&sort_order=created_at_asc&page_token=1&page_token=0")
    assert body["tasks"][0]["title"] == SEED_TITLES[1]


@pytest.mark.parametrize("query", ["page_token=-1", "page_token=-2&page_size=1", "page_token=-1&status=nope",
                                   "page_token=-9223372036854775808"])
def test_negative_page_token_is_empty_500_with_cors(client: FlaskClient, query: str) -> None:
    resp = lst(client, query)
    assert_empty(resp, 500)
    assert resp.status == "500 Internal Server Error"
    assert resp.headers["Content-Length"] == "0"
    # The panic is recovered: the app keeps serving.
    assert list_json(client)["total_count"] == 2


def test_huge_page_token_wraps_end_index_like_int64(client: FlaskClient) -> None:
    resp = lst(client, "page_size=100&page_token=9223372036854775807")
    assert_json(resp, 200)
    assert resp.data == b'{"tasks":null,"next_page_token":"-9223372036854775709","total_count":2}'


# -- filters -------------------------------------------------------------------


@pytest.fixture
def mixed(client: FlaskClient) -> dict[str, dict]:  # type: ignore[type-arg]
    tasks = {
        "a": create(client, {"title": "a", "tags": ["x", "y"], "assigned_to": "alice"}),
        "b": create(client, {"title": "b", "tags": ["x"], "assigned_to": "bob"}),
        "c": create(client, {"title": "c", "tags": ["y", "z", ""], "assigned_to": "Alice"}),
        "d": create(client, {"title": "d", "tags": ["x y"], "assigned_to": ""}),
        "e": create(client, {"title": "e"}),
    }
    client.patch(f"{TASKS}/{tasks['a']['id']}/status", data='{"status":"completed"}')
    client.patch(f"{TASKS}/{tasks['b']['id']}/status", data='{"status":"in_progress"}')
    client.patch(f"{TASKS}/{tasks['c']['id']}/status", data='{"status":"completed"}')
    return tasks


def titles(client: FlaskClient, query: str) -> list[str]:
    body = list_json(client, f"page_size=100&sort_order=created_at_asc&{query}")
    assert body["total_count"] == len(body["tasks"] or [])
    return [t["title"] for t in body["tasks"] or []]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("status=completed", ["a", "c"]),
        ("status=in_progress", ["b"]),
        ("status=pending", [*SEED_TITLES, "d", "e"]),
        ("status=COMPLETED", []),
        ("status=cancelled", []),
        ("status=", [*SEED_TITLES, "a", "b", "c", "d", "e"]),
        ("assigned_to=alice", ["a"]),
        ("assigned_to=Alice", ["c"]),
        ("assigned_to=", [*SEED_TITLES, "a", "b", "c", "d", "e"]),
        ("assigned_to=nobody", []),
        ("tags=x", ["a", "b"]),
        ("tags=x,y", ["a"]),
        ("tags=y,x", ["a"]),
        ("tags=%20x%20,%09y%0A", ["a"]),
        ("tags=x+y", ["d"]),
        ("tags=X", []),
        ("tags=y,", ["c"]),
        ("tags=,", ["c"]),
        ("tags=go", list(SEED_TITLES)),
        ("tags=go,%20grpc", [SEED_TITLES[1]]),
        ("tags=x&tags=z", ["a", "b"]),
        ("tags=z&tags=x", ["c"]),
        ("tags=x,%zz", [*SEED_TITLES, "a", "b", "c", "d", "e"]),
        ("status=completed&assigned_to=alice", ["a"]),
        ("status=completed&tags=z", ["c"]),
        ("status=pending&tags=x", []),
        ("Status=completed", [*SEED_TITLES, "a", "b", "c", "d", "e"]),
    ],
)
def test_filters(client: FlaskClient, mixed: dict, query: str, expected: list[str]) -> None:  # type: ignore[type-arg]
    assert titles(client, query) == expected


def test_total_count_is_after_filtering_before_paging(client: FlaskClient, mixed: dict) -> None:  # type: ignore[type-arg]
    resp = lst(client, "status=pending&page_size=1&sort_order=created_at_asc")
    body = assert_json(resp, 200)
    assert [t["title"] for t in body["tasks"]] == [SEED_TITLES[0]]
    assert body["next_page_token"] == "1" and body["total_count"] == 4
    body = list_json(client, "status=pending&page_size=1&page_token=3&sort_order=created_at_asc")
    assert [t["title"] for t in body["tasks"]] == ["e"] and "next_page_token" not in body


def test_assigned_to_plus_is_space(client: FlaskClient) -> None:
    create(client, {"title": "sp", "assigned_to": "a b"})
    assert titles(client, "assigned_to=a+b") == ["sp"]
    assert titles(client, "assigned_to=a%20b") == ["sp"]


def test_filter_values_unicode(client: FlaskClient) -> None:
    create(client, {"title": "u", "tags": ["café"], "assigned_to": "zoë"})
    assert titles(client, "tags=caf%C3%A9&assigned_to=zo%C3%AB") == ["u"]


def test_tags_trimmed_with_go_unicode_space(client: FlaskClient) -> None:
    create(client, {"title": "nb", "tags": ["k"]})
    assert titles(client, "tags=%C2%A0k%E3%80%80") == ["nb"]  # NBSP / ideographic space are trimmed


# -- sorting -------------------------------------------------------------------


@pytest.fixture
def sortable(client: FlaskClient) -> None:
    for task_id in seed_ids(client).values():
        client.delete(f"{TASKS}/{task_id}")
    for title, priority, due in [
        ("p-low", "low", "2030-01-03T00:00:00Z"),
        ("p-crit", "critical", None),
        ("p-med", "medium", "2030-01-01T00:00:00Z"),
        ("p-high", "high", "2030-01-02T05:00:00+05:30"),  # = 2030-01-01T23:30:00Z
        ("p-none", "medium", None),
    ]:
        body: dict[str, object] = {"title": title, "priority": priority}
        if due:
            body["due_date"] = due
        create(client, body)


def sorted_titles(client: FlaskClient, order: str) -> list[str]:
    return [t["title"] for t in list_json(client, f"sort_order={order}")["tasks"]]


def test_sort_created_at_asc_and_desc(client: FlaskClient, sortable: None) -> None:
    asc = ["p-low", "p-crit", "p-med", "p-high", "p-none"]
    assert sorted_titles(client, "created_at_asc") == asc
    assert sorted_titles(client, "created_at_desc") == asc[::-1]


def test_sort_due_date_asc_offset_aware_nil_last(client: FlaskClient, sortable: None) -> None:
    got = sorted_titles(client, "due_date_asc")
    assert got[:3] == ["p-med", "p-high", "p-low"]
    assert sorted(got[3:]) == ["p-crit", "p-none"]


def test_sort_due_date_desc_nil_still_last(client: FlaskClient, sortable: None) -> None:
    got = sorted_titles(client, "due_date_desc")
    assert got[:3] == ["p-low", "p-high", "p-med"]
    assert sorted(got[3:]) == ["p-crit", "p-none"]


def test_sort_priority_asc_and_desc(client: FlaskClient, sortable: None) -> None:
    rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    for order, reverse in (("priority_asc", False), ("priority_desc", True)):
        tasks = list_json(client, f"sort_order={order}")["tasks"]
        values = [rank[t["priority"]] for t in tasks]
        assert values == sorted(values, reverse=reverse)
    assert sorted_titles(client, "priority_asc")[0] == "p-low"
    assert sorted_titles(client, "priority_desc")[0] == "p-crit"


@pytest.mark.parametrize("order", ["", "bogus", "CREATED_AT_ASC", "priority", "created_at_asc%20"])
def test_unknown_sort_order_returns_all_unsorted(client: FlaskClient, sortable: None, order: str) -> None:
    body = list_json(client, f"sort_order={order}")
    assert body["total_count"] == 5
    assert sorted(t["title"] for t in body["tasks"]) == ["p-crit", "p-high", "p-low", "p-med", "p-none"]


def test_sort_then_paginate(client: FlaskClient, sortable: None) -> None:
    body = list_json(client, "sort_order=due_date_asc&page_size=2&page_token=1")
    assert [t["title"] for t in body["tasks"]] == ["p-high", "p-low"]
    assert body["next_page_token"] == "3" and body["total_count"] == 5


# -- routing specifics ------------------------------------------------------------


def test_list_trailing_slash_redirect_keeps_query(client: FlaskClient) -> None:
    assert_redirect(client.get(f"{TASKS}/"), 301, TASKS)
    assert_redirect(client.get(f"{TASKS}/?status=pending&page_size=1"), 301,
                    f"{TASKS}?status=pending&page_size=1")


def test_list_ignores_request_body(client: FlaskClient) -> None:
    assert list_json(client, "") == json.loads(client.get(TASKS, data=b"{bad").data)


def test_all_titles_helper_sanity(client: FlaskClient) -> None:
    assert sorted(all_titles(client)) == sorted(SEED_TITLES)


def test_tags_not_trimmed_for_non_go_space(client: FlaskClient) -> None:
    """U+001C is whitespace for ``str.strip`` but not for Go's ``strings.TrimSpace``."""
    create(client, {"title": "fs", "tags": ["k"]})
    assert titles(client, "tags=%1Ck") == []
