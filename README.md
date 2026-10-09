# task-management-api-user-migration

Python 3.12+/Flask 3.1 drop-in replacement for the Go/Gin Task Management REST API.
It serves the same endpoints, JSON shapes, status codes and error payloads as the original.

## Run

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PORT=8080 python app.py
```

`PORT` defaults to `8080`; the server binds `0.0.0.0`.

Health check: `curl localhost:8080/health`

## Endpoints

| Method | Path | Success |
|---|---|---|
| GET | `/health` | 200 |
| GET | `/api/v1/tasks` | 200 (`page_size`, `page_token`, `status`, `assigned_to`, `tags`, `sort_order`) |
| POST | `/api/v1/tasks` | 201 + `Location` |
| GET | `/api/v1/tasks/<id>` | 200 |
| PUT | `/api/v1/tasks/<id>` | 200 |
| PATCH | `/api/v1/tasks/<id>/status` | 200 |
| DELETE | `/api/v1/tasks/<id>` | 204 |

## Layout

```
app.py                       # entry point (PORT, startup banner)
task_api/__init__.py         # create_app(): JSON provider, CORS/404/500 hooks
task_api/models.py           # enums, Task, request/response DTOs
task_api/go_compat.py        # Go JSON encoder, RFC3339 time, Gin binder, validator messages
task_api/handlers.py         # Blueprint with the seven routes
task_api/services/task_service.py  # in-memory store
tests/unit, tests/api        # pytest suites (Flask test client)
tests/contract               # black-box HTTP suite driven by BASE_URL
```

## Tests

```sh
pip install -r requirements-dev.txt
pytest                                   # unit + api
BASE_URL=http://localhost:8080 pytest tests/contract
```
