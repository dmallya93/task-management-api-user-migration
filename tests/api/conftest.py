"""Fixtures for the API tests: a fresh ``create_app()`` (and so a fresh, seeded store) per test.

Routing note: ``create_app`` routes every request in a ``before_request`` hook that reads
the raw request target from ``RAW_URI``/``REQUEST_URI`` (see
:func:`task_api.gin_router.go_request_target`), not from ``PATH_INFO``. ``app.py``'s request
handler sets both keys to the raw request-line target. Werkzeug's test client sets them to
the path exactly as passed to ``client.open`` (no re-quoting) plus ``?query`` when the
query is non-empty, so tests drive the same code path as the real server. The one target
it cannot express is a bare trailing ``?`` (Go's ``ForceQuery``); tests for that use
:func:`tests.api.helpers.open_raw`, which overrides both keys. The ``client`` fixture
asserts on every request that the keys are present.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from task_api import create_app
from task_api.go_compat import GoTime
from tests.api.helpers import FakeClock


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Deterministic ``time.Now()``: every call returns the next whole second after ``T0``.

    Requested before ``app`` (see below), so the two seed tasks get ``T0`` and ``T0+1s``.
    """
    fake = FakeClock()
    monkeypatch.setattr(GoTime, "now", classmethod(lambda cls: fake.now()))
    return fake


@pytest.fixture
def app(request: pytest.FixtureRequest) -> Flask:
    """A brand-new app per test. If the test uses ``clock``, it is installed first."""
    if "clock" in request.fixturenames:
        request.getfixturevalue("clock")
    flask_app = create_app()
    flask_app.config.update(TESTING=False)  # keep gin.Recovery's 500 (no exception propagation)
    return flask_app


class _RawTargetClient(FlaskClient):
    """Test client that checks the app sees the raw request target, like the real server."""

    def open(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        kwargs.setdefault("follow_redirects", False)
        resp = super().open(*args, **kwargs)
        environ = resp.request.environ
        assert environ.get("RAW_URI") == environ.get("REQUEST_URI") is not None
        return resp


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    app.test_client_class = _RawTargetClient
    with app.test_client() as c:
        yield c
