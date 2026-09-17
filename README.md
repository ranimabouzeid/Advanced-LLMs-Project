# Advanced-LLMs-Project
SWARMDOCK is a university project for a multi-agent autonomous warehouse simulation
using LangGraph.

The current implementation contains Pydantic domain models and a deterministic
10x10 warehouse simulation with three robots, orders, shelves, temporary blocked
cells, validated single-step movement, reusable deterministic A* route planning,
assignment/pickup/delivery primitives, typed two-leg delivery validation, and atomic
complete-delivery execution. Four LangGraph roles, PLAN/EXECUTE orchestration,
process-local checkpointed sessions, and a thin FastAPI service are implemented.

## Run tests (Windows)

```powershell
.\.venv\Scripts\python.exe -m pytest
```

See [environment setup](docs/environment.md), [warehouse conventions and usage](docs/warehouse.md),
and [architecture and phase boundaries](AGENTS.md).

See the [revised completion plan](plan.md) for the milestone sequence and future work.

Milestones A–F are complete with 498 passing offline tests, including 76 API cases.
See [API startup](docs/environment.md#milestone-f-api) and
[API contract](docs/architecture.md#milestone-f-thin-fastapi-service).
React and Milestone G remain unimplemented and require separate authorization.
