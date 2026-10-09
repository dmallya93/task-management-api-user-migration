"""Port of Gin v1.9.1's router: the radix tree (``tree.go``) and trailing-slash redirect (``gin.go``).

Gin's routing quirks come from this tree, not from a simple "does the path match?" rule.
Werkzeug's matcher cannot reproduce them:

* a ``:param`` may match the empty string when more path follows
  (``PATCH /api/v1/tasks//status`` runs the handler with ``id == ""``);
* the trailing-slash recommendation (``tsr``) fires in several tree states, sometimes
  for a target that does not exist (``PATCH /api/v1/tasks/x/`` -> 307 to ``/api/v1/tasks/x``);
* HEAD/OPTIONS have no tree, so they never match or redirect.

Paths are handled as Go strings, i.e. byte strings. Here they are ``str`` values whose
code points are the bytes (latin-1), so indexing is byte-wise like Go.

Only static segments and ``:param`` wildcards are supported. This API has no
``*catchAll`` routes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import unquote_to_bytes

__all__ = [
    "GinRouter",
    "RouteMatch",
    "RedirectSpec",
    "go_path_clean",
    "go_escape_path",
    "trailing_slash_redirect",
    "RequestTarget",
    "go_request_target",
]

_STATIC, _ROOT, _PARAM = 0, 1, 2


@dataclass(eq=False)
class _Node:
    """Go ``node``; ``handler`` is the route payload (``None`` = no handlers)."""

    path: str = ""
    indices: str = ""
    wild_child: bool = False
    n_type: int = _STATIC
    priority: int = 0
    children: list[_Node] = field(default_factory=list)
    handler: Any = None

    def add_child(self, child: _Node) -> None:
        # Keep the wildcard child at the end.
        if self.wild_child and self.children:
            self.children.insert(len(self.children) - 1, child)
        else:
            self.children.append(child)

    def increment_child_prio(self, pos: int) -> int:
        cs = self.children
        cs[pos].priority += 1
        prio = cs[pos].priority
        new_pos = pos
        while new_pos > 0 and cs[new_pos - 1].priority < prio:
            cs[new_pos - 1], cs[new_pos] = cs[new_pos], cs[new_pos - 1]
            new_pos -= 1
        if new_pos != pos:
            ind = self.indices
            self.indices = ind[:new_pos] + ind[pos] + ind[new_pos:pos] + ind[pos + 1 :]
        return new_pos

    # -- insertion (tree.go addRoute / insertChild) --------------------------

    def add_route(self, path: str, handler: Any) -> None:
        full_path = path
        n = self
        n.priority += 1

        if not n.path and not n.children:
            n.insert_child(path, full_path, handler)
            n.n_type = _ROOT
            return

        while True:
            i = _longest_common_prefix(path, n.path)

            if i < len(n.path):
                child = _Node(
                    path=n.path[i:],
                    wild_child=n.wild_child,
                    n_type=_STATIC,
                    indices=n.indices,
                    children=n.children,
                    handler=n.handler,
                    priority=n.priority - 1,
                )
                n.children = [child]
                n.indices = n.path[i]
                n.path = path[:i]
                n.handler = None
                n.wild_child = False

            if i < len(path):
                path = path[i:]
                c = path[0]

                if n.n_type == _PARAM and c == "/" and len(n.children) == 1:
                    n = n.children[0]
                    n.priority += 1
                    continue

                next_node: _Node | None = None
                for idx, ic in enumerate(n.indices):
                    if c == ic:
                        idx = n.increment_child_prio(idx)
                        next_node = n.children[idx]
                        break
                if next_node is not None:
                    n = next_node
                    continue

                if c not in ":*":
                    n.indices += c
                    child = _Node()
                    n.add_child(child)
                    n.increment_child_prio(len(n.indices) - 1)
                    n = child
                elif n.wild_child:
                    n = n.children[-1]
                    n.priority += 1
                    if (
                        len(path) >= len(n.path)
                        and path.startswith(n.path)
                        and (len(n.path) >= len(path) or path[len(n.path)] == "/")
                    ):
                        continue
                    raise ValueError(
                        f"{path!r} in new path {full_path!r} conflicts with existing wildcard {n.path!r}"
                    )

                n.insert_child(path, full_path, handler)
                return

            if n.handler is not None:
                raise ValueError(f"handlers are already registered for path {full_path!r}")
            n.handler = handler
            return

    def insert_child(self, path: str, full_path: str, handler: Any) -> None:
        n = self
        while True:
            wildcard, i, valid = _find_wildcard(path)
            if i < 0:
                break
            if not valid:
                raise ValueError(f"only one wildcard per path segment is allowed in {full_path!r}")
            if len(wildcard) < 2:
                raise ValueError(f"wildcards must be named with a non-empty name in {full_path!r}")
            if wildcard[0] != ":":
                raise ValueError(f"catch-all routes are not supported: {full_path!r}")

            if i > 0:
                n.path = path[:i]
                path = path[i:]

            child = _Node(n_type=_PARAM, path=wildcard)
            n.add_child(child)
            n.wild_child = True
            n = child
            n.priority += 1

            if len(wildcard) < len(path):
                path = path[len(wildcard) :]
                child = _Node(priority=1)
                n.add_child(child)
                n = child
                continue

            n.handler = handler
            return

        n.path = path
        n.handler = handler

    # -- lookup (tree.go getValue, unescape=false) ---------------------------

    def get_value(self, path: str) -> RouteMatch:
        n = self
        params: list[tuple[str, str]] = []
        # Go skippedNode: (path, node, paramsCount). The tree isn't mutated during
        # lookup, so the node itself can stand in for Go's shallow copy.
        skipped: list[tuple[str, _Node, int]] = []
        params_count = 0

        def roll_back() -> bool:
            nonlocal path, n, params_count
            while skipped:
                s_path, s_node, s_count = skipped.pop()
                if s_path.endswith(path):
                    path, n, params_count = s_path, s_node, s_count
                    del params[s_count:]
                    return True
            return False

        while True:
            prefix = n.path
            if len(path) > len(prefix) and path[: len(prefix)] == prefix:
                path = path[len(prefix) :]

                # Try all the non-wildcard children first.
                idxc = path[0]
                next_node: _Node | None = None
                for idx, c in enumerate(n.indices):
                    if c == idxc:
                        if n.wild_child:
                            skipped.append((prefix + path, n, params_count))
                        next_node = n.children[idx]
                        break
                if next_node is not None:
                    n = next_node
                    continue

                if not n.wild_child:
                    if path != "/" and roll_back():
                        continue
                    return RouteMatch(None, params, tsr=path == "/" and n.handler is not None)

                # Wildcard child (always :param here).
                n = n.children[-1]
                params_count += 1
                end = path.find("/")
                if end < 0:
                    end = len(path)
                params.append((n.path[1:], path[:end]))

                if end < len(path):
                    if n.children:
                        path = path[end:]
                        n = n.children[0]
                        continue
                    return RouteMatch(None, params, tsr=len(path) == end + 1)

                if n.handler is not None:
                    return RouteMatch(n.handler, params)
                tsr = False
                if len(n.children) == 1:
                    n = n.children[0]
                    tsr = (n.path == "/" and n.handler is not None) or (
                        n.path == "" and n.indices == "/"
                    )
                return RouteMatch(None, params, tsr=tsr)

            if path == prefix:
                if n.handler is None and path != "/" and roll_back():
                    continue
                if n.handler is not None:
                    return RouteMatch(n.handler, params)
                if path == "/" and n.wild_child and n.n_type != _ROOT:
                    return RouteMatch(None, params, tsr=True)
                if path == "/" and n.n_type == _STATIC:
                    return RouteMatch(None, params, tsr=True)
                for idx, c in enumerate(n.indices):
                    if c == "/":
                        child = n.children[idx]
                        return RouteMatch(
                            None, params, tsr=len(child.path) == 1 and child.handler is not None
                        )
                return RouteMatch(None, params)

            # Nothing found: maybe recommend the path with an extra trailing slash.
            tsr = path == "/" or (
                len(prefix) == len(path) + 1
                and prefix[len(path)] == "/"
                and path == prefix[:-1]
                and n.handler is not None
            )
            if not tsr and path != "/" and roll_back():
                continue
            return RouteMatch(None, params, tsr=tsr)


def _longest_common_prefix(a: str, b: str) -> int:
    i, m = 0, min(len(a), len(b))
    while i < m and a[i] == b[i]:
        i += 1
    return i


def _find_wildcard(path: str) -> tuple[str, int, bool]:
    for start, c in enumerate(path):
        if c not in ":*":
            continue
        valid = True
        for end in range(start + 1, len(path)):
            if path[end] == "/":
                return path[start:end], start, valid
            if path[end] in ":*":
                valid = False
        return path[start:], start, valid
    return "", -1, False


@dataclass
class RouteMatch:
    """Go ``nodeValue``: ``handler`` is ``None`` when nothing matched."""

    handler: Any
    params: list[tuple[str, str]]
    tsr: bool = False


class GinRouter:
    """Gin ``engine.trees``: one radix tree per HTTP method."""

    def __init__(self) -> None:
        self._trees: dict[str, _Node] = {}

    def add(self, method: str, path: str, handler: Any) -> None:
        if not path.startswith("/"):
            raise ValueError("path must begin with '/'")
        self._trees.setdefault(method, _Node()).add_route(path, handler)

    def lookup(self, method: str, path: str) -> RouteMatch | None:
        """Look ``path`` up in the tree for ``method``. Returns ``None`` if there is no tree for the method."""
        root = self._trees.get(method)
        if root is None:
            return None
        return root.get_value(path)


# ---------------------------------------------------------------------------
# Trailing-slash redirect (gin.go redirectTrailingSlash + net/http http.Redirect)
# ---------------------------------------------------------------------------

_SAFE_PREFIX_RE = re.compile(r"[^a-zA-Z0-9/-]+")
_REPEATED_SLASH_RE = re.compile(r"/{2,}")
# net/url shouldEscape(c, encodePath) == false
_PATH_UNESCAPED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~$&+,/:;=@"
)
_HTML_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&#34;", "'": "&#39;"})


def go_path_clean(p: str) -> str:
    """Go ``path.Clean``."""
    if p == "":
        return "."
    rooted = p.startswith("/")
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not rooted:
                parts.append("..")
            continue
        parts.append(seg)
    out = "/".join(parts)
    if rooted:
        out = "/" + out
    return out or "."


def go_escape_path(p: str) -> str:
    """Go ``url.escape(p, encodePath)`` on a byte string (uppercase hex)."""
    return "".join(c if c in _PATH_UNESCAPED else f"%{ord(c):02X}" for c in p)


def _hex_escape_non_ascii(s: str) -> str:
    return "".join(f"%{ord(c):x}" if ord(c) >= 0x80 else c for c in s)


@dataclass
class RedirectSpec:
    """A redirect as written by ``http.Redirect``. ``body`` is ``None`` when there is none; ``content_type`` likewise."""

    status: int
    location: str
    content_type: str | None
    body: bytes | None


def trailing_slash_redirect(
    method: str, path: str, raw_query: str, force_query: bool, forwarded_prefix: str
) -> RedirectSpec:
    """Gin ``redirectTrailingSlash`` followed by ``redirectRequest`` and ``http.Redirect``.

    ``path`` is the decoded request path (``URL.Path``) as a byte string. ``raw_query``
    is the raw query string (``URL.RawQuery``). ``force_query`` is true for a bare
    trailing ``?``. ``forwarded_prefix`` is the ``X-Forwarded-Prefix`` header.
    """
    p = path
    prefix = go_path_clean(forwarded_prefix)
    if prefix != ".":
        prefix = _SAFE_PREFIX_RE.sub("", prefix)
        prefix = _REPEATED_SLASH_RE.sub("/", prefix)
        p = prefix + "/" + path
    new_path = p + "/"
    if len(p) > 1 and p.endswith("/"):
        new_path = p[:-1]

    # url.URL.String() with only Path/RawQuery set.
    url = go_escape_path(new_path)
    if url and not url.startswith("/") and ":" in url.partition("/")[0]:
        url = "./" + url
    if force_query or raw_query:
        url += "?" + raw_query

    status = 301 if method == "GET" else 307

    # http.Redirect: only a URL without scheme/host (and that url.Parse accepts) is cleaned.
    has_ctl = any(ord(c) < 0x20 or ord(c) == 0x7F for c in url)
    if not url.startswith("//") and not has_ctl:
        oldpath = new_path or "/"
        if url == "" or url[0] != "/":
            url = oldpath[: oldpath.rfind("/") + 1] + url
        url, q, query = url.partition("?")
        trailing = url.endswith("/")
        url = go_path_clean(url)
        if trailing and not url.endswith("/"):
            url += "/"
        url += q + query

    if method in ("GET", "HEAD"):
        content_type: str | None = "text/html; charset=utf-8"
    else:
        content_type = None
    body: bytes | None = None
    if method == "GET":
        href = url.translate(_HTML_ESCAPES)
        body = f'<a href="{href}">Moved Permanently</a>.\n\n'.encode("latin-1")
    return RedirectSpec(status, _hex_escape_non_ascii(url), content_type, body)


# ---------------------------------------------------------------------------
# Request target (net/http readRequest -> url.ParseRequestURI)
# ---------------------------------------------------------------------------

_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_BAD_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENVIRON_KEY = "task_api.request_target"


@dataclass(frozen=True)
class RequestTarget:
    """The parts of Go's ``*url.URL`` that Gin uses. Strings are byte strings (latin-1).

    ``valid`` is false when Go's server rejects the request target. It then replies
    ``400 Bad Request`` before Gin runs.
    """

    path: str
    raw_query: str
    force_query: bool
    valid: bool = True


def _parse_request_uri(raw: str) -> RequestTarget:
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        return RequestTarget("", "", False, valid=False)
    if raw == "*":
        return RequestTarget("*", "", False)

    rest = raw
    scheme = ""
    if m := _SCHEME_RE.match(rest):
        scheme, rest = m.group()[:-1], rest[m.end() :]

    force_query = False
    raw_query = ""
    if rest.endswith("?") and rest.count("?") == 1:
        force_query, rest = True, rest[:-1]
    else:
        rest, _, raw_query = rest.partition("?")

    if not rest.startswith("/"):
        if scheme and rest:
            return RequestTarget("", raw_query, force_query)  # opaque URL
        return RequestTarget("", "", False, valid=False)
    if scheme and rest.startswith("//"):
        slash = rest.find("/", 2)
        rest = rest[slash:] if slash >= 0 else ""

    if _BAD_ESCAPE_RE.search(rest):
        return RequestTarget("", "", False, valid=False)
    path = unquote_to_bytes(rest.encode("latin-1")).decode("latin-1")
    return RequestTarget(path, raw_query, force_query)


def go_request_target(environ: Mapping[str, Any]) -> RequestTarget:
    """Parse the raw request target the way Go's server does (result cached in ``environ``).

    The raw URI (``RAW_URI``/``REQUEST_URI``, set by Werkzeug's server and test client)
    is used instead of ``PATH_INFO``. Werkzeug's server folds a leading ``//`` into the
    path and decodes ``%XX`` leniently, but Go keeps ``//`` and rejects bad escapes.
    """
    cached = environ.get(_ENVIRON_KEY)
    if cached is not None:
        return cached
    raw = environ.get("RAW_URI") or environ.get("REQUEST_URI")
    if raw:
        target = _parse_request_uri(raw)
    else:
        target = RequestTarget(environ.get("PATH_INFO", "") or "/", environ.get("QUERY_STRING", ""), False)
    if isinstance(environ, dict):
        environ[_ENVIRON_KEY] = target
    return target
