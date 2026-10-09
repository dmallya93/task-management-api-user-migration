"""Gin router semantics: CORS, OPTIONS, 404 (never 405), trailing slash redirects."""
import pytest

from conftest import BASE_URL, TASKS, raw


class TestRouting:
    @pytest.mark.parametrize("path", ["/api/v1/tasks", "/anything/at/all", "/health"])
    def test_options_204(self, observe, path):
        r = observe(raw("OPTIONS", BASE_URL + path))
        assert r.status_code == 204
        assert r.text == ""
        assert r.headers["Access-Control-Allow-Methods"] == "GET, POST, PUT, PATCH, DELETE, OPTIONS"

    def test_unknown_path_404(self, observe):
        r = observe(raw("GET", BASE_URL + "/nope"))
        assert r.status_code == 404
        assert r.text == "404 page not found"
        assert r.headers["Content-Type"] == "text/plain"
        assert r.headers["Access-Control-Allow-Origin"] == "*"

    @pytest.mark.parametrize("method,path", [
        ("DELETE", "/api/v1/tasks"), ("POST", "/health"), ("PATCH", "/api/v1/tasks/x"),
    ])
    def test_wrong_method_404_not_405(self, observe, method, path):
        r = observe(raw(method, BASE_URL + path))
        assert r.status_code == 404
        assert r.text == "404 page not found"

    def test_trailing_slash_get_301(self, observe):
        r = observe(raw("GET", TASKS + "/"))
        assert r.status_code == 301
        assert r.headers["Location"] == "/api/v1/tasks"

    def test_trailing_slash_post_307(self, observe):
        r = observe(raw("POST", TASKS + "/", data=b'{"title":"x"}'))
        assert r.status_code == 307
        assert r.headers["Location"] == "/api/v1/tasks"

    def test_trailing_slash_unknown_404(self, observe):
        r = observe(raw("GET", BASE_URL + "/nope/"))
        assert r.status_code == 404
