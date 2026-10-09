"""Tests for the Gin ``ShouldBindJSON`` emulation (``go_compat.bind_json``).

Every expected value below was observed on the Go 1.21 / Gin reference server.
"""

from __future__ import annotations

from typing import Any

import pytest

from task_api.go_compat import BindField, GoBindError, GoError, GoTime, bind_json

CREATE = (
    BindField("title", "string", "string", pointer=False),
    BindField("description", "string", "string", pointer=False),
    BindField("priority", "string", "models.TaskPriority", pointer=False),
    BindField("tags", "strings", "[]string"),
    BindField("assigned_to", "string", "string"),
    BindField("due_date", "time", "time.Time"),
)
UPDATE = (
    BindField("title", "string", "string"),
    BindField("description", "string", "string"),
    BindField("status", "string", "models.TaskStatus"),
    BindField("priority", "string", "models.TaskPriority"),
    BindField("tags", "strings", "[]string"),
    BindField("assigned_to", "string", "string"),
    BindField("due_date", "time", "time.Time"),
)
STATUS = (BindField("status", "string", "models.TaskStatus", pointer=False),)


def create(raw: bytes) -> dict[str, Any]:
    return bind_json(raw, "CreateTaskRequest", CREATE)


def bind_error(raw: bytes, struct: str = "CreateTaskRequest", fields=CREATE) -> str:
    with pytest.raises(GoBindError) as info:
        bind_json(raw, struct, fields)
    return str(info.value)


def test_error_type_hierarchy() -> None:
    with pytest.raises(GoError):
        create(b"")
    assert issubclass(GoBindError, GoError)


# --- reading the first value -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"", "EOF"),
        (b"  \n\t\r ", "EOF"),
        (b"{", "unexpected EOF"),
        (b'{"title":"a"', "unexpected EOF"),
        (b'{"title":1,', "unexpected EOF"),
        (b'{"title":"a","x":fals', "unexpected EOF"),
        (b'{"title":"abc', "unexpected EOF"),
        (b'{"title":"a\\', "unexpected EOF"),
        (b"[1,2", "unexpected EOF"),
        (b'"abc', "unexpected EOF"),
        (b"-", "unexpected EOF"),
        (b"1.", "unexpected EOF"),
        (b"1e+", "unexpected EOF"),
        (b"tru", "unexpected EOF"),
        (b"  nul", "unexpected EOF"),
    ],
)
def test_eof(raw: bytes, expected: str) -> None:
    assert bind_error(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [b'{"title":"a"} x', b'{"title":"a"}{', b'{"title":"a"}]', b'{"title":"a"}}}', b'\n{"title":"a"}\n'],
)
def test_only_first_value_is_read(raw: bytes) -> None:
    assert create(raw) == {"title": "a"}


def test_trailing_garbage_after_bad_typed_value() -> None:
    assert bind_error(b'{"title":1} x') == (
        "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string"
    )


@pytest.mark.parametrize(
    "raw, kind",
    [(b"5x", "number"), (b'"s"garbage', "string"), (b"truex", "bool"), (b"[]]", "array"), (b"01", "number")],
)
def test_top_level_scalar_ends_at_first_value(raw: bytes, kind: str) -> None:
    assert bind_error(raw) == f"json: cannot unmarshal {kind} into Go value of type models.CreateTaskRequest"


# --- syntax errors ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b'{"title":}', "invalid character '}' looking for beginning of value"),
        (b"abc", "invalid character 'a' looking for beginning of value"),
        (b"title=x", "invalid character 'i' in literal true (expecting 'r')"),
        (b'{"title":"a",}', "invalid character '}' looking for beginning of object key string"),
        (b"{1:2}", "invalid character '1' looking for beginning of object key string"),
        (b'{"title":"a" "b"}', "invalid character '\"' after object key:value pair"),
        (b'{"title" 1}', "invalid character '1' after object key"),
        (b'{"title":"a","x":[1 2]}', "invalid character '2' after array element"),
        (b'{"title":"a","x":01}', "invalid character '1' after object key:value pair"),
        (b'{"title":"a","x":tx}', "invalid character 'x' in literal true (expecting 'r')"),
        (b'{"x":tre}', "invalid character 'e' in literal true (expecting 'u')"),
        (b'{"x":trux}', "invalid character 'x' in literal true (expecting 'e')"),
        (b'{"x":fx}', "invalid character 'x' in literal false (expecting 'a')"),
        (b'{"x":fale}', "invalid character 'e' in literal false (expecting 's')"),
        (b'{"title":"a","x":nul}', "invalid character '}' in literal null (expecting 'l')"),
        (b'{"x":nx}', "invalid character 'x' in literal null (expecting 'u')"),
        (b'{"title":"a","x":"\x01"}', "invalid character '\\x01' in string literal"),
        (b'{"title":"a\n"}', "invalid character '\\n' in string literal"),
        (b'{"title":"a\t"}', "invalid character '\\t' in string literal"),
        (b'{"title":"a","x":"\\x"}', "invalid character 'x' in string escape code"),
        (b'{"title":"a","x":"\\u12g"}', "invalid character 'g' in \\u hexadecimal character escape"),
        (b'{"title":"a","x":"\\u00"}', "invalid character '\"' in \\u hexadecimal character escape"),
        (b'{"title":"a","x":1.}', "invalid character '}' after decimal point in numeric literal"),
        (b'{"title":"a","x":1e}', "invalid character '}' in exponent of numeric literal"),
        (b'{"x":1e+}', "invalid character '}' in exponent of numeric literal"),
        (b'{"title":"a","x":-}', "invalid character '}' in numeric literal"),
        (b'{"x":-a}', "invalid character 'a' in numeric literal"),
        (b'{"x":+1}', "invalid character '+' looking for beginning of value"),
        (b'{"x":.5}', "invalid character '.' looking for beginning of value"),
        (b"[,]", "invalid character ',' looking for beginning of value"),
        (b"]", "invalid character ']' looking for beginning of value"),
        (b"[1,]", "invalid character ']' looking for beginning of value"),
        (b"{'a':1}", "invalid character '\\'' looking for beginning of object key string"),
        (b'{"title":"a","x":\'}', "invalid character '\\'' looking for beginning of value"),
        (b"\xff", "invalid character 'ÿ' looking for beginning of value"),
        (b'{"title":"a","x":\x80}', "invalid character '\\u0080' looking for beginning of value"),
        (b'{"title":"a","x":\x7f}', "invalid character '\\x7f' looking for beginning of value"),
        (b"\x00", "invalid character '\\x00' looking for beginning of value"),
        (b"\xef\xbb\xbf{}", "invalid character 'ï' looking for beginning of value"),
        (b"[" * 10001, "invalid character '[' exceeded max depth"),
    ],
)
def test_syntax_errors(raw: bytes, expected: str) -> None:
    assert bind_error(raw) == expected


def test_max_depth_boundary() -> None:
    ok = b'{"title":"a","x":' + b"[" * 9999 + b"]" * 9999 + b"}"
    assert create(ok) == {"title": "a"}
    too_deep = b'{"title":"a","x":' + b"[" * 10000 + b"]" * 10000 + b"}"
    assert bind_error(too_deep) == "invalid character '[' exceeded max depth"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"due_date":"bad","title":1,"x":}',
        b'{"title":1,"x":{"y":[1,}]}}',
        b'{"due_date":5,"x":tru }',
    ],
)
def test_whole_value_is_scanned_before_decoding(raw: bytes) -> None:
    assert bind_error(raw).startswith("invalid character ")


# --- top level --------------------------------------------------------------


@pytest.mark.parametrize(
    "struct, fields", [("CreateTaskRequest", CREATE), ("UpdateTaskRequest", UPDATE), ("UpdateTaskStatusRequest", STATUS)]
)
@pytest.mark.parametrize(
    "raw, kind",
    [(b"[]", "array"), (b'[{"title":"a"}]', "array"), (b'"x"', "string"), (b"5", "number"),
     (b"-0.5e3", "number"), (b"true", "bool"), (b"false", "bool")],
)
def test_top_level_non_object(raw: bytes, kind: str, struct: str, fields) -> None:
    assert bind_error(raw, struct, fields) == f"json: cannot unmarshal {kind} into Go value of type models.{struct}"


def test_package_parameter() -> None:
    with pytest.raises(GoBindError, match=r"^json: cannot unmarshal array into Go value of type pkg\.X$"):
        bind_json(b"[]", "X", CREATE, package="pkg")


@pytest.mark.parametrize("raw", [b"null", b" null ", b"{}", b" { } "])
def test_empty_results(raw: bytes) -> None:
    assert create(raw) == {}
    assert bind_json(raw, "UpdateTaskRequest", UPDATE) == {}


# --- type errors ------------------------------------------------------------


def _type_err(kind: str, struct: str, field: str, go_type: str) -> str:
    return f"json: cannot unmarshal {kind} into Go struct field {struct}.{field} of type {go_type}"


@pytest.mark.parametrize(
    "raw, kind, field, go_type",
    [
        (b'{"title":1}', "number", "title", "string"),
        (b'{"title":-0}', "number", "title", "string"),
        (b'{"title":1e5}', "number", "title", "string"),
        (b'{"title":0.5}', "number", "title", "string"),
        (b'{"title":true}', "bool", "title", "string"),
        (b'{"title":false}', "bool", "title", "string"),
        (b'{"title":[]}', "array", "title", "string"),
        (b'{"title":{"a":1}}', "object", "title", "string"),
        (b'{"title":"a","description":5}', "number", "description", "string"),
        (b'{"title":"a","priority":1}', "number", "priority", "models.TaskPriority"),
        (b'{"title":"a","assigned_to":[]}', "array", "assigned_to", "string"),
        (b'{"title":"a","tags":"x"}', "string", "tags", "[]string"),
        (b'{"title":"a","tags":{}}', "object", "tags", "[]string"),
        (b'{"title":"a","tags":true}', "bool", "tags", "[]string"),
        (b'{"title":"a","tags":7}', "number", "tags", "[]string"),
        (b'{"title":"a","tags":[1,"x"]}', "number", "tags", "string"),
        (b'{"title":"a","tags":[["x"]]}', "array", "tags", "string"),
        (b'{"title":"a","tags":["x",{}]}', "object", "tags", "string"),
        (b'{"title":"a","tags":[false]}', "bool", "tags", "string"),
        # field name in the message is the JSON tag, whatever case the key was sent in
        (b'{"TITLE":1}', "number", "title", "string"),
        (b'{"ASSIGNED_TO":1,"title":"a"}', "number", "assigned_to", "string"),
    ],
)
def test_create_type_errors(raw: bytes, kind: str, field: str, go_type: str) -> None:
    assert bind_error(raw) == _type_err(kind, "CreateTaskRequest", field, go_type)


@pytest.mark.parametrize(
    "raw, kind, field, go_type",
    [
        (b'{"status":1}', "number", "status", "models.TaskStatus"),
        (b'{"title":[]}', "array", "title", "string"),
        (b'{"priority":{}}', "object", "priority", "models.TaskPriority"),
        (b'{"tags":"a"}', "string", "tags", "[]string"),
    ],
)
def test_update_type_errors(raw: bytes, kind: str, field: str, go_type: str) -> None:
    assert bind_error(raw, "UpdateTaskRequest", UPDATE) == _type_err(kind, "UpdateTaskRequest", field, go_type)


@pytest.mark.parametrize(
    "raw, kind",
    [(b'{"status":1}', "number"), (b'{"status":true}', "bool"), (b'{"status":[]}', "array"), (b'{"Status":{}}', "object")],
)
def test_status_type_errors(raw: bytes, kind: str) -> None:
    assert bind_error(raw, "UpdateTaskStatusRequest", STATUS) == _type_err(
        kind, "UpdateTaskStatusRequest", "status", "models.TaskStatus"
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b'{"tags":"x","title":1}', _type_err("string", "CreateTaskRequest", "tags", "[]string")),
        (b'{"title":1,"title":"ok"}', _type_err("number", "CreateTaskRequest", "title", "string")),
        (b'{"title":"a","tags":[1,"x"],"description":5}', _type_err("number", "CreateTaskRequest", "tags", "string")),
        (b'{"description":true,"title":[]}', _type_err("bool", "CreateTaskRequest", "description", "string")),
    ],
)
def test_first_type_error_in_document_order_wins(raw: bytes, expected: str) -> None:
    assert bind_error(raw) == expected


# --- time fields ------------------------------------------------------------

_LAYOUT = "2006-01-02T15:04:05Z07:00"


@pytest.mark.parametrize("value", [b"5", b"true", b"{}", b"[]", b"[1]", b'{"a":"b"}', b"-1.5"])
def test_time_non_string(value: bytes) -> None:
    assert bind_error(b'{"title":"a","due_date":' + value + b"}") == "Time.UnmarshalJSON: input is not a JSON string"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b'{"title":"a","due_date":"bad"}', f'parsing time "bad" as "{_LAYOUT}": cannot parse "bad" as "2006"'),
        (b'{"title":"a","due_date":""}', f'parsing time "" as "{_LAYOUT}": cannot parse "" as "2006"'),
        # time errors abort decoding and override an earlier saved type error
        (b'{"title":1,"due_date":"bad"}', f'parsing time "bad" as "{_LAYOUT}": cannot parse "bad" as "2006"'),
        (b'{"title":1,"due_date":[1]}', "Time.UnmarshalJSON: input is not a JSON string"),
        (b'{"tags":"x","due_date":5,"title":true}', "Time.UnmarshalJSON: input is not a JSON string"),
        # last duplicate decoded; error even after a valid value
        (b'{"due_date":"2024-01-02T03:04:05Z","due_date":5}', "Time.UnmarshalJSON: input is not a JSON string"),
        # raw (still escaped) bytes reach the time parser
        (b'{"due_date":"2024-01-02T03:04:05\\u005a"}',
         f'parsing time "2024-01-02T03:04:05\\\\u005a" as "{_LAYOUT}": cannot parse "\\\\u005a" as "Z07:00"'),
    ],
)
def test_time_errors(raw: bytes, expected: str) -> None:
    assert bind_error(raw) == expected


def test_time_error_before_type_error_wins_and_stops() -> None:
    assert bind_error(b'{"due_date":"x","title":1}') == (
        f'parsing time "x" as "{_LAYOUT}": cannot parse "x" as "2006"'
    )


def test_time_values() -> None:
    got = create(b'{"title":"a","due_date":"2024-01-02T03:04:05.120+05:30"}')
    assert isinstance(got["due_date"], GoTime)
    assert str(got["due_date"]) == "2024-01-02T03:04:05.12+05:30"
    assert create(b'{"title":"a","due_date":null}') == {"title": "a"}
    assert create(b'{"title":"a","due_date":"2024-01-02T03:04:05Z","due_date":null}') == {"title": "a"}
    assert str(create(b'{"due_date":"bad","DUE_DATE":"2024-01-02T03:04:05Z"}'.replace(b"bad", b"2023-01-01T00:00:00Z"))["due_date"]) == (
        "2024-01-02T03:04:05Z"
    )
    assert bind_error(b'{"due_date":"bad","DUE_DATE":"2024-01-02T03:04:05Z"}').startswith('parsing time "bad"')


# --- successful decoding ----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            b'{"title":"a","description":"d","priority":"high","tags":["x","y"],'
            b'"assigned_to":"u"}',
            {"title": "a", "description": "d", "priority": "high", "tags": ["x", "y"], "assigned_to": "u"},
        ),
        (b'{"title":"","tags":[]}', {"title": "", "tags": []}),
        (b'{"title":"a","tags":null}', {"title": "a"}),
        (b'{"title":"a","tags":["x",null]}', {"title": "a", "tags": ["x", ""]}),
        (b'{"title":"a","description":null,"assigned_to":null}', {"title": "a"}),
        # unknown keys (incl. other DTOs' fields) are ignored; their values are only scanned
        (b'{"title":"a","id":"x","status":"completed","created_at":5}', {"title": "a"}),
        (b'{"title":"a","x":{"y":[1,2.5e-3,true,false,null,{"z":[]}]},"w":[[],[{}]]}', {"title": "a"}),
        # whitespace everywhere
        (b' \t\n{ "title" :\r\n"a" , "tags" : [ "x" , null ] } ', {"title": "a", "tags": ["x", ""]}),
    ],
)
def test_create_success(raw: bytes, expected: dict[str, Any]) -> None:
    assert create(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # duplicate keys: last wins, case-insensitive
        (b'{"title":"a","title":"b","TITLE":"c"}', {"title": "c"}),
        (b'{"TITLE":"a","Tags":["x"],"De\xc5\xbfcription":"d"}', {"title": "a", "tags": ["x"], "description": "d"}),
        (b'{"\xc5\xbftatus":"x"}', {}),  # U+017F folds to S; "status" is unknown on create
        # null is a no-op on a plain string field, so the earlier value survives
        (b'{"title":"a","title":null}', {"title": "a"}),
        # ... but resets a pointer field
        (b'{"title":"a","assigned_to":"u","assigned_to":null}', {"title": "a"}),
        # []string decoding reuses the previous backing array: null elements expose old values
        (b'{"tags":["a","b","c"],"tags":[null,"x"]}', {"tags": ["a", "x"]}),
        (b'{"tags":["a","b","c"],"tags":[null,null,null,null]}', {"tags": ["a", "b", "c", ""]}),
        (b'{"tags":["a","b","c","d","e"],"tags":["x"],"tags":[null,null,null,null,null,null]}',
         {"tags": ["x", "b", "c", "d", "e", ""]}),
        # [] and null allocate a fresh (empty / nil) slice
        (b'{"tags":["a","b"],"tags":[],"tags":[null]}', {"tags": [""]}),
        (b'{"tags":["a","b"],"tags":null,"tags":[null]}', {"tags": [""]}),
        (b'{"tags":["a"],"tags":null}', {}),
    ],
)
def test_duplicates_and_folding(raw: bytes, expected: dict[str, Any]) -> None:
    assert create(raw) == expected


def test_exact_key_match_preferred_over_fold() -> None:
    fields = (BindField("Title", "string", "string"), BindField("title", "string", "string"))
    assert bind_json(b'{"title":"x"}', "S", fields) == {"title": "x"}
    assert bind_json(b'{"TITLE":"y"}', "S", fields) == {"Title": "y"}
    assert bind_json(b'{"\xe2\x84\xaaey":"k"}', "S", (BindField("key", "string", "string"),)) == {"key": "k"}


def test_update_pointer_semantics() -> None:
    got = bind_json(
        b'{"title":"a","title":null,"status":"completed","tags":[],"description":""}', "UpdateTaskRequest", UPDATE
    )
    assert got == {"status": "completed", "tags": [], "description": ""}


def test_status_request() -> None:
    assert bind_json(b'{"status":"done","STATUS":"completed"}', "UpdateTaskStatusRequest", STATUS) == {
        "status": "completed"
    }
    assert bind_json(b'{"status":"x","status":null}', "UpdateTaskStatusRequest", STATUS) == {"status": "x"}
    assert bind_json(b"null", "UpdateTaskStatusRequest", STATUS) == {}


# --- string decoding --------------------------------------------------------


@pytest.mark.parametrize(
    "literal, expected",
    [
        (b'"\\u00e9"', "é"),
        (b'"\xc3\xa9"', "é"),
        (b'"\\ud83d\\ude00"', "\U0001f600"),
        (b'"\\ud800\\udc00"', "\U00010000"),
        (b'"\\ud800"', "�"),
        (b'"\\ud800x"', "�x"),
        (b'"\\udc00\\ud800"', "��"),
        (b'"\\ud800\\u0041"', "�A"),
        (b'"\\ud800\\ud800\\udc00"', "�\U00010000"),
        (b'"\xff\xfe"', "��"),
        (b'"\xe2\x82Z"', "��Z"),
        (b'"\xed\xa0\x80"', "���"),
        (b'"\\"\\\\\\/\\b\\f\\n\\r\\t"', '"\\/\b\f\n\r\t'),
        (b'"\\u0000"', "\x00"),
        (b'"\\u005A\\u005a"', "ZZ"),
        (b'"\x7f\xc2\x80"', "\x7f\x80"),
        (b'"\xff\xe2\x82Z\\ud800\\udc00\\udc00\\ud800\\u0041\\u00e9\xc3\xa9"', "���Z\U00010000��Aéé"),
    ],
)
def test_string_values(literal: bytes, expected: str) -> None:
    assert create(b'{"title":' + literal + b"}") == {"title": expected}
    assert create(b'{"tags":[' + literal + b"]}") == {"tags": [expected]}


def test_escaped_key_matches_field() -> None:
    assert create(b'{"\\u0074itle":"a","t\\u0061gs":["b"]}') == {"title": "a", "tags": ["b"]}


def test_bytes_like_input() -> None:
    assert create(bytearray(b'{"title":"a"}')) == {"title": "a"}
