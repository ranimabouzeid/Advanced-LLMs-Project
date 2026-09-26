# Battery execution investigation

Investigated `ranim` at `584c8f3` on 2026-09-26, after fetching and confirming
that `origin/ranim` matched the local commit. The working tree was initially clean.
The reported battery loss was not reproduced. No production accounting change
was justified by the execution trace or the regression results below.

## State flow

1. `graph/batch.py` starts forecasts from committed robots. `collect` subtracts
   delivery cost from the selected forecast only. `project` and
   `schedules.project_parking` build private projected snapshots.
2. `schedules.finalize` records the final projected robot, including parking.
   `batch.finish` clears scratch `robot_forecasts`; the finalized projection
   remains in `robot_schedules[*].projected_robot`. Plan leaves `warehouse`
   unchanged. Execute review also uses private projection.
3. `schedules.execute` starts from `state.warehouse` and calls
   `batch.apply_delivery`, which constructs a `WarehouseSimulation` from that
   snapshot. `execute_delivery` validates and moves in a temporary simulation.
   Each `move_robot` subtracts one battery point; assignment, pickup and delivery
   do not add costs. Finalization checks actual battery against initial battery
   minus movement steps before publishing a snapshot.
4. Each successful delivery replaces the schedule loop's local `warehouse` with
   `result.final_state`. The next delivery receives that updated battery.
   `execute_parking` likewise moves in a private simulation using `move_robot`.
   Successful parking replaces the local warehouse before the next robot runs.
   Execution never rebuilds committed robots from forecast or schedule objects.
5. Each successful delivery or parking action publishes one revision. Failed
   actions discard all their temporary movement and battery changes; completed
   earlier actions remain in the returned partial result.
6. The execution node returns this warehouse to the graph. `SessionCoordinator`
   validates the terminal checkpoint and makes it authoritative. FastAPI returns
   that graph state; subsequent GET reads the same committed checkpoint.
7. `useWarehouseSession` replaces its session with the command response.
   `RobotPanel` uses `state.warehouse.robots` for battery labels/meters. Detailed
   schedule battery is explicitly labeled as projected, and is a separate value.

## Regression evidence

`backend/tests/test_battery_execution.py` adds nine offline cases covering:

- A 10-step atomic delivery: 100 to 90, one revision, other robots unchanged.
- HTTP Plan/Execute and checkpoint readback for delivery plus parking:
  100 minus 10 minus 4 equals 86.
- HTTP Plan/Execute and checkpoint readback for two chained deliveries plus
  parking: 100 minus 10 minus 7 minus 4 equals 79, three revisions.
- Planning forecasts of 100, 90 and 83 while committed battery stays 100;
  finalized parking projection is 79.
- Separate robot schedules pay their own costs; an unused robot stays unchanged.
- Failures after all temporary movement in either delivery discard that
  delivery's battery changes, preserving only any completed prefix.
- Failure after two temporary parking steps retains delivered battery 83;
  a new Plan/Execute parks from that committed snapshot and reaches 79.
- A later batch starts from parked battery 79 and finishes at 71 after another
  four-step delivery and four-step parking action.

`frontend/src/components/RobotPanel.test.jsx` adds a regression that renders
committed battery changes from 100 to 83 to 79 while intentionally providing
different forecast and schedule battery values. The existing App Execute test
also checks replacement of displayed battery after an API response.

These checks use real simulation, graph, checkpoint and HTTP code with offline
model responses. They do not establish the cause of the reported live behavior.
To investigate that discrepancy, capture the failing order inputs and Execute
response (especially `outcome`, `warehouse.revision`, `warehouse.robots`, delivery
statuses and parking statuses), plus the running backend revision. A review-only
replacement that returns READY has not moved robots and should not spend battery.

## Verification

- `.venv/Scripts/python.exe -m pytest`: 630 passed; existing Starlette
  deprecation warning remains.
- `npm.cmd test -- --run --testTimeout=15000`: 93 passed across 13 files.
  The initial default-timeout run had 91 passes and two failures: the reset
  interaction exceeded five seconds, and the following test failed. Both pass
  with the longer per-test timeout; no test configuration was changed.
- `npm.cmd run build`: passed.
- `.venv/Scripts/python.exe -m pip check`: no broken requirements.
- `git diff --check`: passed.

No live Groq calls were made.
