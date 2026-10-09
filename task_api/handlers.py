"""Port of rest/server/handlers.go: the seven routes as a Flask Blueprint.

Routing (404s, redirects, OPTIONS) is decided by the Gin router emulation in
:mod:`task_api`. These views only run for a matched route, with ``id`` being the raw
Gin path parameter.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote_to_bytes

from flask import Blueprint, Response, current_app, request

from task_api.gin_router import go_request_target
from task_api.go_compat import GoError, GoTime, go_map
from task_api.models import (
    CreateTaskRequest,
    ErrorResponse,
    ListTasksQuery,
    TaskStatus,
    UpdateTaskRequest,
    UpdateTaskStatusRequest,
)
from task_api.services.task_service import TaskError, TaskService

__all__ = ["bp", "TASK_SERVICE_KEY", "empty_response", "go_parse_query"]

bp = Blueprint("tasks", __name__)

#: ``app.extensions`` key holding the app's :class:`TaskService`.
TASK_SERVICE_KEY = "task_service"

_HEX = frozenset(b"0123456789abcdefABCDEF")


def _service() -> TaskService:
    return current_app.extensions[TASK_SERVICE_KEY]


def _json(obj: Any, status: int = 200) -> Response:
    """Gin ``c.JSON(status, obj)``."""
    resp = current_app.json.response(obj)
    resp.status_code = status
    return resp


def _error(status: int, code: str, message: str) -> Response:
    return _json(ErrorResponse.of(code, message), status)


def _route(rule: str, method: str):  # type: ignore[no-untyped-def]
    return bp.route(rule, methods=[method], provide_automatic_options=False)


def _go_query_unescape(s: bytes) -> bytes | None:
    """Go ``url.QueryUnescape``. Returns ``None`` on an invalid ``%`` escape."""
    i = 0
    while (i := s.find(b"%", i)) >= 0:
        if len(s) - i < 3 or s[i + 1] not in _HEX or s[i + 2] not in _HEX:
            return None
        i += 3
    return unquote_to_bytes(s.replace(b"+", b" "))


def go_parse_query(raw_query: str) -> dict[str, list[str]]:
    """Go ``url.ParseQuery`` as used by ``c.ShouldBindQuery`` (errors ignored).

    Pairs are split on ``&``. A pair containing ``;`` or a bad escape is dropped, and
    ``+`` means space. ``raw_query`` is a byte string (latin-1). Values come back as
    ``surrogateescape`` strings, go_compat's representation of Go byte strings.
    """
    out: dict[str, list[str]] = {}
    for pair in raw_query.encode("latin-1").split(b"&"):
        if b";" in pair or not pair:
            continue
        key, _, value = pair.partition(b"=")
        k = _go_query_unescape(key)
        v = _go_query_unescape(value)
        if k is None or v is None:
            continue
        out.setdefault(k.decode("utf-8", "surrogateescape"), []).append(
            v.decode("utf-8", "surrogateescape")
        )
    return out


def empty_response(status: int) -> Response:
    """A response with no body and no ``Content-Type`` (Gin ``c.Status`` / ``AbortWithStatus``)."""
    resp = Response(b"", status)
    del resp.headers["Content-Type"]
    return resp


@_route("/health", "GET")
def health_check() -> Response:
    """``GET /health``: a ``gin.H`` map, so keys are emitted sorted."""
    return _json(
        go_map(
            status="healthy",
            timestamp=GoTime.now().format_rfc3339(),
            service="task-api-go",
            version="1.0.0",
        )
    )


@_route("/api/v1/tasks", "GET")
def list_tasks() -> Response:
    """``GET /api/v1/tasks``."""
    try:
        query = ListTasksQuery.from_query(go_parse_query(go_request_target(request.environ).raw_query))
    except GoError as err:
        return _error(400, "VALIDATION_ERROR", str(err))

    if query.page_size == 0:
        query.page_size = 20

    try:
        response = _service().list_tasks(query)
    except TaskError as err:
        return _error(500, "INTERNAL_ERROR", str(err))
    return _json(response)


@_route("/api/v1/tasks/<id>", "GET")
def get_task(id: str) -> Response:  # noqa: A002 - Gin param name
    """``GET /api/v1/tasks/:id``."""
    try:
        task = _service().get_task(id)
    except TaskError as err:
        return _error(404, "NOT_FOUND", str(err))
    return _json(task)


@_route("/api/v1/tasks", "POST")
def create_task() -> Response:
    """``POST /api/v1/tasks``: 201 with a ``Location`` header."""
    try:
        req = CreateTaskRequest.from_json(request.get_data())
    except GoError as err:
        return _error(400, "VALIDATION_ERROR", str(err))

    try:
        task = _service().create_task(req)
    except TaskError as err:
        return _error(400, "VALIDATION_ERROR", str(err))

    resp = _json(task, 201)
    resp.headers["Location"] = "/api/v1/tasks/" + task.id
    return resp


@_route("/api/v1/tasks/<id>", "PUT")
def update_task(id: str) -> Response:  # noqa: A002
    """``PUT /api/v1/tasks/:id``."""
    try:
        req = UpdateTaskRequest.from_json(request.get_data())
    except GoError as err:
        return _error(400, "VALIDATION_ERROR", str(err))

    try:
        task = _service().update_task(id, req)
    except TaskError as err:
        message = str(err)
        status, code = 500, "INTERNAL_ERROR"
        if message == f"task with ID {id} not found":
            status, code = 404, "NOT_FOUND"
        elif "invalid" in message:
            status, code = 400, "VALIDATION_ERROR"
        return _error(status, code, message)
    return _json(task)


@_route("/api/v1/tasks/<id>/status", "PATCH")
def update_task_status(id: str) -> Response:  # noqa: A002
    """``PATCH /api/v1/tasks/:id/status``: status validity is checked before the lookup."""
    try:
        req = UpdateTaskStatusRequest.from_json(request.get_data())
    except GoError as err:
        return _error(400, "VALIDATION_ERROR", str(err))

    if not TaskStatus.is_valid(req.status):
        return _error(400, "VALIDATION_ERROR", f"invalid status: {req.status}")

    try:
        task = _service().update_task_status(id, req.status)
    except TaskError as err:
        message = str(err)
        status, code = 500, "INTERNAL_ERROR"
        if message == f"task with ID {id} not found":
            status, code = 404, "NOT_FOUND"
        return _error(status, code, message)
    return _json(task)


@_route("/api/v1/tasks/<id>", "DELETE")
def delete_task(id: str) -> Response:  # noqa: A002
    """``DELETE /api/v1/tasks/:id``: 204 with no body (``c.Status``)."""
    try:
        _service().delete_task(id)
    except TaskError as err:
        return _error(404, "NOT_FOUND", str(err))
    return empty_response(204)
