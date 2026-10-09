"""Entry point: Python port of cmd/rest-server/main.go.

Reads ``PORT`` (default 8080), prints main.go's three startup lines and serves on
``0.0.0.0`` with ``app.run(threaded=True, debug=False)``. SIGINT/SIGTERM shut down
cleanly (exit code 0, no traceback), logging like ``log.Println``.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from http import HTTPStatus
from types import FrameType

import flask.cli
from werkzeug.serving import WSGIRequestHandler

from task_api import create_app

#: main.go waits up to 5s for in-flight requests (srv.Shutdown with a timeout).
SHUTDOWN_TIMEOUT = 5.0

_log_lock = threading.Lock()


def _go_log(message: str) -> None:
    """Go ``log.Println``: stderr, ``2006/01/02 15:04:05`` prefix."""
    print(f"{time.strftime('%Y/%m/%d %H:%M:%S')} {message}", file=sys.stderr, flush=True)


class AccessLogRequestHandler(WSGIRequestHandler):
    """Werkzeug handler with one ``gin.Logger``-style stdout line per request and no ``Server`` header."""

    _started: float = 0.0

    def run_wsgi(self) -> None:
        self._started = time.perf_counter()
        super().run_wsgi()

    def make_environ(self) -> dict:  # type: ignore[type-arg]
        environ = super().make_environ()
        # http.server (3.12) collapses a leading "//" in self.path and Werkzeug re-encodes
        # raw non-ASCII bytes. Hand the app the request target exactly as received
        # (latin-1, per WSGI) so routing sees what Go's server would.
        words = self.requestline.split()
        if len(words) >= 2:
            environ["RAW_URI"] = environ["REQUEST_URI"] = words[1]
        return environ

    def send_header(self, keyword: str, value: str) -> None:
        if keyword.lower() == "server":  # Go sends no Server header
            return
        super().send_header(keyword, value)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if isinstance(code, HTTPStatus):
            code = code.value
        latency_ms = (time.perf_counter() - self._started) * 1000 if self._started else 0.0
        target = getattr(self, "path", None)
        request = f"{self.command} {target}" if target is not None else self.requestline
        request = request.translate(self._control_char_table)
        line = (
            f"[FLASK] {time.strftime('%Y/%m/%d - %H:%M:%S')} | {code!s:>3} | "
            f"{latency_ms:10.3f}ms | {self.address_string():>15} | {request}"
        )
        with _log_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()


def _install_signal_handlers() -> None:
    def on_signal(signum: int, frame: FrameType | None) -> None:
        # Like main.go's buffered channel, only the first signal counts.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _go_log("Shutting down server...")
        raise KeyboardInterrupt  # Werkzeug's serve_forever catches it and closes the socket

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)


def _wait_for_inflight(timeout: float) -> None:
    """Give request threads up to ``timeout`` seconds to finish, like ``srv.Shutdown(ctx)``."""
    deadline = time.monotonic() + timeout
    current = threading.current_thread()
    for thread in threading.enumerate():
        if thread is current or not thread.is_alive():
            continue
        thread.join(max(0.0, deadline - time.monotonic()))


def main() -> None:
    port = os.environ.get("PORT", "") or "8080"

    # Keep stdout to main.go's banner plus the access log: silence Werkzeug's startup
    # lines and Flask's " * Serving Flask app" banner.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    flask.cli.show_server_banner = lambda *args, **kwargs: None

    app = create_app()
    _install_signal_handlers()

    print(f"REST API server starting on http://localhost:{port}", flush=True)
    print(f"Health check: http://localhost:{port}/health", flush=True)
    print(f"API endpoint: http://localhost:{port}/api/v1/tasks", flush=True)

    try:
        port_number = int(port)
        if not 0 <= port_number <= 65535:
            raise ValueError(port)
    except ValueError:
        _go_log(f"Failed to start server: listen tcp: address {port}: invalid port")
        sys.exit(1)

    try:
        app.run(
            host="0.0.0.0",
            port=port_number,
            threaded=True,
            debug=False,
            load_dotenv=False,
            request_handler=AccessLogRequestHandler,
        )
    except KeyboardInterrupt:
        pass  # a signal that arrived outside serve_forever (e.g. during startup)

    _wait_for_inflight(SHUTDOWN_TIMEOUT)
    _go_log("Server shutdown complete")


if __name__ == "__main__":
    main()
