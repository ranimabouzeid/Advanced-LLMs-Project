"""Atomic empty-robot staging movement, separate from completed delivery."""

from pydantic import Field, model_validator

from .models import DomainModel, Identifier, Position, WarehouseState
from .simulation import WarehouseSimulation
from .validation import ValidationIssue, ValidationResult, validate_route


class MovementPlan(DomainModel):
    """Revision-bound empty-robot route to final parking/staging after delivery work."""
    robot_id: Identifier = Field(description="Robot leaving its final drop-off for parking/staging.")
    route: tuple[Position, ...] = Field(min_length=1, max_length=201, description="Endpoint-inclusive movement cells ending at reserved parking/staging, not a pickup or drop-off.")
    warehouse_revision: int = Field(ge=0, strict=True, description="Expected input revision after this robot's final delivery, before parking movement.")

    @property
    def total_steps(self) -> int:
        """Return the movement cost in battery points, excluding the starting cell."""
        return len(self.route) - 1


class MovementResult(DomainModel):
    """Atomic parking outcome; a rejected move exposes no partially moved snapshot."""
    success: bool
    final_state: WarehouseState | None = None
    error: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.success != (self.final_state is not None) or self.success == (self.error is not None):
            raise ValueError("Movement success requires a snapshot; failure requires an error")
        return self


def validate_parking_plan(warehouse: WarehouseState, plan: MovementPlan, *,
                          expected_target=None, reserved_cells=()) -> ValidationResult:
    """Inspect empty-robot departure without moving it; shared by Safety and execution."""
    robot = next((r for r in warehouse.robots if r.id == plan.robot_id), None)
    target = plan.route[-1]
    checked = validate_route(warehouse, plan.robot_id,
        robot.position if robot else plan.route[0], expected_target or target, plan.route)
    reasons = list(checked.reasons)

    def reject(code, message):
        reasons.append(ValidationIssue(code=code, message=message))

    if plan.warehouse_revision != warehouse.revision:
        reject("stale_plan", "Parking revision does not match the current warehouse")
    if robot is not None:
        if robot.status != "idle" or robot.carried_package_id is not None:
            reject("robot_status", "Parking requires an idle, empty robot")
        if robot.battery < plan.total_steps:
            reject("insufficient_battery", f"Parking requires {plan.total_steps} battery points; robot has {robot.battery}")
    if (target not in warehouse.parking_cells or target in warehouse.dropoff_locations
            or any(o.package.pickup == target for o in warehouse.orders)):
        reject("parking_target", "Parking must end at designated staging, never pickup or drop-off")
    if target in reserved_cells:
        reject("parking_reserved", "Parking target is reserved by another robot")
    return ValidationResult(route_valid=not reasons and not checked.conflicts,
        collision_risk=checked.collision_risk, reasons=tuple(reasons), conflicts=checked.conflicts)


def execute_parking(warehouse: WarehouseState, plan: MovementPlan) -> MovementResult:
    """Publish one revision on success; leave the delivered snapshot intact on failure."""
    try:
        robot = next(r for r in warehouse.robots if r.id == plan.robot_id)
        checked = validate_parking_plan(warehouse, plan)
        if not checked.route_valid:
            raise ValueError("Invalid parking movement")
        simulation = WarehouseSimulation(warehouse)
        for cell in plan.route[1:]:
            simulation.move_robot(robot.id, cell)
        final = WarehouseState.model_validate({**simulation.state.model_dump(), "revision": warehouse.revision + 1})
        return MovementResult(success=True, final_state=final)
    except (ValueError, KeyError, StopIteration):
        return MovementResult(success=False, error="Parking movement rejected; completed delivery preserved")
