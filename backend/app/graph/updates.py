"""Pure, validated partial updates for future sequential workflow nodes.

Each public helper owns a narrow set of outputs and explicit invalidations.
No helper mutates state, runs an agent, or commits movement.
"""

from typing import TypedDict

from app.warehouse.models import DeliveryPlan, OrderStatus, WarehouseState
from app.warehouse.validation import ValidationResult, validate_delivery_plan

from .state import OrderSelection, PlanningOutcome, RunOutcome, WarehouseGraphState


class StateUpdate(TypedDict, total=False):
    warehouse: WarehouseState
    order_selection: OrderSelection | None
    selected_robot_id: str | None
    delivery_plan: DeliveryPlan | None
    planning_outcome: PlanningOutcome
    safety: ValidationResult | None
    replan_count: int
    execution_requested: bool
    run_outcome: RunOutcome
    error_message: str | None


def _validated(state: WarehouseGraphState, update: StateUpdate) -> StateUpdate:
    # model_copy(update=...) bypasses validation. Validate the merged candidate,
    # then return only the explicitly owned/invalidated channels, including None.
    candidate = WarehouseGraphState.model_validate({**state.model_dump(), **update})
    return {key: getattr(candidate, key) for key in update}


def _clear_proposal() -> StateUpdate:
    return dict(delivery_plan=None, safety=None, planning_outcome="not_planned",
                error_message=None, execution_requested=False, run_outcome="running",
                replan_count=0)


def select_order(state: WarehouseGraphState, selection: OrderSelection) -> StateUpdate:
    """Order owns selection and invalidates all downstream proposal state."""
    return _validated(state, {**_clear_proposal(), "order_selection": selection,
                              "selected_robot_id": None})


def select_robot(state: WarehouseGraphState, robot_id: str) -> StateUpdate:
    """Fleet owns the robot choice; preserve the selected order."""
    if state.order_selection is None:
        raise ValueError("Select an order before selecting a robot")
    return _validated(state, {**_clear_proposal(), "selected_robot_id": robot_id})


def replace_warehouse(state: WarehouseGraphState, warehouse: WarehouseState) -> StateUpdate:
    """Publish one domain action's snapshot, whose revision already advanced.

    Domain actions own the increment. Never increment it a second time here.
    Identical snapshots are no-ops; a changed snapshot must advance exactly once.
    """
    warehouse = WarehouseState.model_validate(warehouse.model_dump())
    if warehouse == state.warehouse:
        return {}
    if warehouse.revision != state.warehouse_revision + 1:
        raise ValueError("A warehouse mutation must advance revision exactly once")
    update: StateUpdate = dict(warehouse=warehouse, safety=None, execution_requested=False,
                               error_message=None, run_outcome="idle",
                               planning_outcome="stale" if state.delivery_plan else "not_planned")
    if state.order_selection is not None and not any(
        order.id == state.order_selection.order_id and order.status == OrderStatus.PENDING
        for order in warehouse.orders
    ):
        update.update(order_selection=None, selected_robot_id=None, delivery_plan=None,
                      planning_outcome="not_planned")
    elif state.selected_robot_id is not None and not any(
        robot.id == state.selected_robot_id for robot in warehouse.robots
    ):
        update.update(selected_robot_id=None, delivery_plan=None, planning_outcome="not_planned")
    return _validated(state, update)


def _plan_update(state: WarehouseGraphState, plan: DeliveryPlan, count: int) -> StateUpdate:
    plan = DeliveryPlan.model_validate(plan.model_dump())
    if (state.order_selection is None or plan.order_id != state.order_selection.order_id
            or plan.robot_id != state.selected_robot_id):
        raise ValueError("Plan must match the selected order and robot")
    if plan.warehouse_revision != state.warehouse_revision:
        raise ValueError("New plan must use the current warehouse revision")
    return _validated(state, dict(delivery_plan=plan, planning_outcome="planned",
                                 safety=None, replan_count=count, error_message=None,
                                 execution_requested=False, run_outcome="running"))


def fresh_plan(state: WarehouseGraphState, plan: DeliveryPlan) -> StateUpdate:
    """Route publishes a fresh proposal and starts a new retry budget."""
    return _plan_update(state, plan, 0)


def replan(state: WarehouseGraphState, plan: DeliveryPlan) -> StateUpdate:
    """Route publishes one retry, incrementing once without resetting its budget.

    Choosing actionable feedback and handling unreachable attempts belongs to
    future orchestration. This helper only publishes a supplied retry proposal.
    """
    if state.delivery_plan is None:
        raise ValueError("A retry requires an existing proposal")
    if state.replan_count >= state.max_replans:
        raise ValueError("Replan limit exhausted")
    return _plan_update(state, plan, state.replan_count + 1)


def record_safety(state: WarehouseGraphState, result: ValidationResult) -> StateUpdate:
    """Safety publishes findings only if they match this snapshot and proposal."""
    if state.delivery_plan is None:
        raise ValueError("Safety requires a proposal")
    expected = validate_delivery_plan(state.warehouse, state.delivery_plan)
    if result != expected:
        raise ValueError("Safety result does not match current deterministic validation")
    return _validated(state, dict(safety=expected, run_outcome="ready" if expected.route_valid else "failed",
                                 error_message=None if expected.route_valid else "Delivery plan failed validation"))


def is_plan_approved(state: WarehouseGraphState) -> bool:
    """Derived approval, not execution permission or another stored safety flag.

    Future execution must additionally require intent and revalidate immediately
    before committing. Runtime validation prevents forged or mismatched approval.
    """
    plan = state.delivery_plan
    return bool(plan is not None and state.order_selection is not None
                and plan.order_id == state.order_selection.order_id
                and plan.robot_id == state.selected_robot_id
                and plan.warehouse_revision == state.warehouse_revision
                and state.safety is not None and state.safety.route_valid
                and not state.safety.collision_risk
                and validate_delivery_plan(state.warehouse, plan) == state.safety)
