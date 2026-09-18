# SWARMDOCK: Programming Phase and A* Pathfinding

> This presentation records the foundation and A* phase (85 tests at that time).
> Subsequent work added lifecycle primitives, complete delivery planning, and typed
> validation, and atomic delivery execution, bringing the suite to 169 tests.
> See [current warehouse documentation](warehouse.md) for those additions.
> Milestone A is complete; graph and application layers remain future work.

## 1. Project introduction

SWARMDOCK is a university project for an autonomous multi-agent warehouse
simulation. The long-term objective is to coordinate order selection, robot
assignment, route planning, and safety validation through LangGraph.

The completed programming work establishes the foundation: a validated warehouse
model, deterministic simulation actions, and A* route planning. These components
can run and be tested without an LLM, web server, or frontend.

**Presentation opening:**

> We first built the warehouse rules and routing algorithm in Python. This gives
> the future agents reliable tools: agents will coordinate decisions, while the
> simulation and A* will enforce movement rules and calculate routes.

## 2. Architecture and current progress

The planned final architecture is:

```text
React frontend                         Future
       ↓
FastAPI backend                        Future
       ↓
LangGraph multi-agent workflow         Future
       ↓
Deterministic Python tools              Implemented foundation
       ↓
Warehouse simulation and A* routing     Implemented
```

| Development stage | Completed work |
| --- | --- |
| Environment setup | Python 3.11.9, isolated `.venv`, dependencies, Git ignores |
| Domain model and simulation | Pydantic models, fixed warehouse, orders, blocked cells, robot movement |
| Route planning | Reusable deterministic A* function and routing tests |
| Verification | 85 passing automated test cases in the most recent full run |

The requested domain-model phase included basic simulation, combining steps 2
and 3 of the original development roadmap. A* followed as a separate task.

## 3. Programming tools and module structure

| Technology | Role in this phase |
| --- | --- |
| Python 3.11.9 | Domain logic, simulation, and route planning |
| Pydantic | Structured records and input/state validation |
| `heapq` from Python's standard library | A* priority queue |
| pytest | Deterministic automated tests |
| Windows and VS Code | Local development environment |

LangGraph, LangChain, FastAPI, python-dotenv, and NetworkX are installed for later
use or available support. The current A* implementation uses Python's standard
library rather than NetworkX or an LLM.

```text
backend/
├── app/
│   ├── __init__.py
│   └── warehouse/
│       ├── __init__.py      # Public imports
│       ├── models.py        # Validated domain records and warehouse state
│       ├── simulation.py    # Initialization and state-changing actions
│       └── routing.py       # Pure A* pathfinding function
└── tests/
    ├── test_models.py
    ├── test_simulation.py
    └── test_routing.py

pytest.ini                  # Repository-local test discovery and import setup
```

Separating these responsibilities makes it possible to test routing independently
and later expose the same function as a tool for the Route Agent.

## 4. Warehouse representation

The initial warehouse is a logical grid rather than a physical or graphical model.

| Property | Initial value |
| --- | --- |
| Dimensions | 10 × 10, or 100 cells |
| Coordinates | Zero-based `(x, y)`; valid warehouse values are 0 through 9 |
| Robots | Three |
| Starting positions | `(0,0)`, `(0,1)`, `(0,2)` |
| Battery | 100% for every robot |
| Robot status | `idle` |
| Shelf obstacles | 12 cells: columns 3 and 6, rows 2 through 7 |
| Drop-off cells | `(9,0)` and `(9,9)` |
| Orders and temporary blocks | Empty initially |

### Initial warehouse map

```text
      x → 0 1 2 3 4 5 6 7 8 9
y  0      1 . . . . . . . . D
   1      2 . . . . . . . . .
   2      3 . . # . . # . . .
   3      . . . # . . # . . .
   4      . . . # . . # . . .
   5      . . . # . . # . . .
   6      . . . # . . # . . .
   7      . . . # . . # . . .
   8      . . . . . . . . . .
   9      . . . . . . . . . D
```

**Legend:** `1–3` = robots, `#` = shelves, `D` = drop-off, `.` = free cell.

Permanent shelves cannot be traversed. Temporary blocks represent cells that are
currently unavailable and can be added or removed through simulation methods.
Package pickup coordinates represent accessible cells next to storage, not cells
inside an impassable shelf.

## 5. Domain models and validation

| Model | Main information |
| --- | --- |
| `Position` | Integer `x` and `y` coordinates |
| `Robot` | Identifier, position, battery, and status |
| `Package` | Identifier and pickup coordinate |
| `Order` | Identifier, one package, drop-off coordinate, pending status |
| `WarehouseState` | Dimensions, robots, obstacles, blocks, drop-offs, and orders |

Pydantic rejects invalid records and inconsistent warehouse snapshots. Examples
include battery values outside 0–100, blank identifiers, duplicate robot IDs,
robots sharing a cell, and warehouse coordinates outside the grid.

`Position` represents any integer pair; bounds are checked when that position is
used in a warehouse or routing request. This lets invalid movement requests produce
clear domain errors rather than hiding boundary checks inside the coordinate type.

Models are frozen, and collections use immutable tuples and frozensets. An action
constructs and validates a new snapshot before replacing the current state. This
means a rejected action leaves robot positions, battery, and orders unchanged.

## 6. Deterministic simulation behavior

The simulation currently supports:

1. Initializing the fixed warehouse.
2. Inspecting robots and obstacles.
3. Creating an order with package pickup and drop-off coordinates.
4. Adding and removing temporary blocked cells.
5. Moving one robot to an adjacent valid cell.
6. Rejecting invalid actions without partially changing state.

### Movement rules

- Movement is sequential, one cell at a time.
- Only horizontal and vertical moves are allowed.
- Diagonal moves, jumps, and staying in the same cell are rejected.
- A robot cannot move outside the grid or into a shelf, blocked cell, or another robot.
- Each successful move consumes one battery percentage point.
- `idle` and `busy` robots may move; `charging` and `offline` robots may not.
- Battery must be greater than zero before a move.
- Movement does not change status, pick up a package, or complete an order.

**Example:** four successful steps reduce a robot's battery from 100% to 96%.
A rejected fifth step leaves it at 96% and at its last valid position.

Here, *deterministic* means the same initial state and action sequence produce
the same result. Initialization does not depend on randomness, timestamps, or
external services.

## 7. Pathfinding problem and function contract

The route planner answers:

> Given the grid, a start, a destination, and unavailable cells, what is a shortest
> sequence of valid adjacent cells connecting the start to the destination?

```text
Dimensions + start + goal + shelves + temporary blocks
                         ↓
                   A* pathfinding
                         ↓
        Coordinate route, or None if unreachable
```

The public function is:

```python
astar_path(width, height, start, goal, obstacles=(), blocked_cells=())
```

Coordinates use the existing `Position` model. The planner accepts any positive
integer grid dimensions, even though the current warehouse model is fixed at 10×10.

| Situation | Result |
| --- | --- |
| Valid reachable destination | `list[Position]`, including start and goal |
| No route exists | `None` |
| Start or goal is blocked or a shelf | `None` |
| Start equals goal and the cell is free | `[start]`, requiring zero moves |
| Invalid dimensions or out-of-bounds input coordinates | `ValueError` |

A route with 14 coordinates represents **13 moves**, because the first coordinate
is the starting position.

## 8. How A* works

A* explores promising cells using this score:

```text
f(cell) = g(cell) + h(cell)
```

- **g:** actual number of steps from the start to that cell.
- **h:** estimated remaining number of steps to the goal.
- **f:** estimated total route cost through that cell.

### Manhattan-distance heuristic

For four-direction movement, the implementation uses:

```text
h = |current.x − goal.x| + |current.y − goal.y|
```

For example, from `(1,1)` to `(8,7)`:

```text
h = |1 − 8| + |1 − 7| = 7 + 6 = 13
```

This is a lower bound: obstacles may require a detour, but they cannot reduce the
number of horizontal and vertical moves needed. With unit-cost orthogonal movement,
this heuristic supports finding a shortest route.

### Search procedure

1. Validate dimensions and input coordinates; combine shelves and temporary blocks.
2. Add the start to a priority queue, with cost zero.
3. Remove the queued cell with the smallest `f` score.
4. Ignore an outdated queue entry if a cheaper cost was recorded later.
5. If the cell is the goal, follow predecessor links backward and reverse the result.
6. Otherwise inspect its four neighbors and discard out-of-bounds or unavailable cells.
7. Record and queue a neighbor only when the new route to it is cheaper.
8. Repeat until the goal is reached or the queue is empty.

The implementation uses a dictionary of best costs and a dictionary of predecessor
cells. A priority queue avoids scanning every candidate to choose the next cell.

### Deterministic tie-breaking

Neighbors are considered in this fixed order:

```text
+x → +y → −x → −y
```

An insertion counter resolves equal priority scores. Therefore identical inputs
produce the same selected route, even if several shortest routes exist or the
obstacle collection arrives in a different order.

## 9. Small visual example

This separate 3×3 example illustrates the algorithm; it is not a replacement for
the default 10×10 warehouse.

```text
Start:    (0,1)
Goal:     (2,1)
Obstacle: (1,1)

      x → 0 1 2
y  0      . . .
   1      S # G
   2      * * *
```

The verified output of the current implementation is:

```python
[(0, 1), (0, 2), (1, 2), (2, 2), (2, 1)]
```

| Measurement | Value |
| --- | --- |
| Direct distance without the obstacle | 2 moves |
| Valid shortest detour | 4 moves |
| Coordinates in the returned route | 5 |
| Battery cost if executed under simulation rules | 4 percentage points |

The equally short route through row 0 is also valid; fixed tie-breaking selects
the row-2 route shown here.

## 10. Planning versus execution

`astar_path()` calculates a route and does not move a robot. `move_robot()` applies
one step and validates it against the current simulation state.

```text
Read warehouse snapshot
        ↓
Plan route using A*
        ↓
Check that a route exists
        ↓
Execute route[1:] one step at a time using move_robot()
```

The start coordinate is skipped during execution because the robot is already there.

Other robots' positions are not implicit pathfinding inputs. A caller can add them
to the blocked-cell collection. This prevents planning through their current
positions but does not predict simultaneous future motion.

If a cell becomes blocked after planning, the old route may no longer be usable.
Execution still rejects the invalid step; a caller must request a new route from
a fresh snapshot. Replanning is not automatically orchestrated in this phase.

## 11. Testing and results

The most recent complete test run used Python 3.11.9 and reported:

```text
85 passed
Exit code: 0
```

| Test area | Cases | Examples |
| --- | ---: | --- |
| Domain models | 26 | Invalid battery, coordinates, duplicate IDs, inconsistent layouts, serialization |
| Simulation | 33 | Initialization, orders, blocking/unblocking, movement, rejected-action atomicity |
| Routing | 26 | Normal route, obstacle detour, blocked aisle, unreachable goal, equal endpoints |
| Total | 85 | All passed |

Routing tests also check boundary handling, narrow/rectangular grids, blocked
endpoints, stable tie-breaking, unchanged inputs, and execution through the simulation.

One routing test compares A* with an independent breadth-first search reference
across **128 obstacle layouts** on a 3×3 grid with fixed free start and goal cells.
It checks both reachability and shortest path length. Those 128 layouts are checked
inside one test and are not 128 additional pytest cases.

Breadth-first search is used only as a test reference. The production route planner
is A*. Passing these tests provides evidence for the covered behaviors; it is not
a claim of complete real-world warehouse safety.

## 12. Presentation demonstration

### Run the automated tests

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Show the visual example's actual route

Open Python from the backend directory:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe
```

Paste:

```python
from app.warehouse import Position, astar_path

route = astar_path(
    width=3,
    height=3,
    start=Position(x=0, y=1),
    goal=Position(x=2, y=1),
    obstacles={Position(x=1, y=1)},
)
print([(cell.x, cell.y) for cell in route])
print("Moves:", len(route) - 1)
```

Expected output:

```text
[(0, 1), (0, 2), (1, 2), (2, 2), (2, 1)]
Moves: 4
```

For an unreachable example, replace the obstacle collection with a complete wall:

```python
wall = {Position(x=1, y=y) for y in range(3)}
print(astar_path(3, 3, Position(x=0, y=1), Position(x=2, y=1), wall))
# None
```

## 13. Current limitations and future integration

- Orders remain pending; pickup and delivery execution are not implemented.
- Charging and status transitions are not simulation actions yet.
- A* minimizes the number of steps; it does not check whether battery is sufficient.
- Simulation execution is sequential, without concurrent robot scheduling.
- Collision prediction across multiple robots' future routes is not implemented.
- The program currently uses logical coordinates, not a graphical interface.
- LangGraph, LLM agents, FastAPI, and React remain future phases.

Later, the Order Agent will select an order, the Fleet Agent will select a robot,
the Route Agent will call this deterministic A* function, and the Safety Agent
will validate the proposed route. They will communicate through shared LangGraph
state. Safety results will control whether to execute or replan.

## 14. Suggested presentation outline

| Section | Main message |
| --- | --- |
| Project objective | SWARMDOCK will coordinate warehouse tasks through specialized agents |
| Current architecture | The deterministic foundation is implemented; orchestration is future work |
| Warehouse model | A 10×10 grid with validated robots, packages, orders, shelves, and blocks |
| Simulation | Explicit actions enforce valid movement and preserve state on failure |
| A* explanation | Actual cost plus Manhattan-distance estimate guides the search |
| Visual demonstration | A blocked direct path becomes a four-step detour |
| Verification | 85 tests passed, including independent shortest-path comparisons |
| Next steps | Integrate higher-level validation and orchestration incrementally |

**Closing statement:**

> We now have a tested Python warehouse and a reusable shortest-route planner.
> The next layers can coordinate these tools while keeping routing and movement
> rules deterministic and independently verifiable.

## Project references

- [Domain models](../backend/app/warehouse/models.py)
- [Simulation actions](../backend/app/warehouse/simulation.py)
- [A* implementation](../backend/app/warehouse/routing.py)
- [Routing tests](../backend/tests/test_routing.py)
- [Detailed warehouse usage](warehouse.md)
- [Project architecture and development rules](../AGENTS.md)

Frontend:
- React + Vite dashboard
- npm install
- npm run dev
- npm test -- --run
- npm run build

Backend:
- FastAPI/Uvicorn startup command

Architecture:
- React → FastAPI → SessionCoordinator → LangGraph
- synchronous Plan/Execute
- no WebSockets
- no fake live agent activity
- replacement route requires another manual Execute
- Reset returns a new session
- frontend derives warehouse display from server state

Verified:
- 47 frontend tests passing
- 498 backend tests passing
- production frontend build passing