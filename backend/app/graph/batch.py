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
from .state import NodeActivity, PlannedDelivery, RobotForecast, WarehouseGraphState
from .tools import FleetTools, OrderTools, RouteTools, SafetyTools
from .updates import StateUpdate


logger = logging.getLogger(__name__)


def workspace(state: WarehouseGraphState, **overrides) -> WarehouseGraphState:
    """Adapt the active proposal to a role's existing single-delivery contract."""
    fields = {name: getattr(state, name) for name in (
        "command", "execution_requested", "order_selection", "selected_robot_id",
        "delivery_plan", "planning_outcome", "safety", "replan_count", "max_replans",
        "run_outcome", "error_message", "node_activity", "robot_forecasts")}
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
    return dict(projected_warehouse=state.warehouse,
                planning_queue=tuple(o.id for o in state.warehouse.orders if o.status == OrderStatus.PENDING),
                planning_index=0, planned_deliveries=(), batch_revision=state.warehouse.revision,
                order_selection=None, selected_robot_id=None, delivery_plan=None, safety=None,
                planning_outcome="not_planned", run_outcome="running", error_message=None,
                robot_forecasts=tuple(RobotForecast(robot=r) for r in state.warehouse.robots),
                robot_schedules=(),
                execution_requested=False, replan_count=state.replan_count + 1 if replacement else 0)


def dispatch(state: WarehouseGraphState) -> StateUpdate:
    if state.command == "plan":
        return start_planning(state)
    return dict(execution_requested=True, error_message=None, run_outcome="running",
                projected_warehouse=None, planning_queue=(), planning_index=0)


def order(state: WarehouseGraphState, *, client: BaseChatModel, tools: OrderTools | None = None) -> StateUpdate:
    current = workspace(state, order_selection=None, selected_robot_id=None,
                        delivery_plan=None, safety=None, execution_requested=False)
    update = order_agent(current, client=client, tools=tools,
                         eligible_order_ids=state.planning_queue[state.planning_index:])
    # Role helpers start a fresh per-order budget; the batch owns the retry budget.
    return {**update, "replan_count": state.replan_count}


def fleet(state: WarehouseGraphState, *, client: BaseChatModel,
          tools: FleetTools | None = None) -> StateUpdate:
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
    return {**route_agent(workspace(state), client=client, tools=tools), "replan_count": state.replan_count}


def project(warehouse: WarehouseState, plan: DeliveryPlan) -> WarehouseState:
    """Hypothetical post-delivery data, NOT route validation or actual execution.

    Assume the model-approved delivery succeeds; enforce only immutable domain
    record constraints (battery range, unique occupancy, lifecycle consistency).
    The real executor alone checks every movement and can reject the proposal.
    """
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
    if state.robot_schedules and state.projected_warehouse is None:
        from .schedules import review
        return review(state, client=client, safety_tools=tools)
    if state.projected_warehouse is not None:
        return safety_agent(workspace(state), tools=tools, client=client)
    if not any(item.status in ("approved", "stale") for item in state.planned_deliveries):
        return dict(run_outcome="failed", execution_requested=False, safety=None,
                    error_message="Safety requires an unconsumed batch proposal",
                    node_activity=(*state.node_activity, NodeActivity(node="safety", status="failed",
                        message="Safety requires an unconsumed batch proposal")))
    projected = state.warehouse
    activity = state.node_activity
    checked = []
    for index, item in enumerate(state.planned_deliveries):
        if item.status not in ("approved", "stale"):
            checked.append(item)
            continue
        local = WarehouseGraphState(warehouse=projected, command="execute", execution_requested=True,
                    order_selection=item.selection, selected_robot_id=item.robot_id,
                    delivery_plan=item.delivery_plan, node_activity=activity)
        update = safety_agent(local, tools=tools, client=client)
        activity = update["node_activity"]
        if activity[-1].status == "failed":
            return update  # No retry for provider or schema failures.
        # Revision identity is a data-integrity guard, not an alternate safety decision.
        if item.delivery_plan.warehouse_revision != projected.revision:
            stale = PlannedDelivery.model_validate({**item.model_dump(), "status": "stale",
                "safety": update["safety"], "reason": "Warehouse revision changed; review a new batch"})
            return {**update, "run_outcome": "failed", "execution_requested": False,
                    "planning_outcome": "stale", "error_message": stale.reason,
                    "planned_deliveries": (*checked, stale, *state.planned_deliveries[index + 1:])}
        if not update["safety"].approved:
            # Keep the reviewed prefix, retry this Route, then rebuild the dependent
            # suffix from the resulting projection. Nothing executes in this call.
            previous_ids = tuple(record.order_id for record in checked)
            remaining = tuple(order.id for order in state.warehouse.orders
                              if order.status == OrderStatus.PENDING
                              and order.id not in (*previous_ids, item.order_id))
            return {**update, "projected_warehouse": projected,
                    "batch_revision": state.warehouse_revision,
                    "planning_queue": (*previous_ids, item.order_id, *remaining),
                    "planning_index": len(previous_ids), "planned_deliveries": tuple(checked),
                    "order_selection": item.selection, "selected_robot_id": item.robot_id,
                    "delivery_plan": item.delivery_plan, "planning_outcome": "planned"}
        checked.append(PlannedDelivery.model_validate({**item.model_dump(), "status": "approved",
                                                       "safety": update["safety"], "reason": None}))
        try:
            projected = project(projected, item.delivery_plan)
        except ValueError:
            return dict(run_outcome="failed", execution_requested=False,
                        error_message="Projected delivery violates warehouse data constraints",
                        node_activity=(*activity, NodeActivity(node="execution", status="rejected",
                            message="Projected delivery violates warehouse data constraints")))
    first = next(item for item in checked if item.status == "approved")
    return dict(planned_deliveries=tuple(checked), safety=first.safety, run_outcome="ready",
                planning_outcome="planned", node_activity=activity)


def retry_route(state: WarehouseGraphState, *, client: BaseChatModel,
                tools: RouteTools | None = None) -> StateUpdate:
    if state.safety is None or state.safety.approved or state.replan_count >= state.max_replans:
        return failure(state)
    local = WarehouseGraphState.model_validate({**state.model_dump(),
        "replan_count": state.replan_count + 1, "execution_requested": False})
    return route(local, client=client, tools=tools)


def collect(state: WarehouseGraphState) -> StateUpdate:
    """Record a feasible or unplannable order and advance the private projection."""
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
    approved = next((item for item in state.planned_deliveries if item.status == "approved"), None)
    outcome = "ready" if approved else state.run_outcome
    if outcome == "running":
        outcome = "no_work"
    return dict(projected_warehouse=None, planning_queue=(), planning_index=0,
                robot_forecasts=(),
                order_selection=approved.selection if approved else None,
                selected_robot_id=approved.robot_id if approved else None,
                delivery_plan=approved.delivery_plan if approved else None,
                safety=approved.safety if approved else None,
                planning_outcome="planned" if approved else state.planning_outcome,
                run_outcome=outcome, error_message=None if approved else state.error_message,
                execution_requested=False)


def replan(state: WarehouseGraphState) -> StateUpdate:
    return start_planning(state, replacement=True)


def failure(state: WarehouseGraphState) -> StateUpdate:
    return dict(run_outcome="failed", execution_requested=False,
                robot_forecasts=(),
                projected_warehouse=None, planning_queue=(), planning_index=0,
                error_message="Replan limit exhausted" if (state.planning_outcome == "stale"
                    and state.replan_count >= state.max_replans)
                else state.error_message or "Batch rejected")


def apply_delivery(warehouse: WarehouseState, plan: DeliveryPlan) -> DeliveryExecutionResult:
    """Execution boundary, separately testable from private projection."""
    return WarehouseSimulation(warehouse).execute_delivery(plan)


def execution(state: WarehouseGraphState) -> StateUpdate:
    if state.robot_schedules:
        from .schedules import execute
        return execute(state)
    if (state.command != "execute" or not state.execution_requested or state.run_outcome != "ready"
            or state.batch_revision != state.warehouse_revision
            or any(item.status not in ("approved", "unplannable") for item in state.planned_deliveries)
            or not any(item.status == "approved" for item in state.planned_deliveries)):
        return failure(state)
    warehouse = state.warehouse
    records = []
    stopped = False
    completed = 0
    for item in state.planned_deliveries:
        if item.status != "approved":
            records.append(item)
            continue
        if stopped:
            records.append(PlannedDelivery.model_validate({**item.model_dump(), "status": "not_executed",
                "reason": "Earlier delivery failed; dependent remainder requires a new Plan"}))
            continue
        # The domain executor revalidates immediately before its atomic transition.
        result = apply_delivery(warehouse, item.delivery_plan)
        if result.success:
            warehouse = result.final_state
            completed += 1
            records.append(PlannedDelivery.model_validate({**item.model_dump(), "status": "delivered"}))
        else:
            stopped = True
            records.append(PlannedDelivery.model_validate({**item.model_dump(), "status": "failed",
                "reason": f"Atomic delivery failed during {result.failed_stage}"}))
    # Expected domain failure is a completed command with explicit per-order results.
    # Unexpected exceptions propagate and the session keeps its prior checkpoint.
    outcome = "partial" if stopped and completed else "failed" if stopped else "delivered"
    return dict(warehouse=warehouse, planned_deliveries=tuple(records), run_outcome=outcome,
                order_selection=None, selected_robot_id=None, delivery_plan=None, safety=None,
                planning_outcome="not_planned", execution_requested=False,
                error_message="Batch stopped; review per-order results and Plan again" if stopped else None,
                node_activity=(*state.node_activity, NodeActivity(node="execution",
                    status="rejected" if stopped else "completed",
                    message=f"Completed {completed} deliveries")))
