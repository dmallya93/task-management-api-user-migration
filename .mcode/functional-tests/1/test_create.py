"""POST /api/v1/tasks"""
import json

import requests

from conftest import TASKS, TIMEOUT, raw

JSON_HDR = {"Content-Type": "application/json"}


def err(code, message):
    return json.dumps({"error": {"code": code, "message": message}}, separators=(",", ":"))


class TestCreate:
    def test_create_minimal(self, observe):
        r = observe(requests.post(TASKS, json={"title": "ftrun minimal"}, timeout=TIMEOUT))
        try:
            assert r.status_code == 201
            body = r.json()
            assert r.headers["Location"] == "/api/v1/tasks/" + body["id"]
            assert r.headers["Content-Type"] == "application/json; charset=utf-8"
            assert list(body.keys()) == ["id", "title", "description", "status", "priority", "tags",
                                         "created_by", "created_at", "updated_at"]
            assert body["priority"] == "medium" and body["status"] == "pending"
            assert body["tags"] == [] and body["description"] == ""
            assert '"tags":[]' in r.text
        finally:
            if r.status_code == 201:
                requests.delete(f"{TASKS}/{r.json()['id']}", timeout=TIMEOUT)

    def test_create_full_html_escape_nanos(self, observe):
        payload = ('{"title":"<a&b> café","description":"d","priority":"critical","tags":["x","y"],'
                   '"assigned_to":"bob","due_date":"2025-01-02T03:04:05.123456789+05:30"}')
        r = observe(raw("POST", TASKS, data=payload.encode(), headers=JSON_HDR))
        try:
            assert r.status_code == 201
            assert '"title":"\\u003ca\\u0026b\\u003e café"' in r.text
            assert '"assigned_to":"bob"' in r.text
            assert '"due_date":"2025-01-02T03:04:05.123456789+05:30"' in r.text
            keys = list(r.json().keys())
            assert keys == ["id", "title", "description", "status", "priority", "tags", "created_by",
                            "assigned_to", "created_at", "updated_at", "due_date"]
        finally:
            if r.status_code == 201:
                requests.delete(f"{TASKS}/{r.json()['id']}", timeout=TIMEOUT)

    def test_create_due_date_trailing_zero_trim(self, observe):
        r = observe(requests.post(TASKS, json={"title": "z", "due_date": "2030-06-01T00:00:00.500000000Z"},
                                  timeout=TIMEOUT))
        try:
            assert r.status_code == 201
            assert '"due_date":"2030-06-01T00:00:00.5Z"' in r.text
        finally:
            if r.status_code == 201:
                requests.delete(f"{TASKS}/{r.json()['id']}", timeout=TIMEOUT)

    def test_create_empty_body(self, observe):
        r = observe(raw("POST", TASKS, data=b"", headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR", "EOF")

    def test_create_syntax_error(self, observe):
        r = observe(raw("POST", TASKS, data=b"{bad", headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR", "invalid character 'b' looking for beginning of object key string")

    def test_create_missing_title(self, observe):
        r = observe(raw("POST", TASKS, data=b"{}", headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR",
                             "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'required' tag")

    def test_create_null_body(self, observe):
        r = observe(raw("POST", TASKS, data=b"null", headers=JSON_HDR))
        assert r.status_code == 400
        assert "failed on the 'required' tag" in r.text

    def test_create_title_too_long(self, observe):
        r = observe(requests.post(TASKS, json={"title": "a" * 201}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR",
                             "Key: 'CreateTaskRequest.Title' Error:Field validation for 'Title' failed on the 'max' tag")

    def test_create_title_max_boundary(self, observe):
        r = observe(requests.post(TASKS, json={"title": "a" * 200}, timeout=TIMEOUT))
        try:
            assert r.status_code == 201
        finally:
            if r.status_code == 201:
                requests.delete(f"{TASKS}/{r.json()['id']}", timeout=TIMEOUT)

    def test_create_title_wrong_type(self, observe):
        r = observe(raw("POST", TASKS, data=b'{"title":1}', headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR",
                             "json: cannot unmarshal number into Go struct field CreateTaskRequest.title of type string")

    def test_create_tags_wrong_type(self, observe):
        r = observe(raw("POST", TASKS, data=b'{"title":"x","tags":"a"}', headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR",
                             "json: cannot unmarshal string into Go struct field CreateTaskRequest.tags of type []string")

    def test_create_priority_wrong_type(self, observe):
        r = observe(raw("POST", TASKS, data=b'{"title":"x","priority":2}', headers=JSON_HDR))
        assert r.status_code == 400
        assert "Go struct field CreateTaskRequest.priority of type models.TaskPriority" in r.text

    def test_create_invalid_priority(self, observe):
        r = observe(requests.post(TASKS, json={"title": "x", "priority": "urgent"}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR", "invalid priority: urgent")

    def test_create_bad_due_date(self, observe):
        r = observe(requests.post(TASKS, json={"title": "x", "due_date": "2025-13-01"}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.text == err("VALIDATION_ERROR", 'parsing time "2025-13-01": month out of range')

    def test_create_case_insensitive_keys(self, observe):
        r = observe(raw("POST", TASKS, data=b'{"TITLE":"upper","Priority":"low","extra":1}', headers=JSON_HDR))
        try:
            assert r.status_code == 201
            assert r.json()["title"] == "upper" and r.json()["priority"] == "low"
        finally:
            if r.status_code == 201:
                requests.delete(f"{TASKS}/{r.json()['id']}", timeout=TIMEOUT)
