# Shared workflow state

The shared-state portion of Milestone B lives in `backend/app/graph/state.py`
and its deterministic partial-update helpers in `backend/app/graph/updates.py`.
`WarehouseGraphState` is a frozen Pydantic BaseModel with forbidden extra fields.
It imports the existing domain models; domain code does not import graph code.
All four standalone roles are implemented; graph wiring,
reducers, and checkpoint integration are not implemented. Shared model configuration lives outside graph state in
`backend/app/config.py`; no client is created at import time.

## Authority and ownership

`warehouse: WarehouseState` is the sole authoritative session snapshot.
`warehouse_revision` is a read-only Python property returning `warehouse.revision`;
it is not an independently accepted input or serialized field. Its nonnegative,
strict integer constraint comes from the existing warehouse model.
`DeliveryPlan.warehouse_revision` records the snapshot used to calculate a proposal.

| State field | Intended owner / meaning |
| --- | --- |
| `warehouse` | Trusted deterministic execution or domain actions publish a replacement snapshot |
| `command` | Command entry supplies `plan` or `execute`, independent of HTTP |
| `execution_requested` | Explicit execution intent; true requires an execute command |
| `order_selection` | Future Order role; ID and short explanation, checked against pending orders |
| `selected_robot_id` | Future Fleet role; identifier must exist in the snapshot |
| `delivery_plan` | Future Route role; existing two-leg `DeliveryPlan` |
| `planning_outcome` | Typed planning status: not_planned, planned, stale, unreachable, failed |
| `safety` | Future Safety role; existing `ValidationResult`, with None meaning unchecked |
| `replan_count` | Nonnegative strict integer; cannot exceed max_replans |
| `max_replans` | Session retry limit, default 3, allowed range 0–3; counts retries after initial planning |
| `run_outcome` | Typed idle/running/ready/delivered/no_work/no_robot/unreachable/failed result |
| `error_message` | Optional stripped explanation of 1–300 characters |
| `node_activity` | Ordered immutable tuple of typed node/status/optional-message records |

Order selections carry no invented priority score. Pending eligibility does not
imply reachability. Robot existence does not certify availability or battery.
There is no duplicate robot/order collection, completed-order count, or top-level
route_valid flag. Route validity exists only inside the nested safety result.

The schema validates references and numeric limits. The update helpers enforce
the invalidation contracts below. Direct schema construction does not certify a
`ready` outcome or enforce transitions. Constructing a safety
model is not evidence that deterministic validation ran; future Safety code must
publish actual validation output, and execution must independently revalidate.

## Serialization and future runtime integration

Use `model_dump_json()` and `WarehouseGraphState.model_validate_json(...)` for
JSON round trips. Existing warehouse serializers emit cell sets as sorted lists;
Pydantic reconstructs nested models, frozensets, and tuples on loading. Activity
tuple position preserves ordering without a timestamp or separate counter.
The activity collection uses a default factory; the schema stores no mutable lists.

Future tools receive the current snapshot from trusted node/runtime code. The LLM
must never supply authoritative warehouse_json. Future execution will construct a
temporary `WarehouseSimulation(snapshot)` and publish its successful final_state
as the replacement warehouse. Do not checkpoint that simulation or store the whole
DeliveryExecutionResult, which would duplicate its snapshot and validation result.
Clients and credentials likewise stay outside state.

JSON serialization, actual compiled-graph partial updates, and round trips through
the installed langgraph-checkpoint 4.2.0 JsonPlusSerializer are tested. Serializer
tests cover model objects and channel dictionaries for initial, approved, and
rejected states, with an explicit model-type allowlist and no pickle fallback.
This verifies serialization readiness, not MemorySaver integration. MemorySaver,
checkpoint retrieval, and session isolation/reset remain later-milestone work.
Milestone B's offline acceptance checks pass; the full suite has 250 passing tests.

## Partial updates and invalidation

Helpers return dictionaries containing only fields they own or deliberately
invalidate. They validate the complete merged candidate with `model_validate`,
then return those channels rather than the whole state. Explicit None clears a
channel; omitted keys preserve it. Existing states are never mutated. No reducers
are needed for these sequential replacement updates. Future nodes must use these
contracts rather than bypass them with unchecked `model_copy(update=...)` calls.

| Helper / owner | Outputs and invalidations |
| --- | --- |
| `select_order` / Order | Sets selection; clears robot, plan, safety, error, execution intent; resets planning, retries, and run outcome |
| `select_robot` / Fleet | Sets robot; preserves order; clears plan, safety, error, intent; resets planning, retries, and run outcome |
| `fresh_plan` / Route | Replaces plan, marks planned, clears safety/error/intent, marks running, resets replan_count to zero |
| `replan` / Route | Same proposal invalidation, but increments replan_count exactly once and rejects exhausted limits |
| `record_safety` / Safety | Sets deterministic findings and ready/failed outcome plus diagnostic; rejects findings that disagree with current validation |
| `replace_warehouse` / trusted domain caller | Replaces snapshot, clears safety/error/intent, marks idle; retains eligible selections and marks a retained plan stale |

Every helper preserves command, max_replans, and activity records. Selection
helpers invalidate even when explicitly selecting the same ID again. Warehouse
replacement clears selections/plans if their order is no longer pending or their
robot no longer exists. An identical snapshot returns an empty update.

Domain actions already increment revision. `replace_warehouse` requires a changed
snapshot to have exactly current revision + 1, and does not increment it again.
It accepts one domain action at a time, including atomic complete delivery; callers
must not batch several independently incremented snapshots into one update.

A retained stale plan keeps its original warehouse_revision. The derived
`is_plan_approved` helper requires matching selections, a matching current revision,
positive conflict-free safety, and agreement with current deterministic validation.
There is no additional stored approval field. New plans must match current
selections and revision. All new/retry proposals clear execution intent, so an
execute request cannot silently transfer permission to a replacement route.
Future execution must still require explicit intent and independently revalidate
immediately before committing; these helpers perform no movement.

Retries publish a supplied proposal and increment their own counter; fresh plans
reset it. Deciding whether new feedback is actionable, invoking A*, terminating
unreachable attempts, and conditional retry dispatch belong to future workflow
work. The test-only straight-line StateGraph verifies actual channel merging;
it introduces no production workflow, conditional edges, or checkpointer.

## Shared LLM configuration

`app.config` owns frozen `LLMSettings`, `load_settings`, and
`create_model_client`. Google Gemini is the initial supported provider; provider-specific
imports and credential mapping are localized here. No model ID is a code default.
`LLM_MODEL` must be explicitly configured before constructing a live client.

Application setup will call the factory once and retain its returned client for
injection into future nodes. The factory is not a global singleton or per-node
cache. Tests can pass `client=fake` without settings, keys, provider imports, or
network calls. The injection type is LangChain's `BaseChatModel`, supporting its
fake chat model subclasses and test mocks. Neither factory path invokes a model.

`load_settings()` reads the process environment. Dotenv loading is explicit:
`load_settings(env_file=".env")`. Process values override file values, and parsing
does not mutate the process environment. An explicit `environ={}` isolates tests.
Missing requested files raise an error; there is no parent-directory discovery or
dotenv variable interpolation.

| Variable | Meaning |
| --- | --- |
| `LLM_PROVIDER` | Currently `google_genai` (default); other values rejected |
| `LLM_MODEL` | Required nonblank model ID for live construction; no default |
| `GOOGLE_API_KEY` | Required secret for live construction; omitted from settings dumps/repr |
| `LLM_TIMEOUT_SECONDS` | Positive timeout up to 300 seconds; default 30 |
| `LLM_MAX_RETRIES` | Provider request retries, 0–5, default 2; distinct from route replanning |

Only `.env.example` placeholders belong in Git. Settings may be incomplete for
offline injection; live construction checks required model/key first, then lazily
imports `langchain-google-genai`. That optional package is not installed or added to the
base requirements because this milestone needs only configuration and offline
tests. The missing-package error explains the prerequisite for future live setup.

Provider capability references checked September 16, 2026:
[LangChain ChatGoogleGenerativeAI](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai)
documents the integration package, tool calling, and structured output.
[Google's Gemini model catalog](https://ai.google.dev/gemini-api/docs/models)
is the source for choosing the concrete model ID before live use; no model is a
permanent default or assumed available to the account. The factory explicitly
passes `vertexai=False` to use the Gemini Developer API with `GOOGLE_API_KEY`,
independent of ambient Vertex AI settings. This application uses only
`GOOGLE_API_KEY`, rather than the SDK's alternate credential variable fallback.
No live smoke test is implemented.

The audit also verified the concrete stable example
[`gemini-2.5-flash`](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash):
Google documents both function calling and structured outputs. It remains a
documentation example rather than a code default or a guarantee of account access.
The optional provider package is not installed, so real client construction and
live responses remain untested. Mocked construction and offline injection pass.

## Milestone C: Order Agent only

`graph/agents.py` exposes `order_agent(state, client=shared_client, tools=...)`.
The caller injects the shared client; the agent never creates a provider client.
`graph/tools.py` exposes only `OrderTools.pending_orders` and `order_metadata`,
which inspect the supplied immutable snapshot and return existing Order records.
There are no robot, routing, validation, or mutation tools in this toolset.

Tool invocation is explicit: the agent looks up pending orders in creation order,
reads their metadata, and supplies those results as model context. No model tool
dispatch loop exists and the model cannot provide warehouse_json. The prompt
prefers creation order, but eligibility (not model compliance with that preference)
is enforced in code; any returned pending ID is valid. No numeric priority exists.

`app.config.structured_output` wraps the injected client using
`with_structured_output(OrderSelection, method="json_schema")`. This requests
Gemini native structured output with the Pydantic class. The agent revalidates
the result with Pydantic, then checks membership in pending IDs from the actual
snapshot. The existing state validator provides an additional eligibility check.
Shape validation alone cannot authorize a nonexistent or non-pending ID.

`updates.order_result` uses `select_order` for success and the same existing
proposal-clearing helper for no-work/failure. All outcomes clear the previous
robot, plan, safety, execution intent, and retry count. Success writes one
OrderSelection and `running`; empty pending orders skip the model and write
`no_work`; invalid output or an ordinary tool/model error writes `failed` with a
short sanitized error. One Order activity record is appended. Warehouse, revision,
command, and max_replans are never updated by this agent.

Provider timeout configuration remains in the shared client. The agent handles
raised TimeoutError explicitly and other provider exceptions as generic failures;
it does not add a background timeout thread or retry loop. Process interrupts
are not swallowed. Tests use a fake chat model implementing the structured-output
boundary with real Pydantic JSON parsing, plus injected failure runnables. No
Gemini integration package, credentials, or API calls are needed for these tests.

The Order-only increment added 21 tests. Production graph wiring remains unimplemented.

## Milestone C: Fleet Agent

`fleet_agent(state, client=None, tools=None)` reads the authoritative warehouse and
existing order selection. Its separate `FleetTools` exposes robot status lookup,
two-leg Manhattan lower bound, and deterministic candidate evaluation. Evaluation
calls the existing `plan_delivery` A* implementation; temporary plans are discarded,
not published as a Route proposal. Candidate results include a typed exclusion
reason, battery, lower bound, and actual steps when reachable.

Eligibility requires a pending selected order, an idle empty robot, two reachable
route legs, and battery at least equal to actual combined A* steps. Other robots
remain obstacles through the existing planner. Manhattan is diagnostic only:
detours can cost more. Exactly sufficient battery passes; zero battery can pass
an entirely zero-step delivery. Busy, charging, offline, unreachable, and
insufficient-battery candidates are excluded.

Without a client, Fleet chooses minimum `(total_steps, robot_id)`, using lexical
identifier order for equal costs regardless of warehouse tuple ordering. Optional
model assistance receives only verified eligible candidate records through the
shared structured-output adapter. Its `FleetSelection(robot_id, explanation)` is
parsed by Pydantic and checked for both candidate membership and compliance with
the same deterministic policy. Invalid, malformed, or out-of-policy choices fail;
they are not silently replaced. The model cannot set cost, route, or safety fields.
Activity records use the deterministic selection reason rather than generated claims.

Per the Fleet-specific authorization, every empty eligible set produces `no_robot`,
including the all-unreachable case. Exclusion categories remain visible in activity
diagnostics. This refines the earlier proposal to emit unreachable from Fleet;
future Route planning can still produce the separate unreachable outcome.
No model call occurs for an empty candidate set. Missing order selection, ordinary
tool/model exceptions, and timeouts return `failed` with sanitized diagnostics.

`fleet_result` delegates successful selection to the existing `select_robot` helper
and shares its proposal-clearing behavior for failure/no_robot. The exact partial
update contains selected_robot_id, delivery_plan, safety, planning_outcome,
error_message, execution_requested, run_outcome, replan_count, and node_activity.
Plan/safety become None, planning becomes not_planned, execution intent becomes
false, and retries reset to zero. Success sets running, an empty candidate set sets
no_robot, and failures set failed. One Fleet activity record is appended.
Order selection, warehouse/revision, command, and max_replans are preserved.

The full suite has 307 passing offline tests, including 36 Fleet cases with actual
A* detours and injected structured-model/tool failures. No provider installation
or live credentials were needed. Safety was added in the subsequent increment below.

## Milestone C: Route Agent

`route_agent(state, tools=None)` performs one fresh planning attempt from the
authoritative warehouse, selected order ID, and selected robot ID. `RouteTools`
exposes only `build_delivery_plan(snapshot, order_id, robot_id)`, which delegates
directly to Milestone A's `plan_delivery` and returns its DeliveryPlan or None.
The existing planner calls A* for robot-to-pickup and pickup-to-drop-off, retaining
endpoints, total movement cost, and the current warehouse revision. There is no
duplicated pathfinding, model client, generated-coordinate input, or safety approval.

`route_result` uses the existing `fresh_plan` helper on success. Its partial update
contains delivery_plan, planning_outcome, safety, replan_count, error_message,
execution_requested, run_outcome, and node_activity. Fresh attempts reset retries
to zero, clear safety/errors/execution intent, and append one Route activity.
Success sets planning_outcome=planned and run_outcome=running; Safety has not run.
An unreachable leg sets both outcomes to unreachable and clears the previous plan.
Missing selections, invalid tool return types, and ordinary tool errors set failed
with a sanitized diagnostic and no plan. Neither selections nor warehouse change.

This entry point is explicitly fresh planning; future retry orchestration must
use the separate replan contract rather than calling this reset path as a retry.
No retry loop or conditional dispatch is implemented. Runtime tool injection is
trusted application/test code, not a model-facing extension point.

The full suite has 324 passing tests, including 17 Route cases covering zero-step
legs, obstacles represented by blocked cells, other robot occupancy, unreachable
endpoints, revision retention, exact domain-plan equality, failure handling, and
owned-field invalidation. Existing domain tests cover permanent shelf obstacles.
Tests use no credentials or live calls. Safety was added in the subsequent increment below.

## Milestone C: Safety Agent

`safety_agent(state, tools=None, client=None)` checks the current DeliveryPlan,
snapshot/revision, and selected order/robot. Retry counts and limits are preserved;
Safety does not schedule retries or execute movement. Its separate `SafetyTools`
exposes `check_delivery_plan`, delegating directly to Milestone A validation. That
function calls route validation for both legs, checks occupied robot cells and
conflicts, battery, lifecycle/status, endpoints, bounds, obstacles, and revision.
There is no second implementation of these rules.

`safety_result` uses `record_safety`, which independently compares the tool output
with deterministic validation before accepting it. The authoritative field is
`safety: ValidationResult`; route_valid and collision_risk remain nested, with all
reasons and conflicts retained. Missing validation or a tool failure clears prior
safety to None (unchecked), sets failed, and revokes execution intent. Missing or
mismatched selections fail safely. Malformed plan schemas additionally discard
the malformed proposal and mark planning failed; valid schemas with invalid route
geometry retain the plan and publish deterministic rejection findings.

Success writes safety, run_outcome=ready, error_message=None, and one activity
record. Rejection sets failed and clears execution_requested. No warehouse,
selection, retry, or command fields change. Ready is not movement permission:
later execution must still require intent and independently revalidate.

An optional injected client may produce `SafetyExplanation(explanation)` only,
after deterministic validation. Its text appears solely in an activity message
explicitly labeled non-authoritative. Even contradictory text cannot change
findings or flags. Model error/timeout/malformed summary preserves actual findings
but sets failed and revokes execution intent. Without a client no model call occurs.

The suite now has 347 passing offline tests, including 23 Safety cases. Coverage
includes safe plans, blocked/occupied cells, stale revision, battery/status,
malformed or missing plans, missing/mismatched selections, fabricated tool approval,
tool failures, contradictory fake-model summaries, and model failures/timeouts.
No Safety workflow edges, MemorySaver integration, API, or frontend were added.
