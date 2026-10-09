"""Tests for the Go encoding/json emulation (expected bytes recorded from the Go oracle)."""

from __future__ import annotations

from collections import OrderedDict
from enum import Enum, StrEnum

import pytest
from flask import Flask

from task_api.go_compat import (
    JSON_CONTENT_TYPE,
    GoJSONProvider,
    GoMarshalError,
    GoTime,
    go_decode_utf8,
    go_dumps,
    go_map,
)


def esc(cp: int) -> str:
    return "\\u%04x" % cp


class TestScalars:
    def test_none_bool_int(self) -> None:
        assert go_dumps(None) == "null"
        assert go_dumps(True) == "true"
        assert go_dumps(False) == "false"
        assert go_dumps(0) == "0"
        assert go_dumps(-42) == "-42"
        assert go_dumps(2**63 - 1) == "9223372036854775807"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1.0, "1"),
            (1.5, "1.5"),
            (-0.0, "-0"),
            (0.0, "0"),
            (100.0, "100"),
            (1e20, "100000000000000000000"),
            (1e21, "1e+21"),
            (1e-6, "0.000001"),
            (1e-7, "1e-7"),
            (1.5e-10, "1.5e-10"),
            (0.1, "0.1"),
            (123456.789, "123456.789"),
        ],
    )
    def test_float_formatting(self, value: float, expected: str) -> None:
        assert go_dumps(value) == expected

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_float_unsupported(self, value: float) -> None:
        with pytest.raises(GoMarshalError):
            go_dumps(value)

    def test_unsupported_type(self) -> None:
        with pytest.raises(TypeError):
            go_dumps(object())


class TestStringEscaping:
    def test_html_chars(self) -> None:
        # oracle: title "<>&" -> "<>&"
        assert go_dumps("<>&") == '"' + esc(0x3C) + esc(0x3E) + esc(0x26) + '"'

    def test_line_and_paragraph_separator(self) -> None:
        assert go_dumps("\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}") == (
            '"' + esc(0x2028) + esc(0x2029) + '"'
        )

    def test_all_control_chars(self) -> None:
        # oracle (Go 1.21): only \t \n \r have short forms; \b and \f are \u0008 / \u000c
        s = "".join(chr(c) for c in range(0x20))
        expected = "".join(
            {9: "\\t", 10: "\\n", 13: "\\r"}.get(c, esc(c)) for c in range(0x20)
        )
        assert go_dumps(s) == '"' + expected + '"'
        assert go_dumps("\b\f") == '"\\u0008\\u000c"'

    def test_quote_backslash_slash_del(self) -> None:
        assert go_dumps('/"\\/') == '"/\\"\\\\/"'
        assert go_dumps("\x7f") == '"\x7f"'

    def test_non_ascii_kept(self) -> None:
        s = "\N{GRINNING FACE}\N{LATIN SMALL LETTER E WITH ACUTE} \u0085﻿�"
        assert go_dumps(s) == '"' + s + '"'

    def test_lone_surrogates_become_ufffd_escape(self) -> None:
        assert go_dumps("a" + chr(0xD800) + "b" + chr(0xDCFF)) == '"a\\ufffdb\\ufffd"'

    def test_str_enum_and_enum(self) -> None:
        class S(StrEnum):
            A = "<a>"

        class E(Enum):
            X = 3

        assert go_dumps(S.A) == '"' + esc(0x3C) + "a" + esc(0x3E) + '"'
        assert go_dumps(E.X) == "3"


class TestContainers:
    def test_compact_and_insertion_order(self) -> None:
        d = OrderedDict([("z", 1), ("a", [1, "x"]), ("m", {"k": None})])
        assert go_dumps(d) == '{"z":1,"a":[1,"x"],"m":{"k":null}}'

    def test_go_map_sorted(self) -> None:
        health = go_map(
            status="healthy", timestamp="t", service="task-api-go", version="1.0.0"
        )
        assert go_dumps(health) == (
            '{"service":"task-api-go","status":"healthy","timestamp":"t","version":"1.0.0"}'
        )

    def test_go_map_sorts_bytewise(self) -> None:
        assert go_dumps(go_map({"b": 1, "B": 2, "é": 3, "a": 4})) == (
            '{"B":2,"a":4,"b":1,"é":3}'
        )

    def test_empty_and_null_slices(self) -> None:
        assert go_dumps({"tasks": None, "tags": []}) == '{"tasks":null,"tags":[]}'
        assert go_dumps(()) == "[]"

    def test_keys_are_escaped(self) -> None:
        assert go_dumps({"<k>": 1}) == '{"' + esc(0x3C) + "k" + esc(0x3E) + '":1}'

    def test_to_go_objects(self) -> None:
        class Model:
            def to_go(self) -> dict[str, object]:
                return {"id": "1", "due_date": GoTime(0, 5, 0)}

        assert go_dumps([Model()]) == '[{"id":"1","due_date":"1970-01-01T00:00:00.000000005Z"}]'

    def test_gotime_value(self) -> None:
        t = GoTime.from_parts(2025, 1, 1, 10, 0, 0, 120_000_000, 19800)
        assert go_dumps({"t": t}) == '{"t":"2025-01-01T10:00:00.12+05:30"}'

    def test_gotime_unmarshalable_offset(self) -> None:
        # oracle: due_date "...+24:00" is accepted by the parser but json.Marshal fails
        with pytest.raises(GoMarshalError):
            go_dumps(GoTime(0, 0, 24 * 3600))


class TestDecodeUtf8:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (b"plain", "plain"),
            (b"a\xff\xfeb\xc3", "a��b�"),
            (b"\xed\xa0\x80|\xc0\xaf", "���|��"),
            (b"\xe2\x82Z", "��Z"),  # Python 'replace' would give one U+FFFD
            (b"\xf0\x9f\x98Z", "���Z"),
            (b"\xf4\x90\x80\x80Z", "����Z"),
            (b"Z\xc2", "Z�"),
            ("\N{GRINNING FACE}".encode(), "\N{GRINNING FACE}"),
        ],
    )
    def test_go_replacement(self, raw: bytes, expected: str) -> None:
        # Recorded from the oracle by POSTing these raw bytes as a task title.
        assert go_decode_utf8(raw) == expected


class TestProvider:
    def _app(self) -> Flask:
        app = Flask(__name__)
        app.json = GoJSONProvider(app)
        return app

    def test_response_bytes_and_content_type(self) -> None:
        app = self._app()
        with app.app_context():
            resp = app.json.response({"b": "<x>", "a": 1})
        assert resp.headers["Content-Type"] == JSON_CONTENT_TYPE
        assert resp.get_data() == b'{"b":"\\u003cx\\u003e","a":1}'
        assert resp.status_code == 200

    def test_dumps_and_loads(self) -> None:
        app = self._app()
        assert app.json.dumps({"a": "é"}) == '{"a":"é"}'
        assert app.json.loads('{"a": 1}') == {"a": 1}

    def test_marshal_failure_gives_empty_body(self) -> None:
        app = self._app()
        with app.app_context():
            resp = app.json.response({"due_date": GoTime(0, 0, -25 * 3600)})
        assert resp.get_data() == b""
        assert resp.headers["Content-Type"] == JSON_CONTENT_TYPE

    def test_jsonify_uses_provider(self) -> None:
        from flask import jsonify

        app = self._app()
        with app.app_context():
            resp = jsonify(tasks=None)
        assert resp.get_data() == b'{"tasks":null}'
