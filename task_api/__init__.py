"""Task Management API: Flask drop-in replacement for the Go/Gin service.

:func:`create_app` reproduces ``cmd/rest-server/main.go``'s engine:
``gin.New()`` + ``gin.Logger()`` + ``gin.Recovery()`` + ``corsMiddleware()`` with the routes from
``rest/server/handlers.go``. Gin's request flow is emulated in a ``before_request`` hook.

1. Look the method/path up in Gin's radix tree (:class:`~task_api.gin_router.GinRouter`).
2. No match but a trailing-slash recommendation: redirect (301 for GET, else 307). This
   happens in the router, before any middleware runs, so there are no CORS headers.
3. Otherwise run the middleware chain. CORS headers go on every response, and OPTIONS
   aborts with 204. An unmatched route falls through to Gin's plain-text 404.
4. A matched route is dispatched by Flask with the Gin path params as ``view_args``.

Unhandled exceptions become an empty 500 (``gin.Recovery``). The access log line is
written by the server's request handler (see ``app.py``).
"""

from __future__ import annotations

import re
import sys
from http import HTTPStatus
import traceback
from typing import Any

from flask import Flask, Response, current_app, g, request
from werkzeug.routing import Rule

from task_api.gin_router import GinRouter, go_request_target, trailing_slash_redirect
from task_api.go_compat import GoJSONProvider
from task_api.handlers import TASK_SERVICE_KEY, bp, empty_response
from task_api.services.task_service import TaskService

__all__ = ["create_app", "CORS_HEADERS"]

#: ``corsMiddleware`` in main.go.
CORS_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Origin, Content-Type, Accept, Authorization",
}

_ROUTER_KEY = "gin_router"
_NO_CORS = "_task_api_no_cors"
_FLASK_PARAM_RE = re.compile(r"<(?:[^<>:]+:)?([^<>]+)>")


def _plain_404() -> Response:
    """Gin ``serveError(c, 404, "404 page not found")``: ``text/plain`` without a charset."""
    return Response(b"404 page not found", 404, content_type="text/plain")


def _go_bad_request() -> Response:
    """net/http's reply to an unparsable request target, which happens before Gin runs."""
    return Response(b"400 Bad Request", 400, content_type="text/plain; charset=utf-8")


class _RedirectResponse(Response):
    """Sends ``Location`` byte-for-byte. Werkzeug would otherwise IRI-quote it (e.g. ``<``)."""

    def __init__(self, body: bytes, status: int, location: str) -> None:
        super().__init__(body, status)
        self.raw_location = location

    def get_wsgi_headers(self, environ: Any) -> Any:
        headers = super().get_wsgi_headers(environ)
        headers["Location"] = self.raw_location
        return headers


def _build_router(app: Flask) -> GinRouter:
    """Mirror the Flask rules into Gin trees (same insertion order as handlers.go)."""
    router = GinRouter()
    rule: Rule
    for rule in app.url_map.iter_rules():
        gin_path = _FLASK_PARAM_RE.sub(r":\1", rule.rule)
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            router.add(method, gin_path, rule)
    return router


def _gin_route() -> Response | None:
    """``before_request``: Gin's ``handleHTTPRequest`` up to the handler call."""
    target = go_request_target(request.environ)
    if not target.valid:
        g.setdefault(_NO_CORS, True)
        return _go_bad_request()

    method = request.method
    router: GinRouter = current_app.extensions[_ROUTER_KEY]
    match = router.lookup(method, target.path)

    if match is not None and match.handler is None:
        if method != "CONNECT" and target.path != "/" and match.tsr:
            spec = trailing_slash_redirect(
                method,
                target.path,
                target.raw_query,
                target.force_query,
                request.headers.get("X-Forwarded-Prefix", ""),
            )
            resp = _RedirectResponse(spec.body or b"", spec.status, spec.location)
            del resp.headers["Content-Type"]
            if spec.content_type is not None:
                resp.headers["Content-Type"] = spec.content_type
            g.setdefault(_NO_CORS, True)
            return resp

    # Middleware chain: corsMiddleware aborts OPTIONS with 204, matched or not.
    if method == "OPTIONS":
        return empty_response(204)
    if match is None or match.handler is None:
        return _plain_404()

    # Dispatch through Flask with Gin's path params (Go strings -> surrogateescape str).
    request.routing_exception = None
    request.url_rule = match.handler
    request.view_args = {
        key: value.encode("latin-1").decode("utf-8", "surrogateescape")
        for key, value in match.params
    }
    return None


def _finish(resp: Response) -> Response:
    """``after_request``: CORS headers on everything except router-level redirects.

    Also uses net/http's reason phrase (``301 Moved Permanently``), not Werkzeug's upper case.
    """
    if not g.get(_NO_CORS):
        resp.headers.update(CORS_HEADERS)
    try:
        resp.status = f"{resp.status_code} {HTTPStatus(resp.status_code).phrase}"
    except ValueError:
        pass
    return resp


def _recover(exc: Exception) -> Response:
    """``gin.Recovery``: any panic becomes an empty 500. The stack goes to stderr."""
    print(f"[Recovery] panic recovered: {exc!r}", file=sys.stderr)
    traceback.print_exception(exc, file=sys.stderr)
    resp = Response(b"", 500)
    del resp.headers["Content-Type"]
    return resp


def create_app() -> Flask:
    """Build the Flask app with a fresh, seeded :class:`TaskService`."""
    app = Flask(__name__, static_folder=None)
    app.json = GoJSONProvider(app)
    app.url_map.strict_slashes = True
    app.url_map.merge_slashes = False

    app.extensions[TASK_SERVICE_KEY] = TaskService()
    app.register_blueprint(bp)
    app.extensions[_ROUTER_KEY] = _build_router(app)

    app.before_request(_gin_route)
    app.after_request(_finish)
    app.register_error_handler(Exception, _recover)
    return app
