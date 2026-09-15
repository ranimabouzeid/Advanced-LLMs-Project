"""Pure route and complete-delivery validation for sequential warehouse movement."""

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import model_validator

from .models import DeliveryPlan, DomainModel, OrderStatus, Position, RobotStatus, WarehouseState


class ValidationIssue(DomainModel):
    code: str
    message: str
    cell: Position | None = None
    leg: Literal["pickup", "delivery"] | None = None


class RobotConflict(DomainModel):
    robot_id: str
    cell: Position
    leg: Literal["pickup", "delivery"] | None = None


class ValidationResult(DomainModel):
    route_valid: bool
    collision_risk: bool
    reasons: tuple[ValidationIssue, ...] = ()
    conflicts: tuple[RobotConflict, ...] = ()

    @model_validator(mode="after")
    def validate_flags(self) -> Self:
        if self.collision_risk != bool(self.conflicts):
            raise ValueError("Collision risk must reflect detected robot conflicts")
        if self.route_valid != (not self.reasons and not self.conflicts):
            raise ValueError("Route validity must reflect the validation findings")
        return self


def _result(reasons: list[ValidationIssue], conflicts: list[RobotConflict]) -> ValidationResult:
    return ValidationResult(route_valid=not reasons and not conflicts,
                            collision_risk=bool(conflicts),
                            reasons=tuple(reasons), conflicts=tuple(conflicts))


def validate_route(
    state: WarehouseState,
    robot_id: str,
    expected_start: Position,
    expected_goal: Position,
    route: Sequence[Position],
    *,
    leg: Literal["pickup", "delivery"] | None = None,
) -> ValidationResult:
    """Check geometry, occupied cells and movement status without mutating state.

    Expected endpoints are explicit: a future delivery leg starts at pickup,
    not at the robot's present position. Battery for both legs is checked by
    validate_delivery_plan, not independently per leg.
    """
    reasons: list[ValidationIssue] = []
    conflicts: list[RobotConflict] = []

    def reject(code: str, message: str, cell: Position | None = None) -> None:
        reasons.append(ValidationIssue(code=code, message=message, cell=cell, leg=leg))

    robot = next((item for item in state.robots if item.id == robot_id), None)
    if robot is None:
        reject("unknown_robot", f"Unknown robot: {robot_id}")
    elif robot.status not in (RobotStatus.IDLE, RobotStatus.BUSY):
        reject("robot_status", "Robot status does not permit movement")
    if not state.contains(expected_start) or not state.contains(expected_goal):
        reject("endpoint_bounds", "Expected endpoints must be inside the grid")
    if not route:
        reject("empty_route", "Route must contain at least one coordinate")
        return _result(reasons, conflicts)
    if route[0] != expected_start:
        reject("wrong_start", "Route does not start at the expected position", route[0])
    if route[-1] != expected_goal:
        reject("wrong_goal", "Route does not end at the expected position", route[-1])
    occupied = {item.position: item.id for item in state.robots if item.id != robot_id}
    seen_conflicts: set[Position] = set()
    for index, cell in enumerate(route):
        if not state.contains(cell):
            reject("out_of_bounds", "Route cell is outside the grid", cell)
        if cell in state.obstacles:
            reject("obstacle", "Route cell is a permanent obstacle", cell)
        if cell in state.blocked_cells:
            reject("blocked", "Route cell is temporarily blocked", cell)
        if cell in occupied and cell not in seen_conflicts:
            conflicts.append(RobotConflict(robot_id=occupied[cell], cell=cell, leg=leg))
            seen_conflicts.add(cell)
        if index:
            previous = route[index - 1]
            if abs(cell.x - previous.x) + abs(cell.y - previous.y) != 1:
                reject("non_adjacent", "Consecutive cells must be orthogonally adjacent", cell)
    return _result(reasons, conflicts)


def validate_delivery_plan(state: WarehouseState, plan: DeliveryPlan) -> ValidationResult:
    """Validate a complete proposal before assignment; approval is snapshot-specific.

    Derive movement cost from both routes rather than trusting total_steps.
    Assignment or any other real state change invalidates this proposal's revision.
    This does not execute, resume, or roll back a delivery.
    """
    reasons: list[ValidationIssue] = []
    conflicts: list[RobotConflict] = []

    def reject(code: str, message: str) -> None:
        reasons.append(ValidationIssue(code=code, message=message))

    if plan.warehouse_revision != state.revision:
        reject("stale_plan", "Plan revision does not match the current warehouse")
    order = next((item for item in state.orders if item.id == plan.order_id), None)
    robot = next((item for item in state.robots if item.id == plan.robot_id), None)
    if order is None:
        reject("unknown_order", f"Unknown order: {plan.order_id}")
    if robot is None:
        reject("unknown_robot", f"Unknown robot: {plan.robot_id}")
    if order is None or robot is None:
        return _result(reasons, conflicts)
    if order.status != OrderStatus.PENDING:
        reject("order_status", "Complete delivery requires a pending order")
    if robot.status != RobotStatus.IDLE or robot.carried_package_id is not None:
        reject("robot_status", "Complete delivery requires an idle, empty robot")

    for leg, start, goal, route in (
        ("pickup", robot.position, order.package.pickup, plan.pickup_route),
        ("delivery", order.package.pickup, order.dropoff, plan.delivery_route),
    ):
        result = validate_route(state, robot.id, start, goal, route, leg=leg)
        reasons.extend(result.reasons)
        conflicts.extend(result.conflicts)
    actual_steps = max(0, len(plan.pickup_route) - 1) + max(0, len(plan.delivery_route) - 1)
    if plan.total_steps != actual_steps:
        reject("step_count", "Plan total does not match its route lengths")
    if robot.battery < actual_steps:
        reject("insufficient_battery", f"Delivery requires {actual_steps} battery points; robot has {robot.battery}")
    return _result(reasons, conflicts)
