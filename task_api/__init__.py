"""Task Management API: Flask drop-in replacement for the Go/Gin service."""

from flask import Flask


def create_app() -> Flask:
    """Application factory (filled in by the handlers/middleware task)."""
    app = Flask(__name__)
    return app
