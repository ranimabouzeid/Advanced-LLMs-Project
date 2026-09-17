# AGENTS.md

## Project Overview

This repository is for a university course project: an autonomous multi-agent warehouse simulation built around a LangGraph orchestration layer.

The project is intentionally structured in phases. The application is not to be implemented in full at this stage. This file defines the target architecture, operating rules, and the required development sequence.

### Current Status and Authorization Boundary

- Latest verified status: Milestone F is complete with 498 passing offline tests, including 76 FastAPI TestClient cases. `backend/app/api/` is a thin synchronous adapter using SessionCoordinator exclusively. Missing/consumed proposals return typed expected command rejection with the previous committed state. Lifespan creates one client/coordinator, with offline injection supported. The suite reports one dependency deprecation warning from Starlette's AnyIO BlockingPortal alias. React, WebSockets, streaming, database persistence, authentication, deployment, and Milestone G remain unimplemented and require explicit authorization. The Milestone E entry below records its earlier boundary.

- Latest verified status: Milestone E is complete with 422 passing offline tests, including 39 session cases. `backend/app/sessions.py` provides one in-memory saver, the existing graph with optional checkpointing, UUID/thread mapping, private committed-checkpoint references, nonblocking per-session guards, validated mutations, and reset retirement. Intermediate failed checkpoints never replace committed state. No FastAPI, React, disk/database persistence, deployment, or Milestone F work is authorized. Earlier milestone entries below are historical snapshots.

- Phase 1 (repository/environment setup) is complete. The local Windows virtual environment `.venv` uses Python 3.11.9.
- All seven requested dependencies are installed and import successfully; `pip check` reports no broken requirements.
- The user-authorized Phase 2 combines warehouse domain models and basic deterministic simulation, originally roadmap steps 2 and 3. Both are implemented under `backend/app/warehouse/` with 59 passing tests.
- The simulation supports initialization, inspection, pending orders, blocked-cell changes, and validated adjacent movement. See `docs/warehouse.md` for conventions.
- Deterministic A* is implemented in `backend/app/warehouse/routing.py`, with 26 routing cases and 85 total passing tests. It returns a shortest endpoint-inclusive route or `None`; invalid dimensions/out-of-bounds inputs raise `ValueError`.
- The latest authorized portion of Milestone A adds assignment/pickup/delivery primitives, two-leg A* plans, warehouse revision tracking, and typed route/delivery validation. The updated full suite has 148 passing cases.
- Atomic complete-delivery execution is now implemented via `WarehouseSimulation.execute_delivery(plan)`, with a typed result and one final snapshot publication. Milestone A is complete with 169 passing tests. Do not begin Milestone B or later milestones without an explicit request.
- All four standalone roles are implemented. Order uses structured output and domain eligibility checks; Fleet uses deterministic A* eligibility/cost and stable selection; Route delegates two-leg planning; Safety publishes existing deterministic validation findings, with optional non-authoritative summaries. The full suite has 347 passing offline tests, including 21 Order, 36 Fleet, 17 Route, and 23 Safety cases. No final workflow wiring, conditional edges, MemorySaver integration, API, or React application exists. Do not begin later milestones without explicit authorization.
- Milestone B shared state, deterministic partial-update/invalidation helpers, and shared Gemini configuration are implemented. Its offline audit passes with 250 total tests, including compiled test-graph updates and checkpoint-serializer round trips. No production workflow or MemorySaver integration exists. The optional Gemini integration package and live calls remain untested; no credentials are needed for tests. Do not begin Milestone C without explicit authorization.
- Future architecture and file examples below are design requirements, not authorization to create the whole application.
- Update this status only when a phase has actually been completed and verified.

Current project files, excluding Git internals and the ignored `.venv`:

```text
AGENTS.md
README.md
.gitignore
backend/requirements.txt
backend/app/__init__.py
backend/app/warehouse/__init__.py
backend/app/warehouse/models.py
backend/app/warehouse/simulation.py
backend/app/warehouse/routing.py
backend/app/warehouse/validation.py
backend/app/graph/__init__.py
backend/app/graph/state.py
backend/app/graph/updates.py
backend/app/graph/agents.py
backend/app/graph/tools.py
backend/app/graph/graph.py
backend/app/sessions.py
backend/app/api/__init__.py
backend/app/api/main.py
backend/app/api/schemas.py
backend/app/api/sessions.py
backend/app/config.py
.env.example
backend/tests/test_models.py
backend/tests/test_simulation.py
backend/tests/test_routing.py
backend/tests/test_validation.py
backend/tests/test_execution.py
backend/tests/test_graph_state.py
backend/tests/test_graph_updates.py
backend/tests/test_graph_serialization.py
backend/tests/test_config.py
backend/tests/test_order_agent.py
backend/tests/test_fleet_agent.py
backend/tests/test_route_agent.py
backend/tests/test_safety_agent.py
backend/tests/test_graph_workflow.py
backend/tests/test_sessions.py
backend/tests/test_api.py
pytest.ini
frontend/.gitkeep
docs/environment.md
docs/phase-1-command-log.md
docs/warehouse.md
docs/architecture.md
plan.md
docs/documenation.md
```

## Final System Architecture

React frontend
  ↓
FastAPI backend
  ↓
LangGraph multi-agent system
  ↓
Deterministic Python tools and warehouse simulation

## Core Architectural Requirements

### 1. Frontend
- React-based UI for interaction with the warehouse simulation.
- Not to be implemented yet.
- Will eventually visualize orders, robot status, blocked cells, routes, and validation results.

### 2. Backend
- FastAPI service layer.
- Will expose endpoints to trigger runs, query state, and report results.
- Not to be implemented yet.

### 3. LangGraph Multi-Agent Layer
- A LangGraph workflow orchestrates multiple specialized agents.
- Agents communicate through shared state rather than direct agent-to-agent message passing.
- The workflow is state-driven, with meaningful partial updates written to a shared state object.

### 4. Deterministic Simulation Layer
- Python simulation logic controls warehouse state.
- Includes robots, orders, blocked cells, routes, and validation rules.
- All routing and decision support tools must be deterministic and testable.

### Layer Boundaries

- Domain models, simulation, routing, and validation must work without an LLM, graph, or HTTP server.
- Graph tools wrap deterministic domain functions; API handlers later invoke orchestration.
- Domain code must not import the API or graph to perform warehouse operations.
- React later consumes the API; keep simulation rules out of UI components.
- Installing FastAPI and LangGraph during setup does not authorize their implementation.

## Required Agent Roles

### Order Agent
- Reads pending orders.
- Prioritizes or selects which order to process next.
- Will later use Pydantic Structured Output Mode.
- Must contribute to the shared workflow state.

### Fleet Agent
- Reads the selected order and the robot information.
- Chooses the most appropriate available robot.
- Can use tools for distance and robot status checks.
- Must update shared state with the chosen robot.

### Route Agent
- Reads the selected robot, destination, and blocked cells.
- Calls a deterministic A* routing tool.
- Writes the calculated path into shared state.

### Safety Agent
- Checks the proposed route for blocked cells and robot conflicts.
- Writes route_valid and collision_risk into shared state.
- Must be the component that triggers the conditional routing logic.

### Agent Contracts and Tool Ownership

Implement all four roles even though the course minimum is three. Each role needs a distinct toolset and explicit state ownership. The following field names are proposed contracts to finalize in Phase 6.

| Agent | State inputs | Partial updates it owns | Tools |
| --- | --- | --- | --- |
| Order | Pending orders and metadata | Selected order identifier and selection reason | Pending-order and order-metadata lookup |
| Fleet | Selected order, robot positions and availability | Selected robot identifier and assignment reason | Robot-status and deterministic distance/reachability checks |
| Route | Selected robot, target, obstacles, blocked cells, safety feedback | Proposed route, planning status, replan attempt count | Deterministic A* |
| Safety | Proposed route, warehouse snapshot, other robot positions/routes | `route_valid`, `collision_risk`, rejection reasons and conflict details | Route validation and robot-conflict detection |

- Order must later use a dedicated Pydantic structured output model and validate that a selected identifier refers to an eligible pending order. It must not assign robots or move them.
- Fleet must use documented selection and tie-break rules. It must not invent distances, execute movement, or mark orders complete.
- Route must distinguish an unreachable target from a valid start-equals-goal route. It must not certify its own route as safe.
- Safety must derive its results from runtime checks rather than assume that an A* result is safe.
- No pending order, no available robot, and no reachable route must be separate, explicit outcomes.

### Intended Conditional Workflow

```text
Order → Fleet → Route → Safety
                         ├── valid and no collision risk → deterministic execution
                         └── invalid or collision risk → Route with safety feedback
```

- The LangGraph conditional edge reads the Safety Agent's state updates.
- Execution is a deterministic operation, not another LLM agent. Never execute a rejected route.
- Revalidate against the relevant current simulation state before committing movement.
- Replanning must consume actionable feedback; repeatedly requesting the same route with unchanged inputs is not recovery.
- Bound replanning attempts and terminate with an explicit failure when exhausted. Handle missing work/resources before attempting route execution.
- Do not add advanced scheduling or time-expanded routing without first documenting and authorizing that additional scope.

## Required LangGraph Features

The final implementation must include:
- at least 3 distinct agents
- different tool sets for different agents
- Pydantic BaseModel state
- multiple state data types
- optional state fields
- MemorySaver checkpointing
- meaningful partial state updates
- at least one agent using Pydantic Structured Output Mode
- at least one conditional edge

### Shared State Rules

- Agents communicate through shared LangGraph state, not direct agent-to-agent messages, hidden module globals, or undocumented side effects.
- Use a Pydantic `BaseModel` with identifiers/strings, integer counters, booleans, collections, and nested domain models as appropriate.
- Include optional fields for values not yet known, such as selected order, selected robot, route, and safety results.
- Keep an unknown safety result distinct from a checked invalid route.
- Return meaningful partial updates and preserve unrelated state fields.
- Clear stale routes and safety results when their inputs change.
- Validate structured model output before making it actionable.
- Define the warehouse snapshot/reference strategy explicitly; avoid two independent sources of truth for robot positions or order status.
- Add `MemorySaver` only in Phase 9, with explicit thread identifiers and checks for thread isolation. Verify the installed LangGraph API then.
- In-memory checkpointing is sufficient initially; it does not imply persistence across process restarts or require a database.

## Required Warehouse Model

The warehouse will initially be a simple 10x10 logical grid with:
- 3 robots
- shelves / obstacles
- packages
- blocked cells
- delivery / drop-off locations
- A* routing
- collision and route validation

### Conventions to Define Before Implementing Behavior

- Coordinate order, bounds, and indexing; prefer zero-based coordinates for the initial grid.
- Movement directions and costs; prefer four orthogonal neighbors with unit cost.
- Permanent shelf obstacles versus temporary blocked cells.
- Robot availability and order/package lifecycle states.
- Whether pickup and delivery are separate route legs and how targets are chosen.
- Route representation, including whether it contains the start and destination.
- Sequential execution versus discrete simulation ticks.
- Collision rules appropriate to execution: occupied cells and, if concurrent motion is supported, same-cell conflicts and robots swapping cells in one tick.

These are design decisions to document in the relevant phase, not tasks to implement now.

### Determinism and Safety

- A* must perform routing. Document the heuristic, neighbor ordering, and tie-breaking behavior.
- Never substitute LLM reasoning or another algorithm for required A* routing.
- `networkx` is available but optional; any use must preserve the documented A* behavior.
- Validate bounds, allowed moves, obstacles, blocked cells, endpoints, and robot conflicts.
- Failed movement must not partially update positions or complete an order.
- Use fixed fixtures and stable ordering. If randomness becomes necessary, use an explicit seed.
- LLM selections may vary, but domain rules, routing, and safety checks must be deterministic for fixed inputs.

## Project Rules

1. Do not implement the full application before the planned phases are complete.
2. Keep the simulation deterministic wherever possible.
3. Do not replace deterministic algorithms with LLM reasoning.
4. A* must perform routing.
5. Agents communicate through shared LangGraph state.
6. Favor explicit shared state over implicit messaging between agents.
7. Use Pydantic models for structured data and state validation.
8. Keep testability in mind from the earliest phases.
9. Write tests for deterministic components.
10. Do not add unnecessary infrastructure.
11. Keep the project suitable for a university course project.
12. Do not begin React or FastAPI work until the LangGraph workflow and core simulation logic are ready.
13. Do not proceed to later project phases unless explicitly requested.
14. Every agent should have a clear responsibility and a distinct set of tools.
15. Conditional routing must reflect runtime safety checks rather than hard-coded assumptions.
16. Keep the warehouse logic simple and transparent enough for academic demonstration and debugging.

## Recommended Initial Repository Structure

Keep production Python code under `backend/app/`, as explicitly requested for Phase 2, and tests under `backend/tests/`. Avoid competing warehouse packages at the repository root or directly under backend. Create only the files needed by an explicitly requested phase.

```text
Advanced-LLMs-Project/
├── README.md
├── AGENTS.md
├── .gitignore
├── pytest.ini
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── warehouse/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── simulation.py
│   │   │   ├── routing.py
│   │   │   └── validation.py
│   │   ├── graph/
│   │   │   ├── __init__.py
│   │   │   ├── state.py
│   │   │   ├── agents.py
│   │   │   ├── tools.py
│   │   │   └── graph.py
│   │   └── api/
│   │       └── main.py
│   └── tests/
│       ├── test_models.py
│       ├── test_simulation.py
│       ├── test_routing.py
│       └── test_validation.py
├── frontend/
│   └── package.json
└── docs/
    ├── environment.md
    ├── phase-1-command-log.md
    ├── warehouse.md
    └── architecture.md
```

This structure includes future components. Only the files listed in Current Status exist; do not scaffold future components yet.

`app/warehouse/` owns deterministic domain code. Future `app/graph/tools.py` will contain role-specific wrappers for those functions. `app/graph/agents.py` can initially contain four separate node functions; split files only when useful. `app/api/main.py` and `frontend/package.json` belong to Phases 10 and 11. Imports use `app.warehouse` with `backend` on the Python path, configured for tests in `pytest.ini`. Add packaging metadata only if needed.

## Development Order

1. Repository / environment setup
2. Warehouse data model
3. Deterministic warehouse simulation
4. A* pathfinding
5. Tests
6. Pydantic LangGraph state
7. LangGraph agents
8. Conditional routing
9. MemorySaver
10. FastAPI
11. React frontend

### Phase Completion Criteria

| Phase | Evidence before calling it complete |
| --- | --- |
| 1. Environment | Python 3.11 environment and imports verified, `pip check` succeeds, `.venv` ignored |
| 2. Data model | Minimal Pydantic models, documented conventions, valid examples and invalid-input checks |
| 3. Simulation | Repeatable initialization/transitions; invalid actions leave state consistent |
| 4. A* | Known routes, blocked/unreachable targets, boundaries, and start-equals-goal behavior verified |
| 5. Tests | Offline deterministic test suite passes using repository-local pytest configuration |
| 6. State | Pydantic validation, optional fields, and partial-update behavior verified |
| 7. Agents | Four role contracts, distinct tools, structured Order output, and basic graph wiring checked with mocked model responses |
| 8. Conditional routing | Safe/unsafe branches and bounded replanning failure verified |
| 9. MemorySaver | Checkpoint retrieval and thread isolation verified |
| 10. FastAPI | Thin service layer and endpoint behavior tested against established orchestration |
| 11. React | Core display and interaction flows verified against the established API |

Write focused tests alongside deterministic components in Phases 2–4. Phase 5 consolidates coverage rather than postponing all verification. Passing a phase does not authorize starting the next one.

## Local Development and Verification

- Follow `docs/environment.md` for Windows and VS Code instructions.
- Use `.venv\Scripts\python.exe` explicitly when the terminal is not activated; plain `python` may use a different global installation.
- Keep `.venv`, caches, and `.env` secrets ignored. Never commit API keys.
- `backend/requirements.txt` contains `langgraph`, `langchain`, `pydantic`, `fastapi[standard]`, `python-dotenv`, `networkx`, and `pytest`.
- Versions are currently unpinned. Do not describe the environment as locked or fully reproducible; add constraints only with a concrete reason.
- Deterministic tests must not require live LLM calls or credentials. Mock model responses when testing agent contracts.

Basic checks from the repository root:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
git status --short
```

The repository-local `pytest.ini` selects `backend/tests` and adds `backend` to the Python path. It replaces accidental discovery of the parent course project's configuration. The current suite has 498 passing offline cases. Do not edit unrelated parent configuration.

For documentation-only changes, review the diff and run `git diff --check`; no new tests are needed. For code changes, run checks appropriate to the phase and report exact outcomes, including environmental blockers.

## Execution Constraints for This Repository

- Environment setup, domain models, basic simulation, and deterministic A* are complete with focused tests. Do not implement later phases without an explicit request.
- No application code should be added before the environment and architecture are documented and understood.
- Future code should follow the above sequence and keep each phase minimal and verifiable.
- Any implementation must preserve the agent-role boundaries and shared-state design described above.
- Inspect existing files and Git status first; preserve unrelated user changes.
- Prefer small, readable functions and explicit Pydantic models over unnecessary abstractions.
- Do not add databases, queues, containers, deployment pipelines, or additional services without a concrete requirement and explicit scope.
- Explain every changed file, why it changed, verification results, and remaining limits.
- Update environment and architecture documentation when their decisions change.
- Commit and push when requested, keeping generated environments and secrets out of Git.
- Stop at the authorized phase boundary and describe the next phase without beginning it.

## Summary

This project is designed to demonstrate a realistic agentic architecture in a warehouse domain: deterministic simulation logic, route planning, safety validation, and orchestration using LangGraph. The project should remain intentionally incremental and academically clear, with each phase validated before moving to the next.
