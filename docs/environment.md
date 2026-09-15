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

## Scope

- `backend/` contains dependency configuration only.
- `frontend/` is reserved for a later phase.
- `docs/` contains setup documentation.
- `.venv/` is local and ignored by Git.

No React initialization, FastAPI service, LangGraph agents, or warehouse logic
is included in Phase 1.
