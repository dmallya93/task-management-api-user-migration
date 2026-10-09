"""Black-box contract suite: runs unchanged against the Go oracle and the Flask app.

Usage (server must already be running)::

    # Go reference binary (test oracle only)
    (cd ../task-management-api-dmallya93 && PORT=18080 ./server &)
    # or the Python implementation
    PORT=18080 python app.py &

    BASE_URL=http://localhost:18080 python -m pytest tests/contract -q

Without ``BASE_URL`` every test here is skipped. ``pytest.ini`` does not list
``tests/contract`` in ``testpaths``, so the default ``pytest`` run ignores it.
Tests are self-contained (each creates and deletes its own tasks, list tests
isolate themselves with a unique tag), so the suite may be re-run against the
same server process.
"""

from __future__ import annotations

import time
from typing import Iterator

import pytest
import requests

from tests.contract.helpers import BASE_URL, Client, url


@pytest.fixture(scope="session", autouse=True)
def _server_ready() -> None:
    if not BASE_URL:
        pytest.skip("BASE_URL not set; contract tests need a running server")
    deadline = time.monotonic() + 15
    while True:
        try:
            if requests.get(url("/health"), timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            pytest.fail(f"server at {BASE_URL} did not become healthy")
        time.sleep(0.2)


@pytest.fixture
def client() -> Iterator[Client]:
    c = Client()
    yield c
    c.cleanup()
