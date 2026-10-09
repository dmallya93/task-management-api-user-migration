"""Handler error mapping from handlers.go, including branches the real service never takes.

``listTasks``/``updateTask``/``updateTaskStatus`` map service errors by message: an exact
"task with ID <id> not found" is 404, (PUT only) anything containing "invalid" is 400, and
everything else is 500 ``INTERNAL_ERROR``. The in-memory service only produces the first two,
so the 500 branches are driven by replacing service methods on this test's own app.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from task_api.handlers import TASK_SERVICE_KEY
from task_api.services.task_service import TaskError
from tests.api.helpers import TASKS, assert_empty, assert_error, assert_validation, create, get


def _raise(message: str) -> Any:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise TaskError(message)

    return fail


def _service(app: Flask) -> Any:
    return app.extensions[TASK_SERVICE_KEY]


def test_list_service_error_is_500_internal_error(app: Flask, client: FlaskClient,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service(app), "list_tasks", _raise("store unavailable"))
    assert_error(client.get(TASKS), 500, "INTERNAL_ERROR", "store unavailable")


def test_list_validation_error_precedes_service(app: Flask, client: FlaskClient,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service(app), "list_tasks", _raise("never reached"))
    assert_validation(client.get(f"{TASKS}?page_size=0"),
                      "Key: 'ListTasksQuery.PageSize' Error:Field validation for 'PageSize' failed on the 'min' tag")


@pytest.mark.parametrize(
    ("message", "status", "code"),
    [
        ("task with ID x not found", 404, "NOT_FOUND"),
        ("task with ID y not found", 500, "INTERNAL_ERROR"),  # exact comparison with the path id
        ("value is invalid here", 400, "VALIDATION_ERROR"),
        ("Invalid with capital", 500, "INTERNAL_ERROR"),  # strings.Contains is case-sensitive
        ("disk full", 500, "INTERNAL_ERROR"),
    ],
)
def test_update_error_mapping(app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
                              message: str, status: int, code: str) -> None:
    monkeypatch.setattr(_service(app), "update_task", _raise(message))
    assert_error(client.put(f"{TASKS}/x", data="{}"), status, code, message)


@pytest.mark.parametrize(
    ("message", "status", "code"),
    [
        ("task with ID x not found", 404, "NOT_FOUND"),
        ("invalid status: pending", 500, "INTERNAL_ERROR"),  # PATCH has no "invalid" branch
        ("disk full", 500, "INTERNAL_ERROR"),
    ],
)
def test_update_status_error_mapping(app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
                                     message: str, status: int, code: str) -> None:
    monkeypatch.setattr(_service(app), "update_task_status", _raise(message))
    assert_error(client.patch(f"{TASKS}/x/status", data='{"status":"pending"}'), status, code, message)


def test_create_service_error_is_400(app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service(app), "create_task", _raise("anything at all"))
    assert_validation(client.post(TASKS, data='{"title":"t"}'), "anything at all")


@pytest.mark.parametrize(("method", "path"), [("GET", f"{TASKS}/x"), ("DELETE", f"{TASKS}/x")])
def test_get_and_delete_service_errors_are_404(app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
                                               method: str, path: str) -> None:
    name = "get_task" if method == "GET" else "delete_task"
    monkeypatch.setattr(_service(app), name, _raise("gone"))
    assert_error(client.open(path, method=method), 404, "NOT_FOUND", "gone")


def test_unexpected_exception_is_recovered_as_empty_500(app: Flask, client: FlaskClient,
                                                        monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*_args: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(_service(app), "get_task", boom)
    resp = client.get(f"{TASKS}/x")
    assert_empty(resp, 500)
    assert resp.headers["Content-Length"] == "0"
    assert "[Recovery] panic recovered" in capsys.readouterr().err
    monkeypatch.undo()
    task = create(client, {"title": "still serving"})
    assert get(client, task["id"])["title"] == "still serving"


def test_negative_page_token_panic_is_logged(client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
    assert_empty(client.get(f"{TASKS}?page_token=-1"), 500)
    assert "slice bounds out of range [-1:2]" in capsys.readouterr().err
