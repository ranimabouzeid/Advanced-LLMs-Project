# SWARMDOCK — Revised Project Completion Plan

## 1. Purpose, status, and scope

This plan describes how to finish the university warehouse simulation incrementally.
It revises the supplied draft around the code already present in this repository.
**This document is a proposal, not authorization to implement every remaining phase.**
Each implementation milestone still requires an explicit user request under [AGENTS.md](AGENTS.md).

The intended architecture remains:

```text
React frontend
    ↓
FastAPI service
    ↓
LangGraph workflow: Order → Fleet → Route → Safety
    ↓
Deterministic tools, warehouse simulation, and A*
```

### Verified foundation

| Component | Current status |
| --- | --- |
| Environment | Python 3.11.9 in root `.venv`; dependencies installed |
| Domain | Pydantic positions, robots, packages, pending orders, immutable warehouse snapshots |
| Simulation | Fixed 10×10 grid, three robots, 12 shelves, two drop-offs, blocking and single-step movement |
| A* | Deterministic shortest orthogonal routes; blocked cells respected; explicit unreachable result |
| Tests | Most recent full run: 85 passing cases (26 models, 33 simulation, 26 routing) |
| Graph, API, frontend | Not implemented |

The original roadmap's model and basic simulation steps were implemented together.
Its testing step has focused coverage, but new components still require new tests.
The remaining domain work below completes delivery behavior; it does not replace
the working simulation or A*.

### Planning assumptions

- Four team members; assignments below are suggested roles, not commands to run parallel agents.
- The supplied draft targeted September 29, 2026. Treat that as a tentative team
  deadline to confirm, not an independently verified course deadline.
- Confirm course code, submission format, and whether every agent must make an LLM
  call. The documented requirements specify at least three distinct agents,
  different toolsets, and at least one structured-output agent.
- The two-week schedule is a working estimate contingent on these assumptions.

## 2. Define the minimum complete demonstration

The final demonstration must complete a real order:

```text
Create pending order
→ Select eligible order
→ Select available robot
→ Plan robot-to-pickup and pickup-to-drop-off legs
→ Validate both legs and battery
→ Display proposed delivery route
→ Execute validated delivery
→ Mark order delivered and robot idle
```

A robot reaching the package is not a completed delivery. The demo must also show:

1. A temporary block introduced after planning invalidates the proposal.
2. Revalidation produces a new route for review, without executing the stale one.
3. A genuinely unreachable destination terminates with a clear result.
4. Missing work/resources and model errors terminate without moving anything.
5. Checkpoint retrieval and two isolated warehouse sessions work.

### Scope limits

Keep execution sequential: one active delivery proposal per session, three robots
treated as static obstacles except for the selected robot. Defer simultaneous
robot scheduling, time-expanded A*, charging behavior, databases, deployment,
authentication, rush mode, and elaborate animation.

## 3. Milestone A — Delivery lifecycle and deterministic validation

**Owners:** Person 2 leads; Person 4 helps with tests and demonstration fixtures.
**Dependency:** existing simulation and routing. Complete before agent integration.

### Domain extensions

Extend existing files rather than introduce a second simulation:

- `backend/app/warehouse/models.py`: explicit order lifecycle and assignment data.
- `backend/app/warehouse/simulation.py`: assignment, pickup, delivery, and complete
  delivery execution using existing movement checks.
- `backend/app/warehouse/validation.py`: new pure validation functions/results.
- Existing model/simulation tests plus new `backend/tests/test_validation.py`.

Proposed order lifecycle:

```text
pending → assigned → picked_up → delivered
```

An order stores its assigned robot ID after assignment. A robot can carry at most
one package, represented by an optional package ID. Do not duplicate the order's
lifecycle in a second independent package-status field.

Invariants to specify and test:

- Assignment requires an eligible pending order and an idle robot.
- Pickup requires the assigned robot at that order's pickup cell.
- Delivery requires that robot to carry the matching package at its drop-off.
- Delivery clears the carried package and returns the robot to idle.
- Delivered orders are excluded from future selection.
- A package cannot be assigned or collected twice.
- Successful movement still costs one battery percentage point; pickup/drop-off
  cost zero in this simplified model. No automatic charging is added.

### Complete delivery plan

Introduce a typed plan with selected order/robot IDs, pickup leg, delivery leg,
total step count, and the warehouse revision used to calculate it.

Both legs include endpoints. Execute each leg using `[1:]`, so the pickup junction
is not charged twice. A one-cell leg requires zero moves. Use A* path lengths for
battery feasibility; Manhattan distance alone is only a lower bound.

### Route validation contract

Validation must receive the selected robot, expected start/goal, route, and current
warehouse snapshot. For the delivery leg, the expected start is the pickup cell,
not the robot's original position.

Reject empty routes, wrong endpoints, out-of-bounds coordinates, diagonal/jump
steps, shelves, temporary blocks, and cells occupied by another robot. Validate
the robot's current status and sufficient battery for the complete delivery.

Use a typed deterministic result with `route_valid: bool`, `collision_risk: bool`,
and reason/conflict lists. Here collision risk means a detected conflict under
the sequential model, not an estimated probability. No arbitrary `0.5` threshold.

### Atomic execution policy

Execute the entire delivery against a temporary `WarehouseSimulation` built from
the current snapshot. Validate every transition. Publish its final snapshot only
when all steps, pickup, and delivery succeed. On failure publish no warehouse change.

This extends current per-action atomicity to the whole delivery and avoids a
half-moved robot or a falsely completed order. Intermediate motion is not live UI
state in the minimum demo. A later animation can replay recorded steps explicitly.

**Done when:** lifecycle tests, route validation tests, two-leg delivery, zero-step
legs, insufficient battery, and failed-delivery rollback all pass offline.

## 4. Milestone B — Shared state, ownership, and configuration

**Owner:** Person 1. Design contracts alongside Milestone A; integrate after A passes.

### One authoritative warehouse snapshot

Use a Pydantic graph state containing `warehouse: WarehouseState`. This immutable
snapshot, stored with graph checkpoints, is the authoritative domain state for
that session. Do not keep another mutable global simulation beside the graph.

Deterministic execution creates a temporary simulation from this snapshot and
returns a new snapshot as a partial graph update. Order creation and blocked-cell
changes likewise use existing validated domain actions, coordinated by the session
service. Read-only tools receive the current snapshot from the node/runtime; never
ask the LLM to supply or invent authoritative `warehouse_json`.

### Proposed graph fields

| Field group | Contents |
| --- | --- |
| Warehouse | Snapshot and nonnegative `warehouse_revision` |
| Command | Typed command/event and whether execution was requested |
| Selection | Optional structured order selection and selected robot ID |
| Plan | Optional two-leg plan and explicit planning outcome |
| Safety | Optional deterministic safety result; `None` means unchecked |
| Retry control | Nonnegative retry count and bounded maximum |
| Run outcome | Typed ready/delivered/no-work/no-robot/unreachable/failed result |
| Diagnostics | Short error message and ordered node activity records |

Avoid duplicate `route_valid` fields that can disagree. Store it once in the
nested safety result. Derive delivered-order totals from the warehouse instead
of maintaining a second counter that can drift.

Use enums/literals and bounded numeric fields where appropriate. A structured
Order selection needs an eligible order ID and a short selection explanation;
omit an ungrounded numeric priority score unless order priority data and a policy
are explicitly introduced.

### Partial update and reset rules

- Nodes return only fields they own or deliberately invalidate.
- A new order/robot selection clears dependent plans, safety results, and errors.
- A warehouse mutation increments its revision and clears safety approval.
- A stored route can remain visible as a stale proposal, but cannot execute until
  revalidated against the new revision.
- A fresh plan resets retry counters. A retry must not reset its own retry counter.
- Test actual compiled-graph updates, not just standalone Pydantic constructors.

### LLM configuration

Choose one provider before implementing live model calls. Verify its currently
available model, structured-output support, tool-calling support, and required
LangChain integration package. Do not treat model IDs in the original draft as fixed.

Add one shared configuration module and `.env.example`; initialize an injected
model client once per application setup rather than creating clients in every node.
No real keys in Git. Automated tests use fake clients. A small live smoke test may
be run separately once credentials and provider usage are authorized.

**Done when:** typed state validates, JSON/checkpoint serialization works, partial
updates and stale-state clearing are tested, and provider configuration is explicit.

## 5. Milestone C — Four agents and distinct tools

**Owners:** Person 1 (Order/Fleet), Person 2 (Route/Safety).

| Role | Tools | Authoritative output |
| --- | --- | --- |
| Order | Pending-order lookup, metadata lookup | Validated structured order selection |
| Fleet | Robot status, Manhattan lower bound, A* reachability/total delivery cost | Eligible available robot with sufficient battery |
| Route | A* planning for both legs | Typed plan calculated by deterministic code |
| Safety | Complete-route validation and occupied-cell checks | Deterministic safety result |

The Order Agent uses Pydantic Structured Output Mode. Validate that the returned
ID is pending and eligible; correct JSON shape alone is insufficient.

If Fleet uses model-assisted selection, restrict it to candidates verified by
deterministic tools. Establish stable tie-breaking for equal candidates.

Route and Safety node outputs must use actual tool results. An LLM must not fabricate
coordinates, approve a rejected route, or override a detected conflict. Confirm the
course's interpretation of agent roles: if all four require model calls, let models
coordinate their assigned tools, while code validates and enforces those results.

Define the tool invocation loop or explicit node-to-tool calls. Merely binding
tools to a model does not specify how returned tool requests are executed and fed
back. Keep tool argument schemas narrow and outcomes typed; do not require every
tool to return strings when a numeric or structured result is appropriate.

Test invalid selections, missing resources, invalid model output, tool failures,
and model timeouts. Agent failures return explicit terminal outcomes with no movement.

**Done when:** all four node contracts pass mocked-model tests, distinct tool access
is explicit, and deterministic output cannot be overridden by generated text.

## 6. Milestone D — Workflow, safety branches, and bounded replanning

**Owner:** Person 3 integrates; Person 2 owns safety and execution logic.

### Plan command

```text
Order → [no work: END]
  ↓
Fleet → [no eligible robot: END]
  ↓
Route → [unreachable: END]
  ↓
Safety → [valid, conflict-free: READY, END]
  └────→ [actionable conflict: Route retry]
  └────→ [malformed plan / exhausted retries: FAILED, END]
```

### Execute command

```text
Load existing proposal and current warehouse snapshot
  ↓
Safety revalidation
  ├── approved → atomic deterministic delivery → DELIVERED, END
  ├── changed conditions → Route → Safety → READY for renewed review, END
  └── missing proposal / terminal problem → explicit outcome, END
```

Implement command dispatch and early exits explicitly; unconditional Order-to-Fleet
edges cannot satisfy a no-orders termination test on their own.

Execution requires a present safety result with `route_valid is True` and
`collision_risk is False`, plus validation of the current warehouse revision.
Unknown safety is never approval. The execution node independently enforces this.

If revalidation changes a proposal, clear execution intent: return the replacement
for review rather than silently executing a different route.

### Retry semantics

- `max_replans = 3` means at most three retries after the initial planning attempt.
- Retry only with new actionable input, such as newly discovered occupied/blocked
  cells. Feed that information into A*, and increment once per retry.
- A* returning unreachable on a complete unchanged snapshot is terminal; running
  identical inputs three more times cannot fix it.
- Distinguish unreachable, no robot, model/tool error, and retry exhaustion.
- Reset stale safety results before each new route is checked.

Tests must cover all terminal paths, missing safety, conflicts, revision changes,
exact retry limits, unchanged unreachable inputs, and successful replanning with
actually changed input. Do not use a mocked A* that magically succeeds on unchanged
inputs as the sole evidence that recovery works.

**Done when:** a headless two-leg delivery and a block/replan/review flow work with
mocked LLMs, without executing rejected or stale proposals.

## 7. Milestone E — MemorySaver and session consistency

**Owner:** Person 3. Depends on the validated workflow.

Use one graph factory accepting an optional checkpointer; avoid duplicating node
and edge definitions in separate build functions. Verify the installed MemorySaver
API when implementing it.

Each session has a unique thread ID and its own checkpointed warehouse snapshot.
Initially serialize commands within each session; reject conflicting commands as
busy instead of allowing simultaneous writes. A lock/guard in the in-memory session
coordinator is sufficient for the single-process demo.

All domain mutations and graph invocations go through that coordinator. No API
handler independently edits a hidden simulation. Checkpoint the domain snapshot
and workflow results together. Reads expose the same committed revision.

Execution against a temporary simulation has no external side effects; checkpoint
replay cannot independently move an external robot. Prevent duplicate delivery by
checking order status and consuming/clearing the accepted plan. A second execute
command must not repeat movement or complete another order accidentally.

Reset starts a fresh thread/session and initial warehouse; old plans cannot execute
in the new session. In-memory checkpoints disappear on process restart. No disk
persistence, database, or multi-worker deployment is required.

**Done when:** checkpoint retrieval, same-thread continuity, two-session domain
isolation, reset isolation, repeated-execute handling, and serialized mutations pass.

## 8. Milestone F — Thin FastAPI service

**Owner:** Person 3. Begin after Milestones A–E are verified.

Proposed files: `backend/app/api/main.py`, `schemas.py`, and `sessions.py` for the
small in-memory command coordinator. Keep lifecycle rules in the warehouse layer,
workflow decisions in the graph, and HTTP mapping in the API.

### Proposed consistent API contract

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sessions` | Create an isolated warehouse session; return session ID |
| GET | `/api/sessions/{id}/state` | Return committed warehouse, revision, plan, outcome, and node activity |
| POST | `/api/sessions/{id}/orders` | Create a pending order without automatically executing it |
| POST | `/api/sessions/{id}/plan` | Run selection, planning, and safety; return a proposal |
| POST | `/api/sessions/{id}/execute` | Revalidate and execute, or return a replacement proposal/failure |
| POST | `/api/sessions/{id}/blocked-cells` | Add a temporary block and invalidate prior approval |
| DELETE | `/api/sessions/{id}/blocked-cells/{x}/{y}` | Remove a block and invalidate prior approval |
| POST | `/api/sessions/{id}/reset` | Return a fresh replacement session ID and initial state |

Use one coordinate shape everywhere. Example create-order body:

```json
{
  "order_id": "order-1",
  "package_id": "package-1",
  "pickup": {"x": 2, "y": 2},
  "dropoff": {"x": 9, "y": 0}
}
```

Map malformed input to 422, unknown sessions to 404, and conflicting/stale actions
to a documented conflict response. Expected workflow outcomes such as no eligible
robot or unreachable route should have explicit response fields, not generic 500s.
Configure CORS for the chosen local frontend origin.

Commands may be synchronous for this demo. Responses contain the actual ordered
node activity from the completed command. Do not promise live intermediate state
from an endpoint that returns only after all work finishes.

Use FastAPI TestClient with injected mocked models. Cover body schemas, errors,
plan/execute distinction, block/unblock, stale plan handling, reset, session
isolation, and duplicate execution.

**Done when:** a complete order and block/replan cycle can be demonstrated through
the API without a frontend.

## 9. Milestone G — React frontend

**Owner:** Person 4. Begin implementation after the graph/core are ready and the API
contract is verified. Before then, help with domain tests, fixtures, and UI sketches.

Verify supported Node/Vite versions at implementation time; an unpinned installer
command should not be documented as guaranteeing a particular Node major version.

Use a basic Vite React application with these responsibilities:

- Warehouse grid: robots, shelves, temporary blocks, packages, drop-offs, and both route legs.
- Robot panel: position, status, battery, and carried package.
- Order list: pending/assigned/picked-up/delivered labels from actual domain state.
- Controls: Create Order, Plan, Execute, Block, Unblock, Reset.
- Workflow panel: actual completed node activity, result, and rejection reasons.
- Optional expandable state view for explaining shared state in the course demo.

Show request loading state while a command runs. Show completed activity afterward;
do not animate invented real-time agent progress. Highlight stale proposals and
require renewed execution after a changed route is returned. Disable conflicting
controls while a request is active.

No rush-mode button, WebSockets, streaming infrastructure, or complex animation
is needed for the minimum submission. Record a clear demonstration rather than
adding an unrelated feature late in development.

**Done when:** the UI completes delivery and visibly demonstrates block, replan,
unreachable outcome, reset, and battery changes through real API responses.

## 10. Team ownership and proposed two-week schedule

| Person | Primary ownership | Early supporting work |
| --- | --- | --- |
| 1 | Graph state, model configuration, Order/Fleet roles | State and tool contracts |
| 2 | Domain lifecycle, validation, Route/Safety roles | Atomic execution tests |
| 3 | Graph integration, sessions/checkpoints, API | Contract review and graph test fixtures |
| 4 | Frontend and presentation | Domain tests, demo fixtures, UI sketches |

To avoid shared-file conflicts, assign one integrator for graph state and wiring.
Use role modules such as `graph/agents/order.py`, `fleet.py`, `route.py`, `safety.py`
with corresponding role tool modules if parallel work warrants it. Agree on input
and output schemas before splitting implementation. Person 3 owns final integration;
other contributors submit focused changes instead of all editing `graph.py`.

| Days | Main work | Gate |
| --- | --- | --- |
| 1–2 | Delivery lifecycle/validation, state ownership and contracts | A verified; B schema agreed |
| 3–5 | State tests, provider setup, role tools and agents | B–C verified |
| 6–7 | Graph branches, atomic delivery, replanning, MemorySaver | D–E headless demo verified |
| 8–9 | API and its integration tests | F verified |
| 10–11 | Minimal React UI against stable API | G functional |
| 12 | Full demo scenarios and regression fixes | End-to-end acceptance |
| 13–14 | Buffer, documentation, rehearsal, submission | Freeze features |

This schedule is tight. If graph integration slips, simplify frontend polish and
optional state displays rather than weaken delivery correctness or course criteria.
Do not start React early just to keep a teammate occupied. Explicit authorization
still gates each new implementation milestone.

## 11. Verification and submission evidence

### Required automated coverage

- Preserve the existing model, simulation, and A* tests.
- Add lifecycle, full-route validation, atomic execution, graph state, tools,
  agent contracts, graph branches, session/checkpoint, and API tests.
- Test missing orders/robots, insufficient battery, invalid model IDs/output,
  unreachable routes, stale plans, actual replanning, and retry exhaustion.
- Use mocked model clients; deterministic tests must run without API credentials.
- Do not optimize for a predicted test count. Completion depends on behaviors covered.

### Manual acceptance script

1. Create a session: verify three robots, 12 shelves, and two drop-offs.
2. Create a reachable order and press Plan: both legs are visible; no movement occurs.
3. Press Execute: robot reaches drop-off, battery decreases by route steps, order is delivered.
4. Repeat Execute: no duplicate movement or completion.
5. Create and plan another order; block a free cell on its proposed route.
6. Press Execute: stale route is rejected and a valid replacement is returned for review.
7. Press Execute again: complete the revised delivery.
8. Create an unreachable layout: show explicit failure without pointless repeated A* calls.
9. Demonstrate no orders/no eligible robot and a checkpoint retrieval.
10. Open a second session and verify changes are isolated; reset and verify old proposals are unusable.

For a deterministic safety-loop demonstration, use a controlled test fixture that
introduces a new conflict between planning and validation; do not rely on timing a
mouse click during a synchronous request.

### Course evidence checklist

- [ ] Four distinct roles, with documented responsibilities and separate toolsets.
- [ ] Pydantic BaseModel graph state with multiple types and optional fields.
- [ ] Actual meaningful partial updates demonstrated in a compiled graph.
- [ ] Pydantic structured output used by the Order Agent and validated against domain data.
- [ ] Conditional edge controlled by deterministic Safety results.
- [ ] Bounded retries and explicit early failure branches.
- [ ] MemorySaver checkpoints, state retrieval, and thread/domain isolation.
- [ ] Existing A* remains the production routing algorithm.
- [ ] Complete pickup-to-delivery scenario and current automated tests pass.
- [ ] README, presentation documentation, environment instructions, and demo script match the implementation.

## 12. Commands and repository boundaries

Run tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

After the API milestone exists, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --app-dir backend --reload --port 8000
```

Alternatively, from `backend/`, use `..\.venv\Scripts\python.exe`; the virtual
environment is at the repository root, not inside backend.

After the frontend milestone exists, run `npm run dev` from `frontend/`.
Its first setup installs only the dependencies actually needed by the agreed UI.
Keep `.venv`, `.env`, Python caches, frontend dependencies, and build output ignored;
update `.gitignore` when the frontend is introduced.

This plan adds no application code and does not change the Downloads source file.
The next implementation request should authorize Milestone A: delivery lifecycle
and complete deterministic route validation, before moving into LangGraph.
