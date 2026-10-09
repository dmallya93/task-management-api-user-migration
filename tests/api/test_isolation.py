"""Each ``create_app()`` owns a separate, freshly seeded store."""

from __future__ import annotations

from flask.testing import FlaskClient

from task_api import create_app
from tests.api.helpers import SEED_TITLES, TASKS, assert_empty, assert_error, create, get, list_json, seed_ids


def test_apps_do_not_share_tasks() -> None:
    a, b = create_app().test_client(), create_app().test_client()
    task = create(a, {"title": "only in a"})
    assert_error(b.get(f"{TASKS}/{task['id']}"), 404, "NOT_FOUND", f"task with ID {task['id']} not found")
    assert list_json(a)["total_count"] == 3
    assert list_json(b)["total_count"] == 2


def test_each_app_has_its_own_seed_ids() -> None:
    a, b = create_app().test_client(), create_app().test_client()
    ids_a, ids_b = seed_ids(a), seed_ids(b)
    assert set(ids_a) == set(ids_b) == set(SEED_TITLES)
    assert not set(ids_a.values()) & set(ids_b.values())
    for task_id in ids_a.values():
        assert_empty(a.delete(f"{TASKS}/{task_id}"), 204)
    assert list_json(a)["total_count"] == 0
    assert list_json(b)["total_count"] == 2


def test_mutation_in_one_app_is_invisible_in_another() -> None:
    a, b = create_app().test_client(), create_app().test_client()
    title = SEED_TITLES[0]
    a_id = seed_ids(a)[title]
    a.put(f"{TASKS}/{a_id}", data='{"title":"renamed","status":"completed"}')
    b_task = get(b, seed_ids(b)[title])
    assert b_task["title"] == title and "completed_at" not in b_task


# The next two tests run in order and rely on the per-test ``app`` fixture.


def test_fixture_app_state_mutated(client: FlaskClient) -> None:
    for task_id in seed_ids(client).values():
        client.delete(f"{TASKS}/{task_id}")
    create(client, {"title": "leaked?"})
    assert list_json(client)["total_count"] == 1


def test_fixture_app_is_fresh_for_next_test(client: FlaskClient) -> None:
    body = list_json(client)
    assert body["total_count"] == 2
    assert sorted(t["title"] for t in body["tasks"]) == sorted(SEED_TITLES)
