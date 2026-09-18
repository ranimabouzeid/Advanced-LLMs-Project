"""Sequential batch nodes. Projection is private; only execution publishes warehouse.

The existing role functions operate on a short-lived view of the projected snapshot.
Their role-owned updates return to shared graph state; no clients live in that state.
"""

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from app.warehouse import WarehouseSimulation
from app.warehouse.models import DeliveryPlan, OrderStatus, WarehouseState
from app.warehouse.simulation import DeliveryExecutionResult
from .agents import order_agent, fleet_agent, route_agent, safety_agent
from .state import NodeActivity, PlannedDelivery, RobotForecast, RouteRetryFeedback, WarehouseGraphState
from .tools import FleetTools, OrderTools, RouteTools, SafetyTools
from .updates import StateUpdate


logger = logging.getLogger(__name__)


def workspace(state: WarehouseGraphState, **overrides) -> WarehouseGraphState:
    """Build a role-local view without mutating committed state.

    Assignment previews follow only the selected robot's forecast; finalization
    resolves all robots' physical occupancy in schedule order."""
    fields = {name: getattr(state, name) for name in (
        "command", "execution_requested", "order_selection", "selected_robot_id",
        "delivery_plan", "planning_outcome", "safety", "replan_count", "max_replans",
        "run_outcome", "error_message", "node_activity", "robot_forecasts", "route_retry_feedback")}
    fields.update(overrides)
    warehouse = state.projected_warehouse or state.warehouse
    selected = fields.get("selected_robot_id")
    if selected and state.robot_forecasts:
        forecast = next(item for item in state.robot_forecasts if item.robot.id == selected)
        # An assignment preview follows only this robot's timeline. Other robot
        # schedules are finalized later against actual sequential occupancy.
        warehouse = WarehouseState.model_validate({**state.warehouse.model_dump(),
            "revision": state.warehouse.revision + len(forecast.order_ids),
            "robots": [forecast.robot if r.id == selected else r for r in state.warehouse.robots],
            "orders": [{**o.model_dump(), "status": "delivered", "assigned_robot_id": selected}
                       if o.id in forecast.order_ids else o for o in state.warehouse.orders]})
    return WarehouseGraphState(warehouse=warehouse, **fields)


def start_planning(state: WarehouseGraphState, *, replacement: bool = False) -> StateUpdate:
    """Initialize pending assignments and independent forecasts from committed state.

    Clear prior proposals and execution intent; replacement consumes one retry."""
    return dict(projected_warehouse=state.warehouse,
                planning_queue=tuple(o.id for o in state.warehouse.orders if o.status == OrderStatus.PENDING),
                planning_index=0, planned_deliveries=(), batch_revision=state.warehouse.revision,
                order_selection=None, selected_robot_id=None, delivery_plan=None, safety=None,
                planning_outcome="not_planned", run_outcome="running", error_message=None,
                robot_forecasts=tuple(RobotForecast(robot=r) for r in state.warehouse.robots),
                robot_schedules=(),
                route_retry_feedback=state.route_retry_feedback if replacement else (),
                execution_requested=False, replan_count=state.replan_count + 1 if replacement else 0)


def dispatch(state: WarehouseGraphState) -> StateUpdate:
    """Start assignment planning or request schedule review for an explicit Execute."""
    if state.command == "plan":
        return start_planning(state)
    return dict(execution_requested=True, error_message=None, run_outcome="running",
                projected_warehouse=None, planning_queue=(), planning_index=0)


def order(state: WarehouseGraphState, *, client: BaseChatModel, tools: OrderTools | None = None) -> StateUpdate:
    """Choose the next pending assignment after clearing prior robot, route and Safety fields."""
    current = workspace(state, order_selection=None, selected_robot_id=None,
                        delivery_plan=None, safety=None, execution_requested=False)
    update = order_agent(current, client=client, tools=tools,
                         eligible_order_ids=state.planning_queue[state.planning_index:])
    # Role helpers start a fresh per-order budget; the batch owns the retry budget.
    return {**update, "replan_count": state.replan_count}


def fleet(state: WarehouseGraphState, *, client: BaseChatModel,
          tools: FleetTools | None = None) -> StateUpdate:
    """Invoke Fleet afresh with all robot forecasts and retain the batch retry budget."""
    current = workspace(state)
    update = fleet_agent(current, client=client, tools=tools)
    # Correlate each decision with its input projection, without logging prompts,
    # credentials, or provider exceptions. Repeated IDs alone do not imply reuse.
    logger.info("Fleet decision order=%r revision=%s robots=%r selected=%r outcome=%s",
                current.order_selection.order_id if current.order_selection else None,
                current.warehouse_revision,
                [(robot.id, robot.position.x, robot.position.y, robot.battery, robot.status.value)
                 for robot in ([f.robot for f in current.robot_forecasts] or current.warehouse.robots)],
                update.get("selected_robot_id"), update.get("run_outcome"))
    return {**update, "replan_count": state.replan_count}


def route(state: WarehouseGraphState, *, client: BaseChatModel, tools: RouteTools | None = None) -> StateUpdate:
    """Generate an LLM delivery preview on the selected robot timeline, preserving batch retries."""
    return {**route_agent(workspace(state), client=client, tools=tools), "replan_count": state.replan_count}


def project(warehouse: WarehouseState, plan: DeliveryPlan) -> WarehouseState:
    """Project an approved delivery endpoint and battery cost without executing movement.

    The package remains delivered at the drop-off; the robot is only temporarily
    there until its next pickup leg or final parking/staging departure."""
    order = next(order for order in warehouse.orders if order.id == plan.order_id)
    data = warehouse.model_dump()
    data["revision"] += 1
    data["robots"] = [{**robot.model_dump(), "position": order.dropoff,
                       "battery": robot.battery - plan.total_steps, "status": "idle",
                       "carried_package_id": None} if robot.id == plan.robot_id else robot
                      for robot in warehouse.robots]
    data["orders"] = [{**item.model_dump(), "status": "delivered", "assigned_robot_id": plan.robot_id}
                      if item.id == order.id else item for item in warehouse.orders]
    return WarehouseState.model_validate(data)


def safety(state: WarehouseGraphState, *, client: BaseChatModel,
           tools: SafetyTools | None = None) -> StateUpdate:
    """Assess a planning preview or review finalized schedules through the shared Safety LLM."""
    if state.robot_schedules and state.projected_warehouse is None:
        from .schedules import review
        return review(state, client=client, safety_tools=tools)
    if state.projected_warehouse is not None:
        return safety_agent(workspace(state), tools=tools, client=client)
    return dict(run_outcome="failed", execution_requested=False, safety=None,
                error_message="Safety requires a finalized unconsumed robot schedule",
                node_activity=(*state.node_activity, NodeActivity(node="safety", status="failed",
                    message="Safety requires a finalized unconsumed robot schedule")))


def retry_route(state: WarehouseGraphState, *, client: BaseChatModel,
                tools: RouteTools | None = None) -> StateUpdate:
    """Consume one retry and request new LLM coordinates using the rejected route and feedback."""
    if state.safety is None or state.safety.approved or state.replan_count >= state.max_replans:
        return failure(state)
    local = WarehouseGraphState.model_validate({**state.model_dump(),
        "replan_count": state.replan_count + 1, "execution_requested": False,
        "route_retry_feedback": (*state.route_retry_feedback, RouteRetryFeedback(
            robot_id=state.selected_robot_id, order_id=state.order_selection.order_id,
            previous_route=state.delivery_plan, safety=state.safety))})
    return {**route(local, client=client, tools=tools), "route_retry_feedback": local.route_retry_feedback}


def collect(state: WarehouseGraphState) -> StateUpdate:
    """Record the assignment and advance its robot's independent forecast.

    Subtract delivery steps and temporarily place the robot at the drop-off;
    parking is deferred until every batch assignment has been considered."""
    if not state.planning_queue:
        return {}
    approved = state.run_outcome == "ready"
    reason = None if approved else (state.error_message or state.node_activity[-1].message
                                    or "No feasible delivery")
    record = PlannedDelivery(order_id=state.order_selection.order_id,
                selection=state.order_selection, robot_id=state.selected_robot_id,
                delivery_plan=state.delivery_plan, safety=state.safety,
                status="approved" if approved else "unplannable", reason=reason)
    projected = state.projected_warehouse
    outcome = state.run_outcome
    error = state.error_message
    if approved:
        try:
            forecast = next(f for f in state.robot_forecasts if f.robot.id == record.robot_id)
            order = next(o for o in state.warehouse.orders if o.id == record.order_id)
            updated = RobotForecast(robot=forecast.robot.model_validate({**forecast.robot.model_dump(),
                "position": order.dropoff, "battery": forecast.robot.battery - record.delivery_plan.total_steps}),
                order_ids=(*forecast.order_ids, record.order_id))
        except ValueError:
            error = "Projected delivery violates warehouse data constraints"
            record = PlannedDelivery.model_validate({**record.model_dump(), "status": "unplannable", "reason": error})
            outcome = "failed"
    queue = (*state.planning_queue[:state.planning_index], record.order_id,
             *(oid for oid in state.planning_queue[state.planning_index:] if oid != record.order_id))
    return dict(planned_deliveries=(*state.planned_deliveries, record), planning_queue=queue,
                robot_forecasts=tuple(updated if approved and record.status == "approved" and f.robot.id == record.robot_id
                                      else f for f in state.robot_forecasts),
                run_outcome=outcome, error_message=error,
                projected_warehouse=projected, planning_index=state.planning_index + 1)


def finish(state: WarehouseGraphState) -> StateUpdate:
    """Clear planning scratch state and summarize the first approved delivery without executing it."""
    approved = next((item for item in state.planned_deliveries if item.status == "approved"), None)
    outcome = "ready" if approved else state.run_outcome
    if outcome == "running":
        outcome = "no_work"
    return dict(projected_warehouse=None, planning_queue=(), planning_index=0,
                route_retry_feedback=(),
                robot_forecasts=(),
                order_selection=approved.selection if approved else None,
                selected_robot_id=approved.robot_id if approved else None,
                delivery_plan=approved.delivery_plan if approved else None,
                safety=approved.safety if approved else None,
                planning_outcome="planned" if approved else state.planning_outcome,
                run_outcome=outcome, error_message=None if approved else state.error_message,
                execution_requested=False)


def replan(state: WarehouseGraphState) -> StateUpdate:
    """Rebuild assignments from committed state with one retry consumed and execution intent cleared."""
    return start_planning(state, replacement=True)


def failure(state: WarehouseGraphState) -> StateUpdate:
    """Stop the command, clear planning scratch state and intent, and retain committed warehouse data."""
    return dict(run_outcome="failed", execution_requested=False,
                route_retry_feedback=(),
                robot_forecasts=(),
                projected_warehouse=None, planning_queue=(), planning_index=0,
                error_message="Replan limit exhausted" if (state.planning_outcome == "stale"
                    and state.replan_count >= state.max_replans)
                else state.error_message or "Batch rejected")


def apply_delivery(warehouse: WarehouseState, plan: DeliveryPlan) -> DeliveryExecutionResult:
    """Atomically execute one model-approved delivery; simulation may reject invalid movement."""
    return WarehouseSimulation(warehouse).execute_delivery(plan)


def execution(state: WarehouseGraphState) -> StateUpdate:
    """Execute only finalized schedules that include departure to parking/staging.

    Delivery-only previews cannot execute and leave robots on service cells.
    """
    if not state.robot_schedules:
        return failure(state)
    from .schedules import execute
    return execute(state)
