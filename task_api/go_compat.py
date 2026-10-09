"""Go compatibility helpers: encoding/json, RFC3339 time, Gin binding, validator messages.

Everything here reproduces the observable behaviour of the Go 1.21 standard library
(``encoding/json``, ``time``, ``strconv``) and of Gin 1.9 / go-playground/validator as
used by the reference service, byte for byte where it reaches the HTTP response.

Sections:

1. Go byte/rune helpers (UTF-8 decoding with Go's replacement rules, ``strconv.Quote``)
2. ``encoding/json`` encoder (:func:`go_dumps`, :func:`go_map`, :class:`GoJSONProvider`)
3. ``time.Time`` emulation (:class:`GoTime`, :func:`parse_rfc3339`, :func:`unmarshal_json_time`)
4. ``strconv`` emulation (:func:`go_atoi`, :func:`go_parse_int`)
5. Validator messages (:func:`validation_message`, :func:`join_validation_messages`)
6. Gin JSON binder (:func:`bind_json`, :class:`BindField`) -- ``ShouldBindJSON`` decode step
"""

from __future__ import annotations

import math
import re
import time as _time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from flask import Response
from flask.json.provider import JSONProvider

__all__ = [
    "GoError",
    "GoMarshalError",
    "GoTimeParseError",
    "GoNumError",
    "GoValidationError",
    "GoMap",
    "GoTime",
    "GoJSONProvider",
    "JSON_CONTENT_TYPE",
    "RFC3339",
    "RFC3339_NANO",
    "TIME_NOT_JSON_STRING",
    "go_map",
    "go_dumps",
    "go_decode_utf8",
    "go_bytes",
    "go_quote",
    "parse_rfc3339",
    "unmarshal_json_time",
    "go_atoi",
    "go_parse_int",
    "validation_message",
    "join_validation_messages",
    "GoBindError",
    "BindField",
    "bind_json",
]


class GoError(ValueError):
    """Base class for errors whose ``str()`` is the exact Go ``err.Error()`` text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# 1. Go byte / rune helpers
# ---------------------------------------------------------------------------

_RUNE_ERROR = 0xFFFD
_FFFD_BYTES = b"\xef\xbf\xbd"


def go_bytes(s: str | bytes) -> bytes:
    """Return the bytes a Go ``string`` holding ``s`` would contain.

    ``bytes`` pass through. For ``str``, code points U+DC80..U+DCFF (Python's
    ``surrogateescape`` representation of raw bytes) become those raw bytes and other
    lone surrogates are encoded with ``surrogatepass`` (i.e. as invalid UTF-8).
    """
    if isinstance(s, bytes):
        return s
    try:
        return s.encode("utf-8")
    except UnicodeEncodeError:
        out = bytearray()
        for ch in s:
            cp = ord(ch)
            if 0xDC80 <= cp <= 0xDCFF:
                out.append(cp - 0xDC00)
            else:
                out += ch.encode("utf-8", "surrogatepass")
        return bytes(out)


def _utf8_len(lead: int) -> int:
    if lead < 0x80:
        return 1
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def _go_runes(b: bytes) -> Iterator[tuple[int, int, int]]:
    """Yield ``(index, rune, width)`` exactly like Go's ``for i, r := range s``.

    Invalid UTF-8 yields ``(i, U+FFFD, 1)`` for each offending byte (``utf8.DecodeRune``).
    """
    i, n = 0, len(b)
    while i < n:
        lead = b[i]
        if lead < 0x80:
            yield i, lead, 1
            i += 1
            continue
        width = _utf8_len(lead)
        if width and i + width <= n:
            try:
                ch = b[i : i + width].decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                yield i, ord(ch), width
                i += width
                continue
        yield i, _RUNE_ERROR, 1
        i += 1


def go_decode_utf8(data: bytes) -> str:
    """Decode bytes the way Go's JSON decoder does: every invalid byte becomes one U+FFFD.

    Python's ``errors="replace"`` collapses a truncated multi-byte sequence into a single
    U+FFFD; Go emits one per byte (oracle: ``e2 82 'Z'`` -> ``\\ufffd\\ufffdZ``).
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "".join(chr(r) for _, r, _ in _go_runes(data))


_LOWERHEX = "0123456789abcdef"
_STRCONV_SHORT = {7: "\\a", 8: "\\b", 12: "\\f", 10: "\\n", 13: "\\r", 9: "\\t", 11: "\\v"}


def go_quote(s: str | bytes) -> str:
    """Go ``strconv.Quote``: double-quoted Go string literal.

    Printable runes (Unicode 15, same tables as Go 1.21) are kept, ``"`` and ``\\`` are
    backslash-escaped, ``\\a\\b\\f\\n\\r\\t\\v`` use short forms, other control bytes and
    DEL use ``\\xHH``, other non-printables ``\\uXXXX``/``\\UXXXXXXXX`` and invalid UTF-8
    bytes ``\\xHH``.
    """
    b = go_bytes(s)
    out = ['"']
    for i, r, width in _go_runes(b):
        if width == 1 and r == _RUNE_ERROR:
            out.append("\\x" + _LOWERHEX[b[i] >> 4] + _LOWERHEX[b[i] & 0xF])
            continue
        ch = chr(r)
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ch.isprintable():
            out.append(ch)
        elif r in _STRCONV_SHORT:
            out.append(_STRCONV_SHORT[r])
        elif r < 0x20 or r == 0x7F:
            out.append(f"\\x{r:02x}")
        elif r < 0x10000:
            out.append(f"\\u{r:04x}")
        else:
            out.append(f"\\U{r:08x}")
    out.append('"')
    return "".join(out)


def _time_quote(b: bytes) -> str:
    """The unexported ``time.quote`` used in ``time.ParseError`` messages (Go 1.21).

    Every byte of a non-ASCII or control rune is written as ``\\xHH``; ``"`` and ``\\``
    are backslash-escaped. Faithfully keeps Go's quirk that a valid U+FFFD at the very
    end of the string only emits its first byte.
    """
    out = ['"']
    n = len(b)
    for i, r, rune_width in _go_runes(b):
        if r >= 0x80 or r < 0x20:
            if r == _RUNE_ERROR:
                width = 3 if (i + 2 < n and b[i : i + 3] == _FFFD_BYTES) else 1
            else:
                width = rune_width
            for j in range(width):
                out.append("\\x" + _LOWERHEX[b[i + j] >> 4] + _LOWERHEX[b[i + j] & 0xF])
        else:
            ch = chr(r)
            if ch in ('"', "\\"):
                out.append("\\")
            out.append(ch)
    out.append('"')
    return "".join(out)


# ---------------------------------------------------------------------------
# 2. encoding/json encoder
# ---------------------------------------------------------------------------

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class GoMarshalError(GoError):
    """``json.Marshal`` failed (e.g. a time whose zone offset is >= 24h, NaN floats).

    Gin then sends the status and ``Content-Type`` with an *empty* body.
    """


class GoMap(dict):
    """A dict encoded with Go map semantics: keys sorted (byte-wise) on output."""


def go_map(d: Mapping[str, Any] | None = None, /, **kwargs: Any) -> GoMap:
    """Wrap a mapping so that :func:`go_dumps` emits its keys sorted, like a Go map/``gin.H``."""
    m = GoMap(d or {})
    m.update(kwargs)
    return m


def _json_escape_char(m: re.Match[str]) -> str:
    ch = m.group()
    cp = ord(ch)
    if ch == '"':
        return '\\"'
    if ch == "\\":
        return "\\\\"
    if ch == "\n":
        return "\\n"
    if ch == "\r":
        return "\\r"
    if ch == "\t":
        return "\\t"
    if 0xD800 <= cp <= 0xDFFF:
        # A lone surrogate is invalid UTF-8 in Go terms: encoder writes the � escape.
        return "\\ufffd"
    return f"\\u{cp:04x}"


# Go escapes: control chars, quote, backslash, HTML <>&, U+2028/U+2029; surrogates -> �.
_JSON_ESCAPE_RE = re.compile(
    "[\x00-\x1f\"\\\\<>&  " + chr(0xD800) + "-" + chr(0xDFFF) + "]"
)


def _encode_str(s: str) -> str:
    return '"' + _JSON_ESCAPE_RE.sub(_json_escape_char, s) + '"'


def _encode_float(f: float) -> str:
    """Go ``encoding/json`` float64 formatting (ES6-like)."""
    if math.isnan(f) or math.isinf(f):
        name = "NaN" if math.isnan(f) else ("+Inf" if f > 0 else "-Inf")
        raise GoMarshalError(f"json: unsupported value: {name}")
    if f == 0:
        return "-0" if math.copysign(1.0, f) < 0 else "0"
    d = Decimal(repr(f)).normalize()
    a = abs(f)
    if a < 1e-6 or a >= 1e21:
        mant, _, exp = f"{d:e}".partition("e")
        sign = exp[0] if exp[0] in "+-" else "+"
        digits = exp.lstrip("+-")
        if sign == "+" and len(digits) < 2:
            digits = digits.zfill(2)
        return f"{mant}e{sign}{digits}"
    return f"{d:f}"


def _encode(obj: Any, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif isinstance(obj, str):
        out.append(_encode_str(obj))
    elif isinstance(obj, bool):
        out.append("true" if obj else "false")
    elif isinstance(obj, int):
        out.append(str(int(obj)))
    elif isinstance(obj, float):
        out.append(_encode_float(obj))
    elif isinstance(obj, GoTime):
        out.append('"' + obj.marshal_text() + '"')
    elif hasattr(obj, "to_go"):
        _encode(obj.to_go(), out)
    elif isinstance(obj, Enum):
        _encode(obj.value, out)
    elif isinstance(obj, Mapping):
        items: Iterable[tuple[str, Any]] = ((_map_key(k), v) for k, v in obj.items())
        if isinstance(obj, GoMap):
            items = sorted(items, key=lambda kv: go_bytes(kv[0]))
        out.append("{")
        first = True
        for k, v in items:
            if not first:
                out.append(",")
            first = False
            out.append(_encode_str(k))
            out.append(":")
            _encode(v, out)
        out.append("}")
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, v in enumerate(obj):
            if i:
                out.append(",")
            _encode(v, out)
        out.append("]")
    else:
        raise TypeError(f"go_dumps: unsupported type {type(obj).__name__}")


def _map_key(k: Any) -> str:
    if isinstance(k, str):
        return str.__str__(k) if not isinstance(k, Enum) else str(k.value)
    if isinstance(k, int) and not isinstance(k, bool):
        return str(k)
    raise TypeError(f"go_dumps: unsupported map key type {type(k).__name__}")


def go_dumps(obj: Any) -> str:
    """Serialize ``obj`` exactly like Go's ``json.Marshal`` (as used by Gin's ``c.JSON``).

    Compact (``,``/``:``), no trailing newline, non-ASCII kept as-is, ``< > &`` and
    U+2028/U+2029 escaped as ``\\u003c`` etc., control characters as ``\\n``/``\\r``/``\\t``
    or ``\\u00XX`` (Go 1.21 has no ``\\b``/``\\f`` forms), lone surrogates as ``\\ufffd``.
    Mappings keep insertion order (struct field order) unless wrapped with :func:`go_map`;
    ``None`` is ``null``; :class:`GoTime` is an RFC3339Nano string; objects with a
    ``to_go()`` method are encoded via its return value; ``Enum`` via its value.

    Raises :class:`GoMarshalError` where Go's ``json.Marshal`` would fail.
    """
    out: list[str] = []
    _encode(obj, out)
    return "".join(out)


class GoJSONProvider(JSONProvider):
    """Flask JSON provider emitting Go/Gin-identical JSON with ``charset=utf-8``.

    If marshalling fails (:class:`GoMarshalError`) the response body is empty, matching
    Gin 1.9 where ``c.JSON`` keeps the status and ``Content-Type`` but writes nothing.
    """

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        return go_dumps(obj)

    def loads(self, s: str | bytes, **kwargs: Any) -> Any:
        import json

        return json.loads(s, **kwargs)

    def response(self, *args: Any, **kwargs: Any) -> Response:
        obj = self._prepare_response_obj(args, kwargs)
        try:
            body = go_dumps(obj)
        except GoMarshalError:
            body = ""
        return self._app.response_class(body, content_type=JSON_CONTENT_TYPE)


# ---------------------------------------------------------------------------
# 3. time.Time emulation
# ---------------------------------------------------------------------------

RFC3339 = "2006-01-02T15:04:05Z07:00"
RFC3339_NANO = "2006-01-02T15:04:05.999999999Z07:00"
TIME_NOT_JSON_STRING = "Time.UnmarshalJSON: input is not a JSON string"

_NS_PER_SEC = 1_000_000_000
_SECS_PER_DAY = 86_400


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 in the proleptic Gregorian calendar (any year)."""
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146_097 + doe - 719_468


def _civil_from_days(z: int) -> tuple[int, int, int]:
    z += 719_468
    era = z // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in(month: int, year: int) -> int:
    if month == 2:
        return 29 if _is_leap(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


class GoTime:
    """Immutable Go ``time.Time`` equivalent with nanosecond precision.

    Stores the absolute instant as integer Unix seconds plus ``nanosecond``
    (0..999_999_999) and the original UTC ``offset`` in seconds. Integers are used
    instead of a :class:`datetime` because Go accepts values Python's ``datetime`` cannot
    hold (year 0000, offsets up to +99:99); :attr:`datetime` gives the microsecond
    ``datetime`` view whenever it is representable.

    Comparisons (``<``, ``==``, :meth:`before`, :meth:`after`, :meth:`equal`) use the
    absolute instant, like Go's ``Before``/``After``/``Equal``; the offset only affects
    formatting.
    """

    __slots__ = ("_sec", "_nsec", "_offset")

    def __init__(self, unix_seconds: int, nanosecond: int = 0, offset: int = 0) -> None:
        if not 0 <= nanosecond < _NS_PER_SEC:
            raise ValueError("nanosecond must be in 0..999999999")
        self._sec = int(unix_seconds)
        self._nsec = int(nanosecond)
        self._offset = int(offset)

    # -- constructors -------------------------------------------------------
    @classmethod
    def now(cls) -> GoTime:
        """Current time in the system local zone, like Go's ``time.Now()``."""
        ns = _time.time_ns()
        sec, nsec = divmod(ns, _NS_PER_SEC)
        return cls(sec, nsec, _time.localtime(sec).tm_gmtoff)

    @classmethod
    def from_unix_nano(cls, unix_nano: int, offset: int = 0) -> GoTime:
        sec, nsec = divmod(unix_nano, _NS_PER_SEC)
        return cls(sec, nsec, offset)

    @classmethod
    def from_datetime(cls, dt: datetime, nanosecond: int | None = None) -> GoTime:
        """Build from an aware ``datetime`` (naive means local time).

        ``nanosecond`` overrides the sub-second part (must agree with nothing; it simply
        replaces ``dt.microsecond * 1000``).
        """
        if dt.tzinfo is None or dt.utcoffset() is None:
            dt = dt.astimezone()
        off = dt.utcoffset()
        assert off is not None
        offset = int(off.total_seconds())
        days = _days_from_civil(dt.year, dt.month, dt.day)
        local = days * _SECS_PER_DAY + dt.hour * 3600 + dt.minute * 60 + dt.second
        ns = dt.microsecond * 1000 if nanosecond is None else nanosecond
        return cls(local - offset, ns, offset)

    @classmethod
    def from_parts(
        cls,
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
        nanosecond: int = 0,
        offset: int = 0,
    ) -> GoTime:
        """Like Go ``time.Date`` for already-normalized wall-clock fields at ``offset``."""
        local = (
            _days_from_civil(year, month, day) * _SECS_PER_DAY
            + hour * 3600
            + minute * 60
            + second
        )
        return cls(local - offset, nanosecond, offset)

    # -- accessors ----------------------------------------------------------
    @property
    def unix_seconds(self) -> int:
        return self._sec

    @property
    def nanosecond(self) -> int:
        """Nanoseconds within the second, 0..999_999_999 (Go ``t.Nanosecond()``)."""
        return self._nsec

    @property
    def offset(self) -> int:
        """UTC offset in seconds east of UTC."""
        return self._offset

    @property
    def unix_nano(self) -> int:
        return self._sec * _NS_PER_SEC + self._nsec

    def wall(self) -> tuple[int, int, int, int, int, int]:
        """``(year, month, day, hour, minute, second)`` in the time's own offset."""
        local = self._sec + self._offset
        days, rem = divmod(local, _SECS_PER_DAY)
        y, m, d = _civil_from_days(days)
        hh, rem = divmod(rem, 3600)
        mm, ss = divmod(rem, 60)
        return y, m, d, hh, mm, ss

    @property
    def datetime(self) -> datetime:
        """Aware ``datetime`` (microsecond precision) in the original offset.

        Falls back to UTC if the offset is outside Python's +-24h range; raises
        ``OverflowError``/``ValueError`` if the year is not representable.
        """
        base = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self._sec, microseconds=self._nsec // 1000
        )
        if abs(self._offset) < _SECS_PER_DAY:
            return base.astimezone(timezone(timedelta(seconds=self._offset)))
        return base

    # -- comparisons --------------------------------------------------------
    def _key(self) -> tuple[int, int]:
        return self._sec, self._nsec

    def before(self, other: GoTime) -> bool:
        return self._key() < other._key()

    def after(self, other: GoTime) -> bool:
        return self._key() > other._key()

    def equal(self, other: GoTime) -> bool:
        return self._key() == other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GoTime):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: GoTime) -> bool:
        return self._key() < other._key()

    def __le__(self, other: GoTime) -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: GoTime) -> bool:
        return self._key() > other._key()

    def __ge__(self, other: GoTime) -> bool:
        return self._key() >= other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    # -- formatting ---------------------------------------------------------
    def _format_offset(self) -> str:
        if self._offset == 0:
            return "Z"
        zone = abs(self._offset) // 60
        sign = "-" if self._offset < 0 else "+"
        return f"{sign}{zone // 60:02d}:{zone % 60:02d}"

    def _format(self, nano: bool) -> str:
        y, mo, d, hh, mi, ss = self.wall()
        year = f"{y:04d}" if y >= 0 else f"-{-y:04d}"
        s = f"{year}-{mo:02d}-{d:02d}T{hh:02d}:{mi:02d}:{ss:02d}"
        if nano and self._nsec:
            s += "." + f"{self._nsec:09d}".rstrip("0")
        return s + self._format_offset()

    def format_rfc3339nano(self) -> str:
        """Go ``t.Format(time.RFC3339Nano)``: trimmed fraction, ``Z`` or ``+hh:mm``."""
        return self._format(nano=True)

    def format_rfc3339(self) -> str:
        """Go ``t.Format(time.RFC3339)`` (whole seconds), as used by ``/health``."""
        return self._format(nano=False)

    def marshal_text(self) -> str:
        """RFC3339Nano text as produced by Go 1.21 ``Time.MarshalJSON`` (unquoted).

        Raises :class:`GoMarshalError` for values Go refuses to marshal: a year outside
        0..9999 or a zone offset whose hour part is >= 24 (oracle: ``+24:00`` input
        yields 201 with an empty body).
        """
        y = self.wall()[0]
        if not 0 <= y <= 9999:
            raise GoMarshalError(
                "json: error calling MarshalJSON for type *time.Time: "
                "Time.MarshalJSON: year outside of range [0,9999]"
            )
        if abs(self._offset) // 3600 >= 24:
            raise GoMarshalError(
                "json: error calling MarshalJSON for type *time.Time: "
                "Time.MarshalJSON: timezone hour outside of range [0,23]"
            )
        return self.format_rfc3339nano()

    def to_go(self) -> str:
        return self.marshal_text()

    def __str__(self) -> str:
        return self.format_rfc3339nano()

    def __repr__(self) -> str:
        return f"GoTime({self.format_rfc3339nano()!r})"


class GoTimeParseError(GoError):
    """Go ``*time.ParseError`` (``str()`` is ``err.Error()``)."""

    def __init__(
        self,
        value: bytes,
        layout_elem: str = "",
        value_elem: bytes = b"",
        message: str = "",
        layout: str = RFC3339,
    ) -> None:
        self.value = value
        self.layout = layout
        self.layout_elem = layout_elem
        self.value_elem = value_elem
        self.detail = message
        if message:
            text = "parsing time " + _time_quote(value) + message
        else:
            text = (
                "parsing time "
                + _time_quote(value)
                + " as "
                + _time_quote(layout.encode())
                + ": cannot parse "
                + _time_quote(value_elem)
                + " as "
                + _time_quote(layout_elem.encode())
            )
        super().__init__(text)


class _Bad(Exception):
    """Internal: ``errBad`` from Go's time parser."""


def _isdigit(b: bytes, i: int) -> bool:
    return i < len(b) and 0x30 <= b[i] <= 0x39


def _getnum(b: bytes, fixed: bool) -> tuple[int, bytes]:
    if not _isdigit(b, 0):
        raise _Bad
    if not _isdigit(b, 1):
        if fixed:
            raise _Bad
        return b[0] - 0x30, b[1:]
    return (b[0] - 0x30) * 10 + (b[1] - 0x30), b[2:]


def parse_rfc3339(s: str | bytes) -> GoTime:
    """Go 1.21 ``time.Parse(time.RFC3339, s)`` -- which is what ``Time.UnmarshalJSON`` uses.

    Go 1.21 disables the extra strictness checks (go.dev/issue/54580), so this is the
    general layout parser: 1-digit hours and ``,`` fraction separators are accepted,
    fractions longer than 9 digits are truncated, offsets accept any two-digit
    hour/minute (``+05:99`` == ``+06:39``). Errors carry Go's exact message, e.g.
    ``parsing time "x" as "2006-01-02T15:04:05Z07:00": cannot parse "x" as "2006"``,
    ``parsing time "2025-13-01T10:00:00Z": month out of range`` or
    ``...: extra text: "junk"``.
    """
    value = go_bytes(s)
    v = value

    def fail(layout_elem: str, elem: bytes) -> GoTimeParseError:
        return GoTimeParseError(value, layout_elem, elem)

    def range_err(what: str, rest: bytes) -> GoTimeParseError:
        return GoTimeParseError(value, "", rest, f": {what} out of range")

    def skip(rest: bytes, lit: bytes) -> bytes:
        if not rest or rest[0] != lit[0]:
            raise fail(lit.decode(), rest)
        return rest[1:]

    # stdLongYear "2006"
    if len(v) < 4 or not all(_isdigit(v, i) for i in range(4)):
        raise fail("2006", v)
    year, v = int(v[:4]), v[4:]
    # "-" stdZeroMonth "01"
    v = skip(v, b"-")
    try:
        month, rest = _getnum(v, True)
    except _Bad:
        raise fail("01", v) from None
    v = rest
    if not 1 <= month <= 12:
        raise range_err("month", v)
    # "-" stdZeroDay "02" (range validated after the whole value is parsed)
    v = skip(v, b"-")
    try:
        day, v2 = _getnum(v, True)
    except _Bad:
        raise fail("02", v) from None
    v = v2
    # "T" stdHour "15" (not fixed width: one digit allowed)
    v = skip(v, b"T")
    try:
        hour, v2 = _getnum(v, False)
    except _Bad:
        raise fail("15", v) from None
    v = v2
    if hour >= 24:
        raise range_err("hour", v)
    # ":" stdZeroMinute "04"
    v = skip(v, b":")
    try:
        minute, v2 = _getnum(v, True)
    except _Bad:
        raise fail("04", v) from None
    v = v2
    if minute >= 60:
        raise range_err("minute", v)
    # ":" stdZeroSecond "05" + optional fractional second ('.' or ',')
    v = skip(v, b":")
    try:
        second, v2 = _getnum(v, True)
    except _Bad:
        raise fail("05", v) from None
    v = v2
    if second >= 60:
        raise range_err("second", v)
    nsec = 0
    if len(v) >= 2 and v[0] in b".," and _isdigit(v, 1):
        n = 2
        while n < len(v) and _isdigit(v, n):
            n += 1
        digits = v[1 : min(n, 10)]
        nsec = int(digits) * 10 ** (9 - len(digits))
        v = v[n:]
    # stdISO8601ColonTZ "Z07:00"
    if v[:1] == b"Z":
        offset = 0
        v = v[1:]
    else:
        hold = v
        if len(v) < 6 or v[3] != 0x3A:
            raise fail("Z07:00", hold)
        sign, hh, mm, v = v[0:1], v[1:3], v[4:6], v[6:]
        try:
            hr, r1 = _getnum(hh, True)
            mn, r2 = _getnum(mm, True)
        except _Bad:
            raise fail("Z07:00", hold) from None
        if sign not in (b"+", b"-"):
            raise fail("Z07:00", hold)
        offset = (hr * 60 + mn) * 60
        if sign == b"-":
            offset = -offset
    if v:
        raise GoTimeParseError(value, "", v, ": extra text: " + _time_quote(v))
    if not 1 <= day <= _days_in(month, year):
        raise GoTimeParseError(value, "", v, ": day out of range")
    return GoTime.from_parts(year, month, day, hour, minute, second, nsec, offset)


def unmarshal_json_time(raw: bytes | str) -> GoTime | None:
    """Go 1.21 ``(*time.Time).UnmarshalJSON`` on a raw JSON value (as it appears in the body).

    ``null`` -> ``None`` (field left nil). Anything not delimited by ``"`` (number, bool,
    object, array) raises ``GoError(TIME_NOT_JSON_STRING)``. The bytes between the quotes
    are parsed *without* JSON unescaping (Go TODO go.dev/issue/47353), so an escaped
    ``\\u005a`` is literally ``\\u005a`` to the parser.

    Note for the binder: unlike type mismatches (which Go records and keeps decoding),
    this error aborts decoding immediately and wins over any earlier type error.
    """
    data = go_bytes(raw)
    if data == b"null":
        return None
    if len(data) < 2 or data[:1] != b'"' or data[-1:] != b'"':
        raise GoError(TIME_NOT_JSON_STRING)
    return parse_rfc3339(data[1:-1])


# ---------------------------------------------------------------------------
# 4. strconv emulation
# ---------------------------------------------------------------------------

ERR_SYNTAX = "invalid syntax"
ERR_RANGE = "value out of range"


class GoNumError(GoError):
    """Go ``*strconv.NumError``: ``strconv.<Func>: parsing <Quote(Num)>: <Err>``."""

    def __init__(self, func: str, num: str | bytes, err: str) -> None:
        self.func = func
        self.num = num
        self.err = err
        super().__init__(f"strconv.{func}: parsing {go_quote(num)}: {err}")


_MAX_UINT64 = (1 << 64) - 1
_UINT64_CUTOFF_10 = _MAX_UINT64 // 10 + 1


def _parse_uint10(b: bytes, bit_size: int) -> tuple[int, str | None]:
    """Go ``strconv.ParseUint(s, 10, bitSize)`` core: returns ``(value, err)``."""
    if not b:
        return 0, ERR_SYNTAX
    max_val = (1 << bit_size) - 1
    n = 0
    for c in b:
        if not 0x30 <= c <= 0x39:
            return 0, ERR_SYNTAX
        if n >= _UINT64_CUTOFF_10:
            return max_val, ERR_RANGE
        n1 = n * 10 + (c - 0x30)
        if n1 > max_val:
            return max_val, ERR_RANGE
        n = n1
    return n, None


def go_parse_int(s: str | bytes, bit_size: int = 64, *, func: str = "ParseInt") -> int:
    """Go ``strconv.ParseInt(s, 10, bit_size)`` (``bit_size`` 0 means 64).

    Accepts an optional ``+``/``-`` then ASCII digits only (no spaces, ``_``, prefixes).
    Raises :class:`GoNumError` with ``invalid syntax`` or ``value out of range`` exactly
    as Go does, including Go's scan order (an overflow seen before a bad character is a
    range error: ``"99999999999999999999x"`` -> out of range).
    """
    if bit_size == 0:
        bit_size = 64
    b = go_bytes(s)
    if not b:
        raise GoNumError(func, s, ERR_SYNTAX)
    neg = b[:1] == b"-"
    digits = b[1:] if b[:1] in (b"+", b"-") else b
    un, err = _parse_uint10(digits, bit_size)
    if err == ERR_SYNTAX:
        raise GoNumError(func, s, ERR_SYNTAX)
    cutoff = 1 << (bit_size - 1)
    if not neg and un >= cutoff:
        raise GoNumError(func, s, ERR_RANGE)
    if neg and un > cutoff:
        raise GoNumError(func, s, ERR_RANGE)
    return -un if neg else un


def go_atoi(s: str | bytes) -> int:
    """Go ``strconv.Atoi`` on a 64-bit platform (range = int64); errors name ``Atoi``."""
    return go_parse_int(s, 64, func="Atoi")


# ---------------------------------------------------------------------------
# 5. Validator messages (go-playground/validator v10 via Gin)
# ---------------------------------------------------------------------------


def validation_message(struct: str, field: str, tag: str) -> str:
    """One ``validator.FieldError`` text, e.g. for ``("CreateTaskRequest", "Title", "required")``:

    ``Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'required' tag``
    """
    return f"Key: '{struct}.{field}' Error:Field validation for '{field}' failed on the '{tag}' tag"


def join_validation_messages(messages: Iterable[str]) -> str:
    """``validator.ValidationErrors.Error()``: each message + ``\\n``, then ``TrimSpace``."""
    return "".join(m + "\n" for m in messages).strip()


class GoValidationError(GoError):
    """``validator.ValidationErrors``: a list of ``(struct, field, tag)`` failures."""

    def __init__(self, errors: Iterable[tuple[str, str, str]]) -> None:
        self.errors = list(errors)
        super().__init__(
            join_validation_messages(validation_message(*e) for e in self.errors)
        )


# ===========================================================================
# 6. Gin JSON binder (bind_json)
#
# Gin's ``c.ShouldBindJSON(&req)`` is ``json.NewDecoder(body).Decode(&req)`` followed by
# validation. Go 1.21's Decoder first syntax-scans exactly one JSON value (``readValue``),
# then decodes it into the struct. Both phases are reproduced here:
#
# * :class:`_Scanner` is a transcription of ``encoding/json/scanner.go`` (same states and
#   error texts, ``maxNestingDepth`` 10000) driven by :func:`_read_value`.
# * :class:`_StructDecoder` mirrors ``decodeState.object/array/literalStore`` for the flat
#   request DTOs: first type error saved and decoding continued, ``time.Time`` errors
#   returned immediately, case-insensitive keys, unknown keys skipped, and Go's slice
#   reuse (``[]string`` re-decoded in place, so ``null`` elements may expose old values).
# ===========================================================================


class GoBindError(GoError):
    """An error returned by Gin's JSON binding (``io.EOF``, ``*json.SyntaxError``, ...)."""


@dataclass(frozen=True)
class BindField:
    """One exported field of a request DTO.

    ``kind``: ``"string"`` (any Go string type), ``"strings"`` (``[]string``) or ``"time"``
    (``*time.Time``). ``go_type`` is the Go type name used in ``UnmarshalTypeError``.
    ``pointer`` only matters for ``"string"``: a JSON ``null`` resets a pointer field to nil
    but is a no-op on a plain ``string`` (an earlier duplicate key's value survives).
    """

    json_name: str
    kind: str
    go_type: str
    pointer: bool = True


_MAX_NESTING_DEPTH = 10000

# scanner.go opcodes (only the ones the Decoder inspects are distinguished)
_SCAN_CONTINUE = 0
_SCAN_SKIP_SPACE = 1
_SCAN_END_OBJECT = 2
_SCAN_END_ARRAY = 3
_SCAN_END = 4
_SCAN_ERROR = 5

_PARSE_OBJECT_KEY = 0
_PARSE_OBJECT_VALUE = 1
_PARSE_ARRAY_VALUE = 2

_SPACE = frozenset(b" \t\r\n")
_DIGITS = frozenset(b"0123456789")
_HEX = frozenset(b"0123456789abcdefABCDEF")
_ESCAPES = {
    ord("b"): "\b",
    ord("f"): "\f",
    ord("n"): "\n",
    ord("r"): "\r",
    ord("t"): "\t",
    ord("\\"): "\\",
    ord("/"): "/",
    ord('"'): '"',
}


def _quote_char(c: int) -> str:
    """``encoding/json.quoteChar``: a byte is shown as the rune with that value."""
    if c == ord("'"):
        return "'\\''"
    if c == ord('"'):
        return "'\"'"
    return "'" + go_quote(chr(c))[1:-1] + "'"


class _Scanner:
    """``encoding/json`` scanner state machine (one ``step`` per input byte)."""

    def __init__(self) -> None:
        self.step: Callable[[int], int] = self._begin_value
        self.parse_state: list[int] = []
        self.err: str | None = None
        self.end_top = False

    def _error(self, c: int, context: str) -> int:
        self.step = self._state_error
        self.err = f"invalid character {_quote_char(c)} {context}"
        return _SCAN_ERROR

    def _state_error(self, c: int) -> int:
        return _SCAN_ERROR

    def _push(self, c: int, state: int, success: int) -> int:
        self.parse_state.append(state)
        if len(self.parse_state) <= _MAX_NESTING_DEPTH:
            return success
        return self._error(c, "exceeded max depth")

    def _pop(self) -> None:
        self.parse_state.pop()
        if not self.parse_state:
            self.step = self._end_top
            self.end_top = True
        else:
            self.step = self._end_value

    def _begin_value_or_empty(self, c: int) -> int:
        if c in _SPACE:
            return _SCAN_SKIP_SPACE
        if c == 0x5D:  # ]
            return self._end_value(c)
        return self._begin_value(c)

    def _begin_value(self, c: int) -> int:
        if c in _SPACE:
            return _SCAN_SKIP_SPACE
        if c == 0x7B:  # {
            self.step = self._begin_string_or_empty
            return self._push(c, _PARSE_OBJECT_KEY, _SCAN_CONTINUE)
        if c == 0x5B:  # [
            self.step = self._begin_value_or_empty
            return self._push(c, _PARSE_ARRAY_VALUE, _SCAN_CONTINUE)
        nxt = {
            0x22: self._in_string,
            0x2D: self._neg,
            0x30: self._zero,
            0x74: self._literal(b"rue", "true"),
            0x66: self._literal(b"alse", "false"),
            0x6E: self._literal(b"ull", "null"),
        }.get(c)
        if nxt is None and 0x31 <= c <= 0x39:
            nxt = self._one
        if nxt is None:
            return self._error(c, "looking for beginning of value")
        self.step = nxt
        return _SCAN_CONTINUE

    def _begin_string_or_empty(self, c: int) -> int:
        if c in _SPACE:
            return _SCAN_SKIP_SPACE
        if c == 0x7D:  # }
            self.parse_state[-1] = _PARSE_OBJECT_VALUE
            return self._end_value(c)
        return self._begin_string(c)

    def _begin_string(self, c: int) -> int:
        if c in _SPACE:
            return _SCAN_SKIP_SPACE
        if c == 0x22:
            self.step = self._in_string
            return _SCAN_CONTINUE
        return self._error(c, "looking for beginning of object key string")

    def _end_value(self, c: int) -> int:
        if not self.parse_state:
            self.step = self._end_top
            self.end_top = True
            return self._end_top(c)
        if c in _SPACE:
            self.step = self._end_value
            return _SCAN_SKIP_SPACE
        ps = self.parse_state[-1]
        if ps == _PARSE_OBJECT_KEY:
            if c == 0x3A:  # :
                self.parse_state[-1] = _PARSE_OBJECT_VALUE
                self.step = self._begin_value
                return _SCAN_CONTINUE
            return self._error(c, "after object key")
        if ps == _PARSE_OBJECT_VALUE:
            if c == 0x2C:  # ,
                self.parse_state[-1] = _PARSE_OBJECT_KEY
                self.step = self._begin_string
                return _SCAN_CONTINUE
            if c == 0x7D:
                self._pop()
                return _SCAN_END_OBJECT
            return self._error(c, "after object key:value pair")
        if c == 0x2C:
            self.step = self._begin_value
            return _SCAN_CONTINUE
        if c == 0x5D:
            self._pop()
            return _SCAN_END_ARRAY
        return self._error(c, "after array element")

    def _end_top(self, c: int) -> int:
        if c not in _SPACE:
            # Go records "after top-level value" here but still returns scanEnd; the
            # Decoder never looks at it (only the first value is read).
            self._error(c, "after top-level value")
        return _SCAN_END

    def _in_string(self, c: int) -> int:
        if c == 0x22:
            self.step = self._end_value
        elif c == 0x5C:  # backslash
            self.step = self._in_string_esc
        elif c < 0x20:
            return self._error(c, "in string literal")
        return _SCAN_CONTINUE

    def _in_string_esc(self, c: int) -> int:
        if c in _ESCAPES:
            self.step = self._in_string
            return _SCAN_CONTINUE
        if c == 0x75:  # u
            self.step = self._hex(4)
            return _SCAN_CONTINUE
        return self._error(c, "in string escape code")

    def _hex(self, remaining: int) -> Callable[[int], int]:
        def state(c: int) -> int:
            if c in _HEX:
                self.step = self._in_string if remaining == 1 else self._hex(remaining - 1)
                return _SCAN_CONTINUE
            return self._error(c, "in \\u hexadecimal character escape")

        return state

    def _neg(self, c: int) -> int:
        if c == 0x30:
            self.step = self._zero
            return _SCAN_CONTINUE
        if 0x31 <= c <= 0x39:
            self.step = self._one
            return _SCAN_CONTINUE
        return self._error(c, "in numeric literal")

    def _one(self, c: int) -> int:
        if c in _DIGITS:
            return _SCAN_CONTINUE
        return self._zero(c)

    def _zero(self, c: int) -> int:
        if c == 0x2E:  # .
            self.step = self._dot
            return _SCAN_CONTINUE
        if c in (0x65, 0x45):  # e E
            self.step = self._exp
            return _SCAN_CONTINUE
        return self._end_value(c)

    def _dot(self, c: int) -> int:
        if c in _DIGITS:
            self.step = self._dot0
            return _SCAN_CONTINUE
        return self._error(c, "after decimal point in numeric literal")

    def _dot0(self, c: int) -> int:
        if c in _DIGITS:
            return _SCAN_CONTINUE
        if c in (0x65, 0x45):
            self.step = self._exp
            return _SCAN_CONTINUE
        return self._end_value(c)

    def _exp(self, c: int) -> int:
        if c in (0x2B, 0x2D):  # + -
            self.step = self._exp_sign
            return _SCAN_CONTINUE
        return self._exp_sign(c)

    def _exp_sign(self, c: int) -> int:
        if c in _DIGITS:
            self.step = self._exp0
            return _SCAN_CONTINUE
        return self._error(c, "in exponent of numeric literal")

    def _exp0(self, c: int) -> int:
        if c in _DIGITS:
            return _SCAN_CONTINUE
        return self._end_value(c)

    def _literal(self, rest: bytes, word: str) -> Callable[[int], int]:
        """``stateT``/``stateTr``/... chain for the remaining letters of a literal."""
        expect = rest[0]

        def state(c: int) -> int:
            if c != expect:
                return self._error(c, f"in literal {word} (expecting {chr(expect)!r})")
            self.step = self._end_value if len(rest) == 1 else self._literal(rest[1:], word)
            return _SCAN_CONTINUE

        return state


def _read_value(data: bytes) -> bytes:
    """``(*json.Decoder).readValue`` over a fully buffered body: the first JSON value."""
    scan = _Scanner()
    for i, c in enumerate(data):
        op = scan.step(c)
        if op == _SCAN_END:  # delayed one byte: c is not part of the value
            return data[:i]
        if op in (_SCAN_END_OBJECT, _SCAN_END_ARRAY):
            if scan._end_value(0x20) == _SCAN_END:
                return data[: i + 1]
        elif op == _SCAN_ERROR:
            raise GoBindError(scan.err or "")
    if scan.step(0x20) == _SCAN_END:
        return data
    if any(c not in _SPACE for c in data):
        raise GoBindError("unexpected EOF")
    raise GoBindError("EOF")


def _skip_value(data: bytes, i: int) -> int:
    """Index just past the (already validated) JSON value starting at ``data[i]``."""
    c = data[i]
    if c == 0x22:
        return _skip_string(data, i)
    if c not in (0x7B, 0x5B):
        j = i + 1
        while j < len(data) and data[j] not in _SPACE and data[j] not in b",:]}":
            j += 1
        return j
    depth, j = 0, i
    while True:
        c = data[j]
        if c == 0x22:
            j = _skip_string(data, j)
            continue
        if c in (0x7B, 0x5B):
            depth += 1
        elif c in (0x7D, 0x5D):
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1


def _skip_string(data: bytes, i: int) -> int:
    j = i + 1
    while data[j] != 0x22:
        j += 2 if data[j] == 0x5C else 1
    return j + 1


def _skip_space(data: bytes, i: int) -> int:
    while data[i] in _SPACE:
        i += 1
    return i


def _getu4(s: bytes, i: int) -> int:
    if s[i : i + 2] != b"\\u" or len(s) < i + 6:
        return -1
    return int(s[i + 2 : i + 6], 16)


def _unquote(s: bytes) -> str:
    """``encoding/json.unquoteBytes`` on the bytes between the quotes of a valid string.

    Invalid UTF-8 -> one U+FFFD per byte; an unpaired ``\\uD800``-style surrogate -> U+FFFD.
    """
    if 0x5C not in s:
        return go_decode_utf8(s)
    out: list[str] = []
    i, start, n = 0, 0, len(s)
    while i < n:
        if s[i] != 0x5C:
            i += 1
            continue
        out.append(go_decode_utf8(s[start:i]))
        esc = s[i + 1]
        if esc != 0x75:
            out.append(_ESCAPES[esc])
            i += 2
        else:
            r = _getu4(s, i)
            i += 6
            if 0xD800 <= r < 0xE000:
                r2 = _getu4(s, i)
                if r < 0xDC00 and 0xDC00 <= r2 < 0xE000:
                    r = 0x10000 + ((r - 0xD800) << 10) + (r2 - 0xDC00)
                    i += 6
                else:
                    r = _RUNE_ERROR
            out.append(chr(r))
        start = i
    out.append(go_decode_utf8(s[start:]))
    return "".join(out)


def _fold_name(name: str) -> str:
    """``encoding/json.foldName``: ASCII upper-case, other runes to the smallest rune of
    their ``unicode.SimpleFold`` orbit (only U+212A KELVIN SIGN -> ``K`` and U+017F LONG S
    -> ``S`` can reach ASCII field names)."""
    out = []
    for ch in name:
        if ch < "\x80":
            out.append(ch.upper())
        elif ch == "K":
            out.append("K")
        elif ch == "ſ":
            out.append("S")
        else:
            low, up = ch.lower(), ch.upper()
            cands = [c for c in (ch, low, up) if len(c) == 1 and c.upper() == up]
            out.append(min(cands))
    return "".join(out)


def _json_kind(c: int) -> str:
    """The ``UnmarshalTypeError.Value`` word for a value starting with byte ``c``."""
    if c == 0x7B:
        return "object"
    if c == 0x5B:
        return "array"
    if c == 0x22:
        return "string"
    if c in (0x74, 0x66):
        return "bool"
    return "number"


class _StructDecoder:
    """``decodeState`` specialised to a flat struct of :class:`BindField` fields."""

    def __init__(self, data: bytes, go_struct: str, fields: Sequence[BindField]) -> None:
        self.data = data
        self.go_struct = go_struct
        self.by_name = {f.json_name: f for f in fields}
        self.by_fold: dict[str, BindField] = {}
        for f in fields:
            self.by_fold.setdefault(_fold_name(f.json_name), f)
        self.saved: str | None = None
        self.result: dict[str, Any] = {}
        self.backing: dict[str, list[str]] = {}  # []string backing arrays (cap contents)

    def _save(self, kind: str, f: BindField, go_type: str) -> None:
        if self.saved is None:
            self.saved = (
                f"json: cannot unmarshal {kind} into Go struct field "
                f"{self.go_struct}.{f.json_name} of type {go_type}"
            )

    def object(self, i: int) -> None:
        data = self.data
        i = _skip_space(data, i + 1)
        if data[i] == 0x7D:
            return
        while True:
            end = _skip_string(data, i)
            key = _unquote(data[i + 1 : end - 1])
            f = self.by_name.get(key) or self.by_fold.get(_fold_name(key))
            i = _skip_space(data, _skip_space(data, end) + 1)  # past ':'
            vend = _skip_value(data, i)
            if f is not None:
                self.field(f, i, vend)
            i = _skip_space(data, vend)
            if data[i] == 0x7D:
                return
            i = _skip_space(data, i + 1)  # past ','

    def field(self, f: BindField, i: int, end: int) -> None:
        data, c = self.data, self.data[i]
        if f.kind == "time":
            try:
                value = unmarshal_json_time(data[i:end])
            except GoError as exc:  # returned, not saved: aborts decoding
                raise GoBindError(str(exc)) from exc
            if value is None:
                self.result.pop(f.json_name, None)
            else:
                self.result[f.json_name] = value
        elif f.kind == "string":
            if c == 0x22:
                self.result[f.json_name] = _unquote(data[i + 1 : end - 1])
            elif c == 0x6E:
                if f.pointer:
                    self.result.pop(f.json_name, None)
            else:
                self._save(_json_kind(c), f, f.go_type)
        elif f.kind == "strings":
            if c == 0x5B:
                self.result[f.json_name] = self.string_array(f, i)
            elif c == 0x6E:
                self.result.pop(f.json_name, None)
                self.backing.pop(f.json_name, None)
            else:
                self._save(_json_kind(c), f, f.go_type)
        else:
            raise ValueError(f"unsupported BindField kind {f.kind!r}")

    def string_array(self, f: BindField, i: int) -> list[str]:
        """``decodeState.array`` into an existing ``[]string`` (elements reused in place)."""
        data = self.data
        backing = self.backing.setdefault(f.json_name, [])
        n = 0
        i = _skip_space(data, i + 1)
        if data[i] != 0x5D:
            while True:
                end = _skip_value(data, i)
                if n >= len(backing):
                    backing.append("")
                c = data[i]
                if c == 0x22:
                    backing[n] = _unquote(data[i + 1 : end - 1])
                elif c != 0x6E:  # null into string: no-op (old element kept)
                    self._save(_json_kind(c), f, "string")
                n += 1
                i = _skip_space(data, end)
                if data[i] == 0x5D:
                    break
                i = _skip_space(data, i + 1)
        if n == 0:  # Go: v.Set(reflect.MakeSlice(v.Type(), 0, 0))
            backing.clear()
        return backing[:n]


def bind_json(
    raw: bytes, go_struct: str, fields: Sequence[BindField], *, package: str = "models"
) -> dict[str, Any]:
    """Gin ``c.ShouldBindJSON(&req)`` decode step for request struct ``go_struct``.

    Returns the fields that ended up set (non-nil / non-zero assignment), keyed by JSON
    name: ``str`` for ``"string"``, ``list[str]`` for ``"strings"``, :class:`GoTime` for
    ``"time"``. Absent and ``null`` fields are omitted. Raises :class:`GoBindError` whose
    ``str()`` is Go's ``err.Error()`` (``EOF``, ``unexpected EOF``, syntax errors, the first
    ``UnmarshalTypeError``, or a ``time.Time.UnmarshalJSON`` error). Validation is separate.
    """
    data = _read_value(bytes(raw))
    i = _skip_space(data, 0)
    c = data[i]
    if c == 0x6E:  # top-level null: struct left untouched
        return {}
    if c != 0x7B:
        raise GoBindError(
            f"json: cannot unmarshal {_json_kind(c)} into Go value of type {package}.{go_struct}"
        )
    dec = _StructDecoder(data, go_struct, fields)
    dec.object(i)
    if dec.saved is not None:
        raise GoBindError(dec.saved)
    return dec.result


# ===========================================================================
# END Gin JSON binder section
# ===========================================================================
