"""Entry point: Python port of cmd/rest-server/main.go."""

from task_api import create_app


def main() -> None:
    # Startup (PORT, banner, signal handling) is implemented in the handlers/runtime task.
    app = create_app()
    app.run(host="0.0.0.0", port=8080, threaded=True, debug=False)


if __name__ == "__main__":
    main()
