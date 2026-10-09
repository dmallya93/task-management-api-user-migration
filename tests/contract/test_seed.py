"""Seed data created by NewTaskService (internal/services/task_service.go)."""

from __future__ import annotations

import pytest

from tests.contract.helpers import TASKS, UUID4_RE, Client, assert_json, task_body

SEEDS = {
    "Implement Go REST API": dict(
        description="Create REST API with Gin framework", priority="high", tags=["go", "rest", "api"],
    ),
    "Add gRPC support": dict(
        description="Implement gRPC server with native Go support", priority="medium",
        tags=["go", "grpc", "protobuf"],
    ),
}


def _seed(client: Client, title: str) -> dict:
    matches = [t for t in client.iter_all() if t["title"] == title]
    assert len(matches) == 1, f"seed task {title!r} missing or duplicated"
    return matches[0]


@pytest.mark.parametrize("title", list(SEEDS))
def test_seed_task_listed_with_expected_fields(client: Client, title: str) -> None:
    task = _seed(client, title)
    expected = SEEDS[title]
    assert UUID4_RE.match(task["id"])
    assert task["description"] == expected["description"]
    assert task["priority"] == expected["priority"]
    assert task["tags"] == expected["tags"]
    assert task["status"] == "pending"
    assert task["created_by"] == "system"
    assert task["created_at"] == task["updated_at"]
    for absent in ("assigned_to", "due_date", "completed_at"):
        assert absent not in task


@pytest.mark.parametrize("title", list(SEEDS))
def test_seed_task_raw_serialization(client: Client, title: str) -> None:
    task_id = _seed(client, title)["id"]
    assert_json(client.get(f"{TASKS}/{task_id}"), 200, task_body(title=title, **SEEDS[title]),
                check_now=False)  # seeds are as old as the server process


def test_seed_tasks_found_by_tag_filter(client: Client) -> None:
    titles = {t["title"] for t in client.iter_all(tags="go")}
    assert set(SEEDS) <= titles
    titles = {t["title"] for t in client.iter_all(tags="go, grpc")}
    assert "Add gRPC support" in titles and "Implement Go REST API" not in titles
