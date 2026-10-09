"""Port of internal/services/task_service.go: the in-memory task store.

Errors carry the exact Go ``err.Error()`` text (``str(exc)``) so that handlers can map
them the way ``handlers.go`` does (string comparison / "invalid" substring).
"""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Callable
from functools import cmp_to_key

from ..go_compat import GoNumError, GoTime, go_atoi
from ..models import (
    CreateTaskRequest,
    ListTasksQuery,
    ListTasksResponse,
    SortOrder,
    Task,
    TaskPriority,
    TaskStatus,
    UpdateTaskRequest,
)

__all__ = [
    "TaskService",
    "TaskError",
    "TaskNotFoundError",
    "InvalidStatusError",
    "InvalidPriorityError",
    "SliceBoundsPanic",
    "go_trim_space",
]


class TaskError(Exception):
    """A Go ``error`` returned by the service; ``str()`` is the Go message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class TaskNotFoundError(TaskError):
    """``task with ID <id> not found``."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"task with ID {task_id} not found")
        self.task_id = task_id


class InvalidStatusError(TaskError):
    """``invalid status: <status>``."""

    def __init__(self, status: str) -> None:
        super().__init__(f"invalid status: {status}")


class InvalidPriorityError(TaskError):
    """``invalid priority: <priority>``."""

    def __init__(self, priority: str) -> None:
        super().__init__(f"invalid priority: {priority}")


class SliceBoundsPanic(IndexError):
    """Emulates the Go runtime panic of ``tasks[start:end]`` with a negative start.

    Not a ``TaskError``: Gin's recovery middleware turns the panic into an empty 500.
    """

    def __init__(self, start: int, end: int) -> None:
        super().__init__(f"runtime error: slice bounds out of range [{start}:{end}]")


# Go unicode.IsSpace (Latin-1 + White_Space), used by strings.TrimSpace.
_GO_SPACE = (
    "\t\n\v\f\r \x85\xa0        "
    "        　"
)


def go_trim_space(s: str) -> str:
    """Go ``strings.TrimSpace`` (differs from ``str.strip`` on e.g. U+001C..U+001F)."""
    return s.strip(_GO_SPACE)


def _wrap_int64(n: int) -> int:
    """Go int64 two's-complement wraparound (``startIndex + pageSize`` can overflow)."""
    return (n + (1 << 63)) % (1 << 64) - (1 << 63)


_Less = Callable[[Task, Task], bool]


def _due_less(after: bool) -> _Less:
    """Go's due-date comparator: nil due dates sort last in both directions."""

    def less(a: Task, b: Task) -> bool:
        if a.due_date is None:
            return False
        if b.due_date is None:
            return True
        return a.due_date.after(b.due_date) if after else a.due_date.before(b.due_date)

    return less


_SORTS: dict[str, _Less] = {
    SortOrder.CREATED_AT_ASC: lambda a, b: a.created_at.before(b.created_at),
    SortOrder.CREATED_AT_DESC: lambda a, b: a.created_at.after(b.created_at),
    SortOrder.DUE_DATE_ASC: _due_less(after=False),
    SortOrder.DUE_DATE_DESC: _due_less(after=True),
    SortOrder.PRIORITY_ASC: lambda a, b: (
        TaskPriority.get_value(a.priority) < TaskPriority.get_value(b.priority)
    ),
    SortOrder.PRIORITY_DESC: lambda a, b: (
        TaskPriority.get_value(a.priority) > TaskPriority.get_value(b.priority)
    ),
}


class TaskService:
    """Go ``services.TaskService``: an RLock-guarded, insertion-ordered ``id -> Task`` map.

    Methods return the stored :class:`Task` objects (Go returns pointers), except
    :meth:`list_tasks`, which returns shallow copies (Go copies ``*task`` values).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._initialize_sample_data()

    def _initialize_sample_data(self) -> None:
        for req in (
            CreateTaskRequest(
                title="Implement Go REST API",
                description="Create REST API with Gin framework",
                priority=TaskPriority.HIGH.value,
                tags=["go", "rest", "api"],
            ),
            CreateTaskRequest(
                title="Add gRPC support",
                description="Implement gRPC server with native Go support",
                priority=TaskPriority.MEDIUM.value,
                tags=["go", "grpc", "protobuf"],
            ),
        ):
            self.create_task(req)

    # -- queries ------------------------------------------------------------

    def list_tasks(self, query: ListTasksQuery) -> ListTasksResponse:
        """Go ``ListTasks``: filter, sort, paginate.

        Raises :class:`SliceBoundsPanic` for a negative ``page_token`` (Go panics).
        """
        with self._lock:
            tasks = self._filter_tasks(list(self._tasks.values()), query)
            self._sort_tasks(tasks, query.sort_order)

            total = len(tasks)
            page_size = query.page_size
            if page_size <= 0:
                page_size = 20
            if page_size > 100:
                page_size = 100

            start = 0
            if query.page_token != "":
                try:
                    start = go_atoi(query.page_token)
                except GoNumError:
                    pass

            end = _wrap_int64(start + page_size)
            if end > total:
                end = total

            page: list[Task] | None = None
            if start < total:
                if start < 0:
                    raise SliceBoundsPanic(start, end)
                page = [copy.copy(t) for t in tasks[start:end]] or None

            next_token = str(end) if end < total else ""
            return ListTasksResponse(tasks=page, next_page_token=next_token, total_count=total)

    def get_task(self, task_id: str) -> Task:
        """Go ``GetTask``; raises :class:`TaskNotFoundError`."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            return task

    # -- commands -----------------------------------------------------------

    def create_task(self, req: CreateTaskRequest) -> Task:
        """Go ``CreateTask``; raises :class:`InvalidPriorityError` (task not stored)."""
        with self._lock:
            now = GoTime.now()
            task = Task(
                id=str(uuid.uuid4()),
                title=req.title,
                description=req.description,
                status=TaskStatus.PENDING.value,
                priority=req.priority,
                tags=req.tags,
                created_by="system",
                assigned_to=req.assigned_to,
                created_at=now,
                updated_at=now,
                due_date=req.due_date,
            )
            if task.priority == "":
                task.priority = TaskPriority.MEDIUM.value
            if not TaskPriority.is_valid(task.priority):
                raise InvalidPriorityError(task.priority)
            if task.tags is None:
                task.tags = []
            self._tasks[task.id] = task
            return task

    def update_task(self, task_id: str, req: UpdateTaskRequest) -> Task:
        """Go ``UpdateTask``: apply fields in Go order, mutating the stored task in place.

        Lookup happens first (:class:`TaskNotFoundError`). An invalid status/priority
        raises :class:`InvalidStatusError`/:class:`InvalidPriorityError` *after* the
        earlier fields were applied, and without bumping ``updated_at``.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)

            if req.title is not None:
                task.title = req.title
            if req.description is not None:
                task.description = req.description
            if req.status is not None:
                if not TaskStatus.is_valid(req.status):
                    raise InvalidStatusError(req.status)
                task.status = req.status
                if task.status == TaskStatus.COMPLETED and task.completed_at is None:
                    task.completed_at = GoTime.now()
            if req.priority is not None:
                if not TaskPriority.is_valid(req.priority):
                    raise InvalidPriorityError(req.priority)
                task.priority = req.priority
            if req.tags is not None:
                task.tags = req.tags
            if req.assigned_to is not None:
                task.assigned_to = req.assigned_to
            if req.due_date is not None:
                task.due_date = req.due_date

            task.updated_at = GoTime.now()
            return task

    def update_task_status(self, task_id: str, status: str) -> Task:
        """Go ``UpdateTaskStatus``: ``UpdateTask`` with only ``status`` set."""
        return self.update_task(task_id, UpdateTaskRequest(status=status))

    def delete_task(self, task_id: str) -> None:
        """Go ``DeleteTask``; raises :class:`TaskNotFoundError`."""
        with self._lock:
            if task_id not in self._tasks:
                raise TaskNotFoundError(task_id)
            del self._tasks[task_id]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _filter_tasks(tasks: list[Task], query: ListTasksQuery) -> list[Task]:
        wanted_tags = (
            [go_trim_space(t) for t in query.tags.split(",")] if query.tags != "" else []
        )
        filtered: list[Task] = []
        for task in tasks:
            if query.status != "" and task.status != query.status:
                continue
            if query.assigned_to != "" and (
                task.assigned_to is None or task.assigned_to != query.assigned_to
            ):
                continue
            task_tags = task.tags or []
            if any(tag not in task_tags for tag in wanted_tags):
                continue
            filtered.append(task)
        return filtered

    @staticmethod
    def _sort_tasks(tasks: list[Task], order: str) -> None:
        """Sort in place (stable); unknown ``order`` leaves insertion order."""
        less = _SORTS.get(order)
        if less is None:
            return

        def cmp(a: Task, b: Task) -> int:
            if less(a, b):
                return -1
            if less(b, a):
                return 1
            return 0

        tasks.sort(key=cmp_to_key(cmp))
