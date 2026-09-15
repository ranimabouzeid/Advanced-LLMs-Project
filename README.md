# Advanced-LLMs-Project
SWARMDOCK is a university project for a multi-agent autonomous warehouse simulation
using LangGraph.

The current implementation contains Pydantic domain models and a deterministic
10x10 warehouse simulation with three robots, orders, shelves, temporary blocked
cells, validated single-step movement, reusable deterministic A* route planning,
assignment/pickup/delivery primitives, and typed two-leg delivery validation.

## Run tests (Windows)

```powershell
.\.venv\Scripts\python.exe -m pytest
```

See [environment setup](docs/environment.md), [warehouse conventions and usage](docs/warehouse.md),
and [architecture and phase boundaries](AGENTS.md).

See the [revised completion plan](plan.md) for the remaining delivery lifecycle,
LangGraph, API, frontend, and verification milestones.

The test suite includes 148 cases for models, simulation, routing, and validation.
Atomic full-delivery execution is deferred. LangGraph agents, FastAPI, and React
are future phases.
