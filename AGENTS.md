# AGENTS.md

## Project Overview

This repository is for a university course project: an autonomous multi-agent warehouse simulation built around a LangGraph orchestration layer.

The project is intentionally structured in phases. The application is not to be implemented in full at this stage. This file defines the target architecture, operating rules, and the required development sequence.

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

## Required Warehouse Model

The warehouse will initially be a simple 10x10 logical grid with:
- 3 robots
- shelves / obstacles
- packages
- blocked cells
- delivery / drop-off locations
- A* routing
- collision and route validation

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

The repository should evolve in this order:

```text
Advanced-LLMs-Project/
├── README.md
├── AGENTS.md
├── .gitignore
├── requirements.txt
├── pyproject.toml
├── pytest.ini
├── src/
│   └── warehouse/
│       ├── __init__.py
│       ├── models.py
│       ├── simulation.py
│       ├── routing.py
│       ├── validation.py
│       └── tools.py
├── tests/
│   ├── test_simulation.py
│   ├── test_routing.py
│   └── test_validation.py
├── app/
│   ├── graph/
│   │   ├── state.py
│   │   ├── agents.py
│   │   ├── tools.py
│   │   └── graph.py
│   └── api/
│       └── main.py
├── frontend/
│   └── package.json
└── docs/
    └── architecture.md
```

This structure is a recommendation for the final project shape. The current implementation phase does not require creating these directories yet.

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

## Execution Constraints for This Repository

- The current phase is documentation and planning only.
- No application code should be added before the environment and architecture are documented and understood.
- Future code should follow the above sequence and keep each phase minimal and verifiable.
- Any implementation must preserve the agent-role boundaries and shared-state design described above.

## Summary

This project is designed to demonstrate a realistic agentic architecture in a warehouse domain: deterministic simulation logic, route planning, safety validation, and orchestration using LangGraph. The project should remain intentionally incremental and academically clear, with each phase validated before moving to the next.
