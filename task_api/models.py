"""Port of internal/models/task.go: enums, the Task entity, request DTOs and responses.

Go's ``TaskStatus``/``TaskPriority``/``SortOrder`` are string aliases, so any string can
flow through a request or query filter unvalidated. The Python ports therefore keep plain
``str`` values on the models; the ``StrEnum`` classes provide the constants and the
``IsValid``/``GetValue`` helpers.

Request DTOs follow Design Decision 1: a Go pointer / nil slice is ``None`` for both an
absent key and an explicit JSON ``null``; ``""`` and ``[]`` are kept as real values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Self

from .go_compat import BindField, GoTime, GoValidationError, bind_json, go_parse_int

__all__ = [
    "TaskStatus",
    "TaskPriority",
    "SortOrder",
    "Task",
    "CreateTaskRequest",
    "UpdateTaskRequest",
    "UpdateTaskStatusRequest",
    "ListTasksQuery",
    "ListTasksResponse",
    "ErrorDetail",
    "ErrorResponse",
]

ValidationFailure = tuple[str, str, str]  # (struct, field, tag) for GoValidationError


# ---------------------------------------------------------------------------
# Enums (Go string aliases)
# ---------------------------------------------------------------------------


class TaskStatus(StrEnum):
    """Go ``models.TaskStatus``."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ON_HOLD = "on_hold"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Go ``TaskStatus.IsValid`` (case-sensitive; ``""`` is invalid)."""
        return status in cls._value2member_map_


class TaskPriority(StrEnum):
    """Go ``models.TaskPriority``."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def is_valid(cls, priority: str) -> bool:
        """Go ``TaskPriority.IsValid`` (case-sensitive; ``""`` is invalid)."""
        return priority in cls._value2member_map_

    @classmethod
    def get_value(cls, priority: str) -> int:
        """Go ``TaskPriority.GetValue``: low=1 .. critical=4, anything else 2."""
        return _PRIORITY_VALUES.get(priority, 2)


_PRIORITY_VALUES: dict[str, int] = {
    TaskPriority.LOW: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.HIGH: 3,
    TaskPriority.CRITICAL: 4,
}


class SortOrder(StrEnum):
    """Go ``models.SortOrder``; any other value means "no sorting"."""

    CREATED_AT_ASC = "created_at_asc"
    CREATED_AT_DESC = "created_at_desc"
    DUE_DATE_ASC = "due_date_asc"
    DUE_DATE_DESC = "due_date_desc"
    PRIORITY_ASC = "priority_asc"
    PRIORITY_DESC = "priority_desc"


# ---------------------------------------------------------------------------
# Task entity
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """Go ``models.Task``. Mutable: the service updates stored tasks in place."""

    id: str
    title: str
    description: str
    status: str
    priority: str
    tags: list[str] | None
    created_by: str
    assigned_to: str | None
    created_at: GoTime
    updated_at: GoTime
    due_date: GoTime | None = None
    completed_at: GoTime | None = None

    def to_go(self) -> dict[str, Any]:
        """JSON object in Go struct order; ``*T,omitempty`` keys dropped only when nil."""
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "priority": str(self.priority),
            "tags": self.tags,
            "created_by": self.created_by,
        }
        if self.assigned_to is not None:
            out["assigned_to"] = self.assigned_to
        out["created_at"] = self.created_at
        out["updated_at"] = self.updated_at
        if self.due_date is not None:
            out["due_date"] = self.due_date
        if self.completed_at is not None:
            out["completed_at"] = self.completed_at
        return out


# ---------------------------------------------------------------------------
# Request DTOs (Gin ShouldBindJSON = bind_json + binding-tag validation)
# ---------------------------------------------------------------------------


class _JSONRequest:
    """Shared Gin ``ShouldBindJSON`` behaviour for the body DTOs."""

    GO_STRUCT: ClassVar[str]
    BIND_FIELDS: ClassVar[tuple[BindField, ...]]

    @classmethod
    def from_bound(cls, bound: Mapping[str, Any]) -> Self:
        """Build from :func:`bind_json` output (JSON name -> decoded non-null value)."""
        return cls(**dict(bound))  # type: ignore[call-arg]

    @classmethod
    def bind(cls, raw: bytes) -> Self:
        """Decode ``raw`` like Go's ``json.Unmarshal`` into this struct (no validation).

        Raises the binder's :class:`~task_api.go_compat.GoError` subclasses.
        """
        return cls.from_bound(bind_json(raw, cls.GO_STRUCT, cls.BIND_FIELDS))

    @classmethod
    def from_json(cls, raw: bytes) -> Self:
        """Gin ``c.ShouldBindJSON``: decode, then validate the ``binding`` tags.

        Raises a ``GoError`` (decode error) or :class:`GoValidationError`; ``str(err)``
        is the exact Gin message for the 400 ``VALIDATION_ERROR`` response.
        """
        req = cls.bind(raw)
        req.validate()
        return req

    def validation_errors(self) -> list[ValidationFailure]:
        raise NotImplementedError

    def validate(self) -> None:
        """Raise :class:`GoValidationError` if any ``binding`` tag fails."""
        errors = self.validation_errors()
        if errors:
            raise GoValidationError(errors)


@dataclass
class CreateTaskRequest(_JSONRequest):
    """Go ``models.CreateTaskRequest``; ``title`` is ``binding:"required,min=1,max=200"``."""

    title: str = ""
    description: str = ""
    priority: str = ""
    tags: list[str] | None = None
    assigned_to: str | None = None
    due_date: GoTime | None = None

    GO_STRUCT: ClassVar[str] = "CreateTaskRequest"
    BIND_FIELDS: ClassVar[tuple[BindField, ...]] = (
        # Go value fields (not pointers): a JSON null leaves an earlier value in place.
        BindField("title", "string", "string", pointer=False),
        BindField("description", "string", "string", pointer=False),
        BindField("priority", "string", "models.TaskPriority", pointer=False),
        BindField("tags", "strings", "[]string"),
        BindField("assigned_to", "string", "string"),
        BindField("due_date", "time", "time.Time"),
    )

    def validation_errors(self) -> list[ValidationFailure]:
        # required fails first for ""; min=1 is then unreachable. len() counts code
        # points, which equals Go's rune count (invalid bytes already became U+FFFD).
        if self.title == "":
            return [(self.GO_STRUCT, "Title", "required")]
        if len(self.title) > 200:
            return [(self.GO_STRUCT, "Title", "max")]
        return []


@dataclass
class UpdateTaskRequest(_JSONRequest):
    """Go ``models.UpdateTaskRequest``: every field optional (``None`` = absent/null).

    ``title`` is ``binding:"omitempty,min=1,max=200"`` on a pointer, so a present ``""``
    fails ``min``. ``tags=[]`` replaces tags; ``assigned_to=""`` sets an empty assignee.
    """

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    tags: list[str] | None = None
    assigned_to: str | None = None
    due_date: GoTime | None = None

    GO_STRUCT: ClassVar[str] = "UpdateTaskRequest"
    BIND_FIELDS: ClassVar[tuple[BindField, ...]] = (
        BindField("title", "string", "string"),
        BindField("description", "string", "string"),
        BindField("status", "string", "models.TaskStatus"),
        BindField("priority", "string", "models.TaskPriority"),
        BindField("tags", "strings", "[]string"),
        BindField("assigned_to", "string", "string"),
        BindField("due_date", "time", "time.Time"),
    )

    def validation_errors(self) -> list[ValidationFailure]:
        if self.title is None:
            return []
        if len(self.title) < 1:
            return [(self.GO_STRUCT, "Title", "min")]
        if len(self.title) > 200:
            return [(self.GO_STRUCT, "Title", "max")]
        return []


@dataclass
class UpdateTaskStatusRequest(_JSONRequest):
    """Go ``models.UpdateTaskStatusRequest``; ``status`` is ``binding:"required"``."""

    status: str = ""

    GO_STRUCT: ClassVar[str] = "UpdateTaskStatusRequest"
    BIND_FIELDS: ClassVar[tuple[BindField, ...]] = (
        BindField("status", "string", "models.TaskStatus", pointer=False),
    )

    def validation_errors(self) -> list[ValidationFailure]:
        if self.status == "":
            return [(self.GO_STRUCT, "Status", "required")]
        return []


# ---------------------------------------------------------------------------
# List query / response
# ---------------------------------------------------------------------------


def _first_value(args: Any, key: str) -> str | None:
    """First value of a repeated query parameter, or ``None`` when absent.

    Accepts a Werkzeug ``MultiDict`` (``getlist``) or a plain ``name -> list[str]`` map.
    """
    values: Sequence[str]
    if hasattr(args, "getlist"):
        values = args.getlist(key)
    else:
        values = args.get(key) or ()
        if isinstance(values, str):
            values = (values,)
    return values[0] if values else None


@dataclass
class ListTasksQuery:
    """Go ``models.ListTasksQuery`` (``form`` tags; ``page_size`` default 20, 1..100)."""

    page_size: int = 20
    page_token: str = ""
    status: str = ""
    assigned_to: str = ""
    tags: str = ""
    sort_order: str = ""

    GO_STRUCT: ClassVar[str] = "ListTasksQuery"
    _STRING_FIELDS: ClassVar[tuple[str, ...]] = (
        "page_token",
        "status",
        "assigned_to",
        "tags",
        "sort_order",
    )

    @classmethod
    def from_query(cls, args: Any) -> ListTasksQuery:
        """Gin ``c.ShouldBindQuery``: form mapping, then ``binding`` validation.

        ``args`` maps names to value lists (``MultiDict`` or ``dict[str, list[str]]``);
        the first value wins and keys are case-sensitive. ``page_size`` is a Go ``int``
        (int64): absent -> 20, ``""`` -> 0, otherwise ``strconv.ParseInt(v, 10, 64)``.

        Raises :class:`~task_api.go_compat.GoNumError` (ParseInt failure) or
        :class:`GoValidationError` (``min``/``max`` on ``PageSize``).
        """
        raw_size = _first_value(args, "page_size")
        if raw_size is None:
            page_size = 20
        else:
            page_size = go_parse_int(raw_size or "0", 64)
        query = cls(page_size=page_size)
        for name in cls._STRING_FIELDS:
            value = _first_value(args, name)
            if value is not None:
                setattr(query, name, value)
        query.validate()
        return query

    def validation_errors(self) -> list[ValidationFailure]:
        if self.page_size < 1:
            return [(self.GO_STRUCT, "PageSize", "min")]
        if self.page_size > 100:
            return [(self.GO_STRUCT, "PageSize", "max")]
        return []

    def validate(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise GoValidationError(errors)


@dataclass
class ListTasksResponse:
    """Go ``models.ListTasksResponse``; ``tasks=None`` encodes as ``null``."""

    tasks: list[Task] | None = None
    next_page_token: str = ""
    total_count: int = 0

    def to_go(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tasks": self.tasks}
        if self.next_page_token:
            out["next_page_token"] = self.next_page_token
        out["total_count"] = self.total_count
        return out


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


@dataclass
class ErrorDetail:
    """Go ``models.ErrorDetail``; ``details`` (``interface{}``, omitempty) dropped when nil."""

    code: str
    message: str
    details: Any = None

    def to_go(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


@dataclass
class ErrorResponse:
    """Go ``models.ErrorResponse``: ``{"error": {...}}``."""

    error: ErrorDetail = field(default_factory=lambda: ErrorDetail("", ""))

    @classmethod
    def of(cls, code: str, message: str) -> ErrorResponse:
        return cls(ErrorDetail(code, message))

    def to_go(self) -> dict[str, Any]:
        return {"error": self.error}
