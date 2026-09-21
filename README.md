# Advanced-LLMs-Project
SWARMDOCK is a university project for a multi-agent autonomous warehouse simulation
using LangGraph.

The current implementation contains Pydantic domain models and a deterministic
10x10 warehouse simulation with three robots, orders, shelves, temporary blocked
cells, validated single-step movement, reusable deterministic A* route planning,
assignment/pickup/delivery primitives, typed two-leg delivery validation, and atomic
complete-delivery execution. Four LangGraph roles, PLAN/EXECUTE orchestration,
process-local checkpointed sessions, a thin FastAPI service, and a React dashboard are implemented.
Plan builds a sequential batch using projected robot positions and batteries;
Execute revalidates deliveries and final parking. Drop-offs are temporary service
cells: packages stay delivered while robots chain to their next pickup or park.
Order, Fleet and Safety use one shared Groq client. Order selects orders; hybrid
Fleet selects the minimum complete projected A* cost deterministically, breaking
ties by robot ID. Groq supplies structured commentary and cannot override or veto
the assignment; invalid/unavailable commentary retains the trusted cost summary. Route remains a
LangGraph node and generates exact shortest paths using deterministic A*, with no
Groq call. Hybrid Safety interprets trusted findings and enforces every hard failure.
Deterministic simulation still guards actual execution.

## Quickstart: Setup and Execution

### 1. Prerequisites
- **Python**: Python 3.11+ (recommended) installed on Windows.
- **Node.js**: Node.js v18+ and npm installed.
- **Groq API Key**: For live LLM planning commentary and order selection (obtainable at [console.groq.com](https://console.groq.com/)).

---

### 2. Backend Setup & Virtual Environment

From the repository root:

1. **Create the Python virtual environment** (if not already created):
   ```powershell
   python -m venv .venv
   ```

2. **Activate the virtual environment**:
   - **PowerShell**:
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
     *(If script execution is disabled, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that PowerShell window first).*
   - **Command Prompt (CMD)**:
     ```cmd
     .\.venv\Scripts\activate.bat
     ```

3. **Install backend dependencies**:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
   ```

4. **Configure environment variables**:
   Copy `.env.example` to `.env` and fill in your Groq API credentials:
   ```powershell
   Copy-Item .env.example .env
   ```
   Edit `.env`:
   ```env
   GROQ_MODEL=llama-3.3-70b-versatile
   GROQ_API_KEY=your-groq-api-key-here
   LLM_TIMEOUT_SECONDS=30
   LLM_MAX_RETRIES=2
   LLM_MAX_TOKENS=512
   CORS_ALLOWED_ORIGINS=http://localhost:5173
   ```

---

### 3. Frontend Setup

From the repository root, install Node dependencies for the dashboard:

```powershell
cd frontend
npm install
cd ..
```

---

### 4. Running the Application

#### A. Start the Backend (FastAPI)
From the repository root (with `.venv` activated or calling Python directly):
```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --app-dir backend --env-file .env --port 8000 --reload
```
- **Backend API Docs (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **OpenAPI Schema**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

#### B. Start the Frontend (React + Vite)
In a second terminal window:
```powershell
cd frontend
npm run dev
```
- **Dashboard Web UI**: [http://localhost:5173](http://localhost:5173)

---

## Run tests (Windows)

### Backend Tests (pytest)
```powershell
.\.venv\Scripts\python.exe -m pytest
```

### Frontend Tests (Vitest)
```powershell
npm.cmd --prefix frontend test -- --run
```

### Live Groq Smoke Test (Optional)
```powershell
.\.venv\Scripts\python.exe scripts/test_groq_api.py
```

See [environment setup](docs/environment.md), [warehouse conventions and usage](docs/warehouse.md),
and [architecture and phase boundaries](AGENTS.md).

See the [revised completion plan](plan.md) for the milestone sequence and future work.

The backend includes offline domain, role, batch, session, and API tests.
See [API startup](docs/environment.md#milestone-f-api) and
[API contract](docs/architecture.md#milestone-f-thin-fastapi-service).
See [batch planning and execution](docs/architecture.md#sequential-multi-order-batch-planning-current-workflow)
for state ownership, partial failure policy, and the route selector.

The current architecture preserves fresh projected Fleet decisions, direct same-batch
chaining, unique final parking, and checkpoint isolation. Identical routing inputs
are not retried after Safety rejection; changed warehouse inputs permit bounded
replacement planning followed by explicit Execute. Expected unreachable paths return
typed outcomes, while caught node failures return controlled workflow rejections. Uncaught
exceptions retain server-side diagnostics, generic API errors and checkpoint rollback. See the current workflow architecture for details and limitations.

Verification: **608 offline backend tests and 58 frontend tests pass**. The frontend
build, pip check and git diff --check pass. The existing Starlette deprecation warning
remains. Restart the backend after this schema change; sessions are process-local.
