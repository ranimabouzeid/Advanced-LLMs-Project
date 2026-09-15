# SWARMDOCK warehouse: domain model and deterministic simulation

This phase implements data and sequential actions only. There is no pathfinding,
order execution, charging process, LangGraph, LLM agent, HTTP service, or UI.

## Layout and conventions

- The grid is exactly 10x10. A position is `(x, y)`, with each coordinate from 0
  through 9 inside a warehouse. `Position` itself represents any integer pair;
  warehouse validation rejects out-of-bounds cells.
- Three robots start at `(0, 0)`, `(0, 1)`, and `(0, 2)`, with IDs `robot-1`
  through `robot-3`, battery 100, and status `idle`.
- Shelf obstacles occupy `(3, y)` and `(6, y)` for `y = 2..7` (12 cells).
- Registered drop-off cells are `(9, 0)` and `(9, 9)`.
- Orders and temporary blocked cells start empty. Initialization uses no random
  data, generated IDs, timestamps, network calls, or external state.
- Shelves are permanent impassable cells. Temporary blocks are separate and
  cannot overlap shelves or current robot positions.
- Package pickup locations are walkable access cells, not cells inside shelves.
  Each order embeds one package and names a registered drop-off destination.
- Orders and packages have unique nonblank identifiers; robot IDs are also unique.
  Callers supply identifiers. Multiple packages may share a pickup location.
- A pickup or drop-off may be temporarily blocked, including when an order is
  created. This represents work that cannot currently be reached; order creation
  does not test reachability or move a package. Pickup and drop-off may coincide.

## Models and state ownership

`backend/app/warehouse/models.py` defines Pydantic `Position`, `Robot`, `Package`,
`Order`, and `WarehouseState` models, plus status enums. Unknown fields are rejected.
Coordinates and battery require actual integers (not booleans or numeric strings).
Battery is an integer percentage from 0 through 100.

Snapshots and nested models are frozen; collections are tuples and frozensets.
Use simulation methods to change state. `state` exposes an immutable snapshot,
and `robot_positions()` returns a detached dictionary. Old snapshots remain valid
after later actions. JSON serialization is available via `state.model_dump_json()`;
cell collections serialize as lists sorted by `(x, y)` for stable output.

The simulation owns a single current `WarehouseState`. It validates complete
candidate snapshots before replacing state, so a rejected action changes nothing.
Optional custom initialization accepts a valid `WarehouseState`, still requiring
exactly three robots and the 10x10 dimensions. Separate simulations share no mutable
state. This is a sequential in-memory simulation, not a concurrent execution engine.

## Actions

| Method | Behavior |
| --- | --- |
| `WarehouseSimulation()` | Initialize the fixed warehouse |
| `robot_positions()` | Inspect robot positions by ID |
| `obstacles()` | Inspect permanent shelf cells |
| `get_robot(robot_id)` | Inspect position, battery, and status |
| `create_order(order_id, package_id, pickup, dropoff)` | Add one validated pending order |
| `add_blocked_cell(cell)` | Add a temporary block; an existing block is a no-op |
| `remove_blocked_cell(cell)` | Remove a temporary block; a missing in-bounds block is a no-op |
| `move_robot(robot_id, destination)` | Apply one validated orthogonal step |

Movement has Manhattan distance exactly one: no diagonals, jumps, or stationary
moves. The destination must be inside the grid and cannot be a shelf, temporary
block, or another robot's cell. Successful moves cost one battery percentage point.
An `idle` or `busy` robot may move if it has battery remaining; `charging` and
`offline` robots cannot move. Status stays unchanged because each step is immediate
and this phase does not assign work. Moving does not pick up or deliver packages.

Invalid actions raise `ValueError` (including Pydantic `ValidationError`); unknown
robot IDs raise `KeyError`. All rejected actions preserve the entire snapshot,
including battery. A robot may spend its last battery point, then cannot move again.
Status changes and charging are not yet provided as simulation actions.

## Run locally

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The root `pytest.ini` sets `testpaths = backend/tests` and `pythonpath = backend`.
It prevents accidental use of the parent course project's pytest configuration.
Tests require no API keys or LLM access.

To experiment interactively, start Python from `backend/`:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe
```

Then:

```python
from app.warehouse import Position, WarehouseSimulation

warehouse = WarehouseSimulation()
warehouse.robot_positions()
warehouse.obstacles()
warehouse.create_order("order-1", "package-1", Position(x=2, y=2), Position(x=9, y=0))
warehouse.move_robot("robot-1", Position(x=1, y=0))  # battery: 99
warehouse.add_blocked_cell(Position(x=2, y=0))
warehouse.remove_blocked_cell(Position(x=2, y=0))
```

`app` is importable from `backend/` or through pytest's configured Python path;
the project is not installed as an editable Python distribution.

## Next boundary

This request combines the original roadmap's domain-model and basic simulation
steps, with focused tests. A* routing is the next proposed implementation step
and requires a new explicit request. Pickup/delivery execution and later graph
integration are still future work.
