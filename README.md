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
Execute revalidates and applies approved deliveries. All four agents use one shared
Groq client: Order selects orders, Fleet selects robots, Route supplies coordinates,
and Safety approves or rejects. A* remains a standalone domain utility; agents do
not use it. Deterministic simulation still guards actual execution.

## Run tests (Windows)

```powershell
.\.venv\Scripts\python.exe -m pytest
```

See [environment setup](docs/environment.md), [warehouse conventions and usage](docs/warehouse.md),
and [architecture and phase boundaries](AGENTS.md).

See the [revised completion plan](plan.md) for the milestone sequence and future work.

The backend includes offline domain, role, batch, session, and API tests.
See [API startup](docs/environment.md#milestone-f-api) and
[API contract](docs/architecture.md#milestone-f-thin-fastapi-service).
See [batch planning and execution](docs/architecture.md#sequential-multi-order-batch-planning-current-workflow)
for state ownership, partial failure policy, and the route selector.

Current ALL-LLM verification: **507 backend tests and 53 frontend tests pass**;
frontend build, pip check, and git diff --check pass. Earlier counts above are
historical. Model decisions are mocked in tests; no live call was made. LLM routes
can be suboptimal or invalid and Safety can make mistakes; deterministic execution
may reject a model-approved plan. See the current workflow architecture for retry,
review, projection, and partial-commit behavior.
