# Phase 1 command log

Commands below were run from the repository root in PowerShell, in order.
Session polling used the execution tool and did not run additional shell commands.

## Inspection (before installation)

```powershell
Get-Location
py -0p
python --version
Test-Path .venv
Get-ChildItem -Force
if (Test-Path .gitignore) { Get-Content .gitignore }
rg --files -g AGENTS.md -g '*requirements*' -g pyproject.toml -g 'pyvenv.cfg'
git status --short
Get-Content AGENTS.md
Get-Content README.md
Get-Command python,python3,python3.11,py -ErrorAction SilentlyContinue | Select-Object Name,Source
Test-Path "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
Test-Path 'C:\Python311\python.exe'
Get-ChildItem -Path . -Filter pyvenv.cfg -Recurse -Force | Select-Object FullName
```

Python was 3.13.7. The launcher reported no installed Pythons, and the
Python 3.11 executable checks found none. There was no existing `.venv`,
other repository `pyvenv.cfg`, or `.gitignore`. Git status was clean.

## Environment creation and installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

The first pip invocation failed because sandbox network access was blocked
(WinError 10013). The identical second invocation ran with elevated network
permission. No activation command was run; the environment executable was
used directly.

## Verification

```powershell
.\.venv\Scripts\python.exe -c "import sys; import langgraph, langchain, pydantic, fastapi, dotenv, networkx, pytest; from importlib.metadata import version; assert sys.prefix != sys.base_prefix; print(sys.executable); print(sys.version); print('\n'.join(name + '==' + version(name) for name in ['langgraph', 'langchain', 'pydantic', 'fastapi', 'python-dotenv', 'networkx', 'pytest']))"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest --version
git check-ignore -v .venv/pyvenv.cfg
git diff --check
git status --short --untracked-files=all
```

## File changes

Created using the patch tool (which also created the parent directories):

- `.gitignore`: local environment, secret files, and Python/test caches.
- `backend/requirements.txt`: seven requested direct dependencies.
- `frontend/.gitkeep`: retains the empty frontend directory in Git.
- `docs/environment.md`: Windows and VS Code setup instructions.
- `docs/phase-1-command-log.md`: this command and file-change report.

`python -m venv` and pip generated the local `.venv/` tree, including
`pyvenv.cfg`, activation scripts, executables, and installed dependencies.
That generated tree is ignored by Git. Existing files were not changed.

## Follow-up: switch to Python 3.11

The initial setup above used 3.13.7. The following commands installed Python
3.11.9 and recreated the environment. Installation and pip ran with permission
outside the sandbox. The legacy launcher did not detect Python inside the
sandbox, so environment creation used the verified executable path.

```powershell
py -0p
Get-Command python,winget -ErrorAction SilentlyContinue | Select-Object Name,Source
Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory
Get-Content .gitignore
Get-Content docs/environment.md
git status --short
winget install --id Python.Python.3.11 --exact --source winget --scope user --silent --accept-source-agreements --accept-package-agreements
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" --version
py -3.11 --version
$projectVenv = (Resolve-Path -LiteralPath .venv).Path
if ($projectVenv -ne 'C:\Users\User\OneDrive\Bureau\COE549\Advanced_LLMs_Project\Advanced-LLMs-Project\.venv') { throw 'Unexpected virtual environment path' }
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv --clear $projectVenv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Changed files: `docs/environment.md`, this log, and the ignored `.venv/` tree.
The Windows installer also created the per-user Python311 installation outside
the repository. No application code was added.

Verification commands:

```powershell
.\.venv\Scripts\python.exe -c "import sys; import langgraph, langchain, pydantic, fastapi, dotenv, networkx, pytest; assert sys.version_info[:2] == (3, 11); assert sys.prefix != sys.base_prefix; print(sys.executable); print(sys.version); print('All seven dependency imports passed')"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest --rootdir=. -p no:cacheprovider
Write-Output "Pytest exit code: $LASTEXITCODE"
git check-ignore -v .venv/pyvenv.cfg
git diff --check
git status --short
```

Results: Python 3.11.9, isolated environment, all seven imports passed, no
broken requirements. Pytest collected zero tests (expected exit code 5).
It still discovers the parent `COE549/pyproject.toml`; a local test configuration
remains future setup work. `.venv` remains ignored. Git reported only the two
documentation changes above. The diff check reported line-ending conversion
warnings, with no whitespace errors.
