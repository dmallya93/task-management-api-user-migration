"""GET /api/v1/tasks: filters, sorting, pagination, query validation."""
import pytest
import requests

from conftest import TASKS, TIMEOUT

TAG = "ftlist"


@pytest.fixture
def dataset(make_task):
    """Three namespaced tasks (tag ftlist) with distinct priorities/due dates; deleted on teardown."""
    a = make_task(title="ft A", priority="low", tags=[TAG, "alpha"], assigned_to="ann",
                  due_date="2032-03-01T00:00:00Z").json()
    b = make_task(title="ft B", priority="critical", tags=[TAG, "beta"],
                  due_date="2032-01-01T00:00:00Z").json()
    c = make_task(title="ft C", priority="high", tags=[TAG, "alpha", "beta"], assigned_to="ann").json()
    return a, b, c


def titles(r):
    return [t["title"] for t in (r.json()["tasks"] or [])]


class TestListFilters:
    def test_filter_tags_all_of_trimmed(self, observe, dataset):
        r = observe(requests.get(TASKS, params={"tags": f"{TAG}, alpha", "sort_order": "created_at_asc"},
                                 timeout=TIMEOUT))
        assert r.status_code == 200
        assert titles(r) == ["ft A", "ft C"] and r.json()["total_count"] == 2

    def test_filter_assigned_to(self, observe, dataset):
        r = observe(requests.get(TASKS, params={"tags": TAG, "assigned_to": "ann", "sort_order": "created_at_desc"},
                                 timeout=TIMEOUT))
        assert titles(r) == ["ft C", "ft A"]

    def test_filter_status(self, observe, dataset):
        requests.patch(f"{TASKS}/{dataset[1]['id']}/status", json={"status": "in_progress"}, timeout=TIMEOUT)
        r = observe(requests.get(TASKS, params={"tags": TAG, "status": "in_progress"}, timeout=TIMEOUT))
        assert titles(r) == ["ft B"]

    def test_filter_no_match_null_tasks(self, observe, dataset):
        r = observe(requests.get(TASKS, params={"status": "bogus"}, timeout=TIMEOUT))
        assert r.status_code == 200
        assert r.text == '{"tasks":null,"total_count":0}'


class TestListSort:
    @pytest.mark.parametrize("order,expected", [
        ("created_at_asc", ["ft A", "ft B", "ft C"]),
        ("created_at_desc", ["ft C", "ft B", "ft A"]),
        ("priority_asc", ["ft A", "ft C", "ft B"]),
        ("priority_desc", ["ft B", "ft C", "ft A"]),
        ("due_date_asc", ["ft B", "ft A", "ft C"]),
        ("due_date_desc", ["ft A", "ft B", "ft C"]),
    ])
    def test_sort_orders(self, observe, dataset, order, expected):
        r = observe(requests.get(TASKS, params={"tags": TAG, "sort_order": order}, timeout=TIMEOUT))
        assert r.status_code == 200
        assert titles(r) == expected


class TestListPagination:
    def test_paginate(self, observe, dataset):
        p = {"tags": TAG, "sort_order": "created_at_asc", "page_size": 2}
        r1 = observe(requests.get(TASKS, params=p, timeout=TIMEOUT), "page1")
        assert titles(r1) == ["ft A", "ft B"]
        assert r1.json()["next_page_token"] == "2" and r1.json()["total_count"] == 3
        r2 = observe(requests.get(TASKS, params={**p, "page_token": "2"}, timeout=TIMEOUT), "page2")
        assert titles(r2) == ["ft C"] and "next_page_token" not in r2.json()
        r3 = observe(requests.get(TASKS, params={**p, "page_token": "10"}, timeout=TIMEOUT), "beyond")
        assert r3.json()["tasks"] is None and r3.json()["total_count"] == 3
        r4 = observe(requests.get(TASKS, params={**p, "page_token": "abc"}, timeout=TIMEOUT), "nonnumeric")
        assert titles(r4) == ["ft A", "ft B"]

    def test_negative_page_token_500(self, observe):
        r = observe(requests.get(TASKS, params={"page_token": "-1"}, timeout=TIMEOUT))
        assert r.status_code == 500
        assert r.text == ""
        assert r.headers.get("Access-Control-Allow-Origin") == "*"

    @pytest.mark.parametrize("size,msg", [
        ("0", "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'min' tag"),
        ("101", "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'max' tag"),
        ("abc", 'strconv.ParseInt: parsing "abc": invalid syntax'),
    ])
    def test_page_size_invalid(self, observe, size, msg):
        r = observe(requests.get(TASKS, params={"page_size": size}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.json() == {"error": {"code": "VALIDATION_ERROR", "message": msg}}

    def test_page_size_boundaries(self, observe, dataset):
        r1 = observe(requests.get(TASKS, params={"tags": TAG, "page_size": 1, "sort_order": "created_at_asc"},
                                  timeout=TIMEOUT), "size1")
        assert r1.status_code == 200 and titles(r1) == ["ft A"] and r1.json()["next_page_token"] == "1"
        r2 = observe(requests.get(TASKS, params={"tags": TAG, "page_size": 100, "sort_order": "created_at_asc"},
                                  timeout=TIMEOUT), "size100")
        assert r2.status_code == 200 and len(titles(r2)) == 3
