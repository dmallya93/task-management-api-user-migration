"""Functional test harness for the Task API (Go origin vs Flask target).

Usage:
    BASE_URL=http://localhost:18080 FT_OUT=/tmp/ft_origin.json pytest .mcode/functional-tests/1 -v

Every test records a *normalized* observation (status, selected headers, raw body
with UUIDs/current timestamps replaced by placeholders) into FT_OUT so that the
origin and target runs can be compared field-for-field (byte order preserved).
"""
import json
import os
import re

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
TASKS = BASE_URL + "/api/v1/tasks"
FT_OUT = os.environ.get("FT_OUT")
TIMEOUT = 10

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# RFC3339Nano for "now" timestamps produced by the server (UTC, Z suffix, trimmed nanos)
NOW_TS_RE = re.compile(r'"(20[2-9]\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z)"')
HEADERS_OF_INTEREST = [
    "Content-Type",
    "Location",
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Methods",
    "Access-Control-Allow-Headers",
]
FIXED_YEAR_PREFIXES = ("2025-", "2030-", "2031-", "2032-")  # client-supplied dates kept verbatim

_observations = {}


def normalize_text(text):
    text = UUID_RE.sub("<UUID>", text)

    def _ts(m):
        val = m.group(1)
        if val.startswith(FIXED_YEAR_PREFIXES):
            return m.group(0)
        return '"<NOW_TS>"'

    return NOW_TS_RE.sub(_ts, text)


def snapshot(resp, normalize=True):
    f = normalize_text if normalize else (lambda x: x)
    hdrs = {h: resp.headers.get(h) for h in HEADERS_OF_INTEREST if resp.headers.get(h) is not None}
    if "Location" in hdrs:
        hdrs["Location"] = f(hdrs["Location"])
    req_body = resp.request.body
    if isinstance(req_body, bytes):
        req_body = req_body.decode("utf-8", "replace")
    return {
        "method": resp.request.method,
        "request_body": req_body,
        "path": f(resp.request.path_url),
        "status": resp.status_code,
        "headers": hdrs,
        "body": f(resp.text),
    }


@pytest.fixture
def observe(request):
    """Collect snapshots of responses for the current test."""
    items, raws = [], []

    def _obs(resp, label=None):
        snap, rsnap = snapshot(resp), snapshot(resp, normalize=False)
        if label:
            snap["label"] = rsnap["label"] = label
        items.append(snap)
        raws.append(rsnap)
        return resp

    yield _obs
    # group parametrized cases under the test function name
    entry = _observations.setdefault(request.node.originalname, {"norm": [], "raw": []})
    entry["norm"].extend(items)
    entry["raw"].extend(raws)


def pytest_sessionfinish(session, exitstatus):
    if FT_OUT:
        with open(FT_OUT, "w") as fh:
            json.dump(_observations, fh, indent=1)


@pytest.fixture(autouse=True)
def _health():
    r = requests.get(BASE_URL + "/health", timeout=TIMEOUT)
    assert r.status_code == 200, "server not healthy"


@pytest.fixture
def make_task():
    """Create tasks via the API and delete them on teardown."""
    created = []

    def _make(**body):
        r = requests.post(TASKS, json=body, timeout=TIMEOUT)
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])
        return r

    yield _make
    for tid in created:
        requests.delete(f"{TASKS}/{tid}", timeout=TIMEOUT)


def raw(method, url, data=None, headers=None, allow_redirects=False):
    return requests.request(method, url, data=data, headers=headers or {},
                            timeout=TIMEOUT, allow_redirects=allow_redirects)
