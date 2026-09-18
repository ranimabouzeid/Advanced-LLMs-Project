# Local Python environment (Phase 1)

This project uses Python 3.11.9 in `.venv`. Python 3.11 was installed for
the Windows user, and the original Python 3.13 environment was recreated.

From the repository root in a Windows PowerShell terminal:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Run the creation command only if `.venv` does not already exist.
The explicit interpreter path also works when the legacy `py` launcher does
not detect installed interpreters. Python 3.13 can remain installed separately.
Activation is optional: using the environment's executable directly keeps
commands isolated without changing PowerShell execution policy.
To activate in a terminal that permits scripts:

```powershell
.\.venv\Scripts\Activate.ps1
```

In VS Code, run **Python: Select Interpreter** from the Command Palette and
select `.venv\Scripts\python.exe` (the Microsoft Python extension is required).

`backend/requirements.txt` lists the seven direct dependencies without version
pins; future installations can resolve newer versions.

## Current scope

- `backend/app/warehouse/` contains the domain models and deterministic simulation.
- `backend/tests/` contains the deterministic test suite.
- `frontend/` is reserved for a later phase.
- `docs/` contains setup documentation.
- `.venv/` is local and ignored by Git.

Phase 1 added environment setup only. The subsequent user-authorized Phase 2 added
domain models and basic simulation; see [warehouse usage](warehouse.md).
Subsequent milestones added A*, four LangGraph roles, command orchestration,
process-local sessions, and the thin FastAPI service. React remains future work.

Run the tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The repository-local `pytest.ini` configures test discovery and backend imports.

## Optional LLM configuration

The shared settings/factory are in `backend/app/config.py`. Inspection found
LangChain 1.4.0, langchain-core 1.6.3, Pydantic 2.13.5, python-dotenv 1.2.3,
and transitive pydantic-settings 2.15.0, but no provider integration package.
The implementation uses the existing direct Pydantic/python-dotenv dependencies;
requirements and installed packages were not changed.

Offline tests inject fake clients and require no credentials. For future live
Groq setup, install `langchain-groq` from backend requirements in the
project virtual environment. Copy `.env.example` to ignored `.env`, replace its
placeholders, and load it explicitly with `load_settings(env_file=".env")`.
Create one client during application setup and pass it to future consumers.
See [configuration and ownership](architecture.md#shared-llm-configuration).

## Milestone F API

The API uses existing installed FastAPI, Starlette, HTTPX, Pydantic and Uvicorn
dependencies. No dependency upgrades or additional infrastructure were added.
The default app creates one model client and coordinator during lifespan startup,
never during import. Live startup requires configured model settings and the
Groq integration described above. Startup configuration errors fail
startup; the application does not silently substitute a fake client.

From the repository root, after configuring the ignored `.env`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --app-dir backend --env-file .env --port 8000
```

Alternatively export the settings into the process environment and omit
`--env-file .env`. The API does not implicitly search for dotenv files.
Use one process: sessions and checkpoints live only in that process's RAM and
are lost on restart. This is a local development service, with no deployment setup.
Interactive documentation is at `http://localhost:8000/docs`; OpenAPI is at
`http://localhost:8000/openapi.json`.

`CORS_ALLOWED_ORIGINS` accepts comma-separated origins and defaults to
`http://localhost:5173`. Explicit `create_app(allowed_origins=[...])` overrides it;
an empty list disables allowed origins. CORS permits GET, POST, DELETE and
Content-Type, with credentials disabled.

Offline tests use `create_app(coordinator=SessionCoordinator(client=fake_client))`
inside a TestClient context. They neither load live model settings nor require
credentials. Verification: 498 tests passed, including 76 API tests. One installed
Starlette TestClient warning reports the deprecated `anyio.abc.BlockingPortal`
alias; no dependency versions were changed or warnings suppressed.

## Groq live smoke test

Install dependencies with `.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt`.
Set `GROQ_API_KEY` and a tool-capable `GROQ_MODEL` in the ignored root `.env`.
From the repository root run:

```powershell
.\.venv\Scripts\python.exe scripts/test_groq_api.py
```

The script uses application settings and the shared factory, then makes one
structured OrderSelection request. It prints success only after schema validation.
Process environment values override `.env`. Offline pytest never invokes this script.

Groq migration verification: 499 offline tests passed with the existing Starlette
warning. Installed langchain-groq 1.1.3 and groq 0.37.1; `pip check` passed.
Dependencies remain unpinned. No live Groq request was made during verification.


## ALL-LLM batch workflow verification

The ALL-LLM batch workflow passes 507 offline backend tests and 53 frontend tests.
The Vite production build and `pip check` pass. The backend still reports the
existing Starlette BlockingPortal deprecation warning. Groq calls remain mocked.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
npm.cmd --prefix frontend test -- --run
npm.cmd --prefix frontend run build
git diff --check
```

All four roles share the existing ChatGroq factory and settings. No additional
dependencies or environment variables are required for this migration.
The optional live smoke script checks OrderSelection connectivity; it does not
certify the quality of Fleet, Route, or Safety decisions.
The in-memory session/checkpoint store is process-local; restart the backend after
code changes. Sessions do not persist across process restarts.


Schema-guidance and parking verification: **554 backend tests and 58 frontend tests**
pass offline. Frontend build, pip check and git diff --check pass. No new dependency
or live Groq request was needed. The existing Starlette deprecation warning remains.


Hybrid Fleet verification: **570 backend tests and 58 frontend tests** pass.
Frontend build, pip check and git diff --check pass. All provider calls were
mocked, including real ChatGroq structured-call boundary tests. No dependencies
or environment variables changed. The existing Starlette warning remains.


Historical hybrid Route/Safety verification: **594 backend tests and 58 frontend tests** pass.
The frontend build, pip check and git diff --check pass. The former routing-intent call
and checkpointed feedback replacements are tested offline. No new packages,
environment variables or live Groq calls were needed. The existing Starlette
BlockingPortal deprecation warning remains.


## Deterministic Route verification

The current architecture is Order LLM, hybrid Fleet, deterministic A* Route, and
hybrid Safety. **590 backend tests and 58 frontend tests pass**; frontend build,
pip check and git diff --check pass. The three-order API regression mocks the real
ChatGroq completion boundary for Order/Fleet/Safety only and verifies Plan, checkpoint
readback, shared drop-off chaining, final parking and Execute. Route makes no model
calls. Pytest does not read credentials or contact Groq. The existing Starlette
BlockingPortal warning remains.

Restart the backend after changing graph state schemas. Checkpoints and sessions
are process-local, so restart starts fresh sessions; no persistent migration is needed.
Expected unreachable paths and Safety rejection return typed command outcomes.
Unexpected errors still return a generic 500. Inspect backend error logs for the
original exception/traceback and session command, pending nodes, outcome and failed
activity details. Do not publish server diagnostics as API responses.

One approved live diagnostic before this conversion successfully planned a default
warehouse with a synthetic order and final parking. A follow-up live diagnostic was
blocked by automatic approval review's usage limit. The original intermittent live
500 was not reproduced against Groq, and this conversion is not proof that Route
caused that error. No post-conversion live-provider verification was performed.
