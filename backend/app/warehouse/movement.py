"""Atomic empty-robot staging movement, separate from completed delivery."""

from pydantic import Field, model_validator

from .models import DomainModel, Identifier, Position, WarehouseState
from .simulation import WarehouseSimulation
from .validation import validate_route


class MovementPlan(DomainModel):
    robot_id: Identifier
    route: tuple[Position, ...] = Field(min_length=1, max_length=201)
    warehouse_revision: int = Field(ge=0, strict=True)

    @property
    def total_steps(self) -> int:
        return len(self.route) - 1


class MovementResult(DomainModel):
    success: bool
    final_state: WarehouseState | None = None
    error: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.success != (self.final_state is not None) or self.success == (self.error is not None):
            raise ValueError("Movement success requires a snapshot; failure requires an error")
        return self


def execute_parking(warehouse: WarehouseState, plan: MovementPlan) -> MovementResult:
    """Publish one revision on success; leave the delivered snapshot intact on failure."""
    try:
        robot = next(r for r in warehouse.robots if r.id == plan.robot_id)
        target = plan.route[-1]
        if (plan.warehouse_revision != warehouse.revision or robot.status != "idle"
                or robot.carried_package_id is not None or robot.battery < plan.total_steps
                or target not in warehouse.parking_cells or target in warehouse.blocked_cells
                or target in warehouse.dropoff_locations
                or any(o.package.pickup == target for o in warehouse.orders)):
            raise ValueError("Invalid parking preconditions")
        checked = validate_route(warehouse, robot.id, robot.position, target, plan.route)
        if not checked.route_valid:
            raise ValueError("Invalid parking movement")
        simulation = WarehouseSimulation(warehouse)
        for cell in plan.route[1:]:
            simulation.move_robot(robot.id, cell)
        final = WarehouseState.model_validate({**simulation.state.model_dump(), "revision": warehouse.revision + 1})
        return MovementResult(success=True, final_state=final)
    except (ValueError, KeyError, StopIteration):
        return MovementResult(success=False, error="Parking movement rejected; completed delivery preserved")
