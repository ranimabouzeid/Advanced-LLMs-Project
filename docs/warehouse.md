# SWARMDOCK warehouse: domain model and deterministic simulation

The current implementation includes domain data, sequential lifecycle actions,
deterministic A*, complete two-leg delivery planning, and typed validation.
Atomic full-delivery execution, charging, LangGraph, LLM agents, HTTP, and UI remain
outside the implemented scope.

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
| `get_order(order_id)` | Inspect an order and its lifecycle |
| `pending_orders()` | Return eligible pending orders in creation order |
| `assign_order(order_id, robot_id)` | Assign a pending order to an idle robot and mark it busy |
| `pickup_package(order_id, robot_id)` | Collect the assigned package at its pickup cell |
| `deliver_package(order_id, robot_id)` | Deliver the carried package at its drop-off and return the robot to idle |
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
Lifecycle actions change status as documented below. Charging is not implemented.

## Delivery lifecycle and domain invariants

```text
pending → assigned → picked_up → delivered
```

- `Order.assigned_robot_id` is `None` for pending orders and required for all later
  statuses. Delivered orders retain the assigned robot ID as history.
- `Robot.carried_package_id` is optional and represents at most one carried package.
  The order status remains the only package lifecycle; `Package.pickup` is the
  original pickup location, not an independently updated live package position.
- Assignment requires a pending order and an idle, empty robot. The robot becomes
  busy. A robot cannot have two active assigned/picked-up orders.
- Pickup requires the explicitly supplied robot ID to match the assignment,
  an empty busy robot, and the correct pickup position. It changes the order to
  picked-up and sets the robot's carried package ID.
- Delivery requires the assigned busy robot, matching carried package, and correct
  drop-off position. It clears carrying, returns the robot to idle, and marks the
  order delivered. That robot may subsequently be assigned another order.
- Duplicate assignment, pickup, and delivery are rejected. Package IDs remain unique
  across all orders, including delivered history. Delivered orders never appear in
  `pending_orders()`.
- Assignment, pickup, and delivery consume zero battery. A robot with zero battery
  may perform a zero-movement pickup/drop-off if all other conditions hold.
- Existing generic busy robots without an active order remain supported for movement.
  A carried package, however, must correspond to a picked-up order on that robot.

Each primitive validates the combined robot/order snapshot before publication.
Invalid actions raise `ValueError`; unknown order or robot IDs raise `KeyError`.
Model-level validation also rejects inconsistent assignment/carrying snapshots.

### Warehouse revision

`WarehouseState.revision` starts at zero. Each successful state-changing primitive
increments it once. Failed actions, adding an existing block, or removing an absent
in-bounds block leave it unchanged. Blocking and unblocking restores cell contents
but advances the revision twice. Constructing a simulation from a snapshot retains
that snapshot's revision.

Revision identifies changes within the current simulation history. It is not a
global identifier across unrelated simulations, and it does not add thread/session
management. Later graph state should use this field rather than maintain a second
independent warehouse revision.

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

## Deterministic A* route planning

`backend/app/warehouse/routing.py` exports the pure function:

```python
astar_path(width, height, start, goal, obstacles=(), blocked_cells=())
```

Start, goal, and obstacle collections use `Position` values. The function accepts
any positive integer dimensions, although `WarehouseState` remains fixed at 10x10.
It returns a `list[Position]` including both endpoints, or `None` if disconnected
or if either endpoint is obstructed. A free start equal to the goal returns
`[start]`. Invalid dimensions or out-of-bounds input cells raise `ValueError`.
Obstacle collections can be lists, sets, or other iterables; overlap is harmless.

A* uses a priority queue ordered by `f = g + h`:

- `g`: number of steps taken from the start.
- `h`: Manhattan distance, `abs(x - goal.x) + abs(y - goal.y)`.

Every move is orthogonal and costs one, so Manhattan distance never overestimates
the remaining cost. A* keeps the cheapest known cost for each cell, records its
predecessor, and reconstructs a shortest route when the goal is removed from the
queue. If the queue empties, no route exists. Neighbors are considered in `+x`,
`+y`, `-x`, `-y` order, with insertion order breaking equal queue priorities.
This makes equal-length route selection deterministic, independent of obstacle
collection order. The function does not mutate inputs or simulation state.

Example using an existing simulation (imports work from `backend/`):

```python
from app.warehouse import Position, WarehouseSimulation, astar_path

warehouse = WarehouseSimulation()
snapshot = warehouse.state
robot = warehouse.get_robot("robot-1")
other_positions = {r.position for r in snapshot.robots if r.id != robot.id}
route = astar_path(
    snapshot.width, snapshot.height, robot.position, Position(x=8, y=7),
    snapshot.obstacles, snapshot.blocked_cells | other_positions,
)
if route is not None:
    print([(cell.x, cell.y) for cell in route])
    print("Steps:", len(route) - 1)
    # Optional execution remains a separate, validated operation:
    for cell in route[1:]:
        warehouse.move_robot(robot.id, cell)
```

Other robots are not implicit routing inputs: include their current positions
in `blocked_cells` when appropriate. This is a snapshot route, not a time-based
collision prediction or battery feasibility check. `move_robot` still validates
every executed step. If a new block appears after planning, replan using a fresh
snapshot; the old route is not modified automatically.

Routing tests cover required scenarios, invalid inputs, stable tie-breaking,
rectangular grids, simulation integration, and an independent breadth-first
search comparison across all 128 obstacle layouts of a 3x3 grid with fixed free
endpoints. Breadth-first search exists only in tests; production routing is A*.

## Next boundary

The authorized portion of Milestone A implements lifecycle primitives, delivery
plans, and validation only. Atomic full-delivery execution remains deferred by the
latest user instruction. No later milestone has begun.

## Complete delivery planning and typed validation

```python
plan_delivery(state, order_id, robot_id) -> DeliveryPlan | None
validate_delivery_plan(state, plan) -> ValidationResult
validate_route(state, robot_id, expected_start, expected_goal, route, *, leg=None)
```

`plan_delivery` is a pure helper alongside the unchanged `astar_path`. It plans
robot-to-pickup and pickup-to-drop-off routes using A* twice, with other robots'
current positions added to the blocked cells. It does not assign or move a robot.
Unknown IDs raise `KeyError`; a nonpending order or nonidle robot raises `ValueError`.
An unreachable leg returns `None`. A reachable plan can exceed available battery;
the validation result explicitly reports that failure rather than calling it unreachable.

`DeliveryPlan` is a frozen Pydantic model with `order_id`, `robot_id`,
`pickup_route`, `delivery_route`, `total_steps`, and `warehouse_revision`.
Route fields are nonempty tuples of `Position` for immutable snapshots. Both legs
include endpoints. The model enforces:

```text
total_steps = (len(pickup_route) - 1) + (len(delivery_route) - 1)
```

One-coordinate legs cost zero steps. Validation independently derives this cost,
so an understated `total_steps` cannot bypass the battery check. Actual A* route
lengths account for detours; Manhattan distance alone is insufficient.

`validate_route` checks nonempty input, expected endpoints, bounds, orthogonal
adjacency (including rejecting repeated stationary steps), shelves, blocked cells,
other robots, and movement-permitted status. Expected endpoints are explicit so
the delivery leg starts at pickup even though the robot has not moved there yet.

`validate_delivery_plan` also checks pending-order eligibility, an idle empty robot,
current revision, and sufficient battery for both legs combined. Complete-plan
validation is for a fresh proposal before assignment, not resuming a picked-up order.

The frozen `ValidationResult` contains:

- `route_valid`: true only when no reasons or conflicts were found.
- `collision_risk`: true only when another robot occupies a route cell.
- `reasons`: typed issues with code, message, optional cell, and optional leg.
- `conflicts`: typed findings identifying robot ID, cell, and optional leg.

An obstacle, stale revision, or depleted battery makes a plan invalid but does not
by itself imply a robot conflict. The result model rejects contradictory flags.
Standalone route validation accepts empty sequences so it can return an explicit
`empty_route` issue; the delivery-plan model also rejects empty legs at construction.

### Primitive-by-primitive demonstration

From Python launched in `backend/`:

```python
from app.warehouse import Position, WarehouseSimulation, plan_delivery, validate_delivery_plan

warehouse = WarehouseSimulation()
warehouse.create_order("order-1", "package-1", Position(x=2, y=0), Position(x=9, y=0))
plan = plan_delivery(warehouse.state, "order-1", "robot-1")
assert plan is not None
assert validate_delivery_plan(warehouse.state, plan).route_valid

warehouse.assign_order(plan.order_id, plan.robot_id)
for cell in plan.pickup_route[1:]:
    warehouse.move_robot(plan.robot_id, cell)
warehouse.pickup_package(plan.order_id, plan.robot_id)
for cell in plan.delivery_route[1:]:
    warehouse.move_robot(plan.robot_id, cell)
warehouse.deliver_package(plan.order_id, plan.robot_id)

assert warehouse.get_order("order-1").status.value == "delivered"
assert warehouse.get_robot("robot-1").battery == 91  # 2 + 7 moves
```

This sequence is deliberately not a transaction across the full delivery. If a
later primitive fails, earlier successful primitives remain committed. Each
primitive itself remains atomic. No `execute_delivery` method is implemented.
Assignment changes revision, so the original plan is no longer a fresh pending
proposal afterward; each explicit movement and lifecycle action still validates
current state. Full-plan atomic execution and its validation protocol are deferred.

### Compatibility and tests

Existing public signatures, A*, initial layout, and one-point movement costs remain
unchanged. New fields default to `None` or revision zero, so earlier pending-order
snapshots can still be loaded. Serialized output gains those fields. Consumers
comparing complete snapshots must account for revision changes.

The latest full suite contains 148 passing cases: 43 model, 44 simulation, 30
routing, and 31 validation cases. Coverage includes zero-move delivery, exact
battery boundaries, obstacle detours, stale plans, repeated lifecycle actions,
assignment/carrying consistency, and unchanged state after failed primitives.
