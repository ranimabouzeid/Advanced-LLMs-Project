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

The current architecture preserves fresh projected Fleet decisions, direct same-batch
chaining, unique final parking, and checkpoint isolation. Identical routing inputs
are not retried after Safety rejection; changed warehouse inputs permit bounded
replacement planning followed by explicit Execute. Expected unreachable paths return
typed outcomes, while caught node failures return controlled workflow rejections. Uncaught
exceptions retain server-side diagnostics, generic API errors and checkpoint rollback. See the current workflow architecture for details and limitations.

Verification: **608 offline backend tests and 58 frontend tests pass**. The frontend
build, pip check and git diff --check pass. The existing Starlette deprecation warning
remains. Restart the backend after this schema change; sessions are process-local.
