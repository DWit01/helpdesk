# Helpdesk

I'm building a helpdesk application using Python and FastAPI to get more comfortable with backend development. It's a learning project that should also help me with the Python work I'm doing on the Sky project.

The longer-term aim is to build a ticket management system where people can raise support requests and track their progress. For now, I'm building the backend one piece at a time.

## Current status

I've finished the initial backend setup and added:

- A FastAPI app with a basic home route and a `/health` endpoint.
- Configuration using Pydantic Settings and environment variables.
- JSON logging with request IDs to help trace requests.
- Ruff, Black and mypy for code checks.
- pytest and a test for the health endpoint.
- pre-commit hooks to run checks before commits.
- A GitHub Actions workflow for backend checks on pull requests to `master`.

The CI workflow has been added, but I'm still verifying its first successful run on GitHub.

## Running locally

The backend is its own uv project inside `backend/`. From the repository root, open PowerShell and run:

```powershell
cd backend
uv sync --locked
uv run uvicorn app.main:app --reload
```

The app runs locally at `http://127.0.0.1:8000` by default.

To check that it's responding, open another PowerShell terminal and run:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

The health endpoint returns:

```json
{"status": "ok"}
```

This is a basic check that the app responds. It doesn't check a database or other services yet.

## Running checks

From `backend/`:

```powershell
uv run ruff check .
uv run black --check .
uv run mypy app tests
uv run pytest -v
```

Ruff checks for linting issues, Black checks formatting, mypy checks types, and pytest checks behaviour. The health endpoint test checks the response status and JSON body.

## Configuration and logging

The backend uses environment variables for settings. `backend/.env.example` shows the available configuration without storing real secrets in Git. Real `.env` files should stay local.

Logs are written as JSON. The request ID middleware generates an ID for requests that don't supply one, returns it in the `X-Request-ID` response header and makes it available to logs for that request.

## What's next

Next I'll finish checking that GitHub Actions runs successfully, then move on to the database and ticket-related backend code. The frontend is planned for a later stage.

Docker Desktop is currently blocked by company security policy, so Docker-dependent work is deferred. I'm continuing with the parts of the project that can run locally without it.
