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
The subsequent routing phase added deterministic A* pathfinding. There is no
React initialization, FastAPI service, or LangGraph agent.

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
Google Gemini setup, `langchain-google-genai` is an optional prerequisite to install in the
project virtual environment. Copy `.env.example` to ignored `.env`, replace its
placeholders, and load it explicitly with `load_settings(env_file=".env")`.
Create one client during application setup and pass it to future consumers.
See [configuration and ownership](architecture.md#shared-llm-configuration).
