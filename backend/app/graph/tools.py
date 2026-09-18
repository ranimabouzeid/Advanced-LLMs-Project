"""Trusted role context, Fleet costs, Route A* planning and hard Safety findings."""

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.warehouse.models import DeliveryPlan, Identifier, Order, OrderStatus, Robot, WarehouseState
from app.warehouse.routing import astar_path
from app.warehouse.validation import validate_delivery_plan
from app.warehouse.movement import MovementPlan, validate_parking_plan


class FleetCandidate(BaseModel):
    """Trusted shortest delivery costs for one robot at its projected input state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    robot: Robot = Field(description="Projected robot position, battery and availability before this assignment.")
    pickup_cost: int | None = Field(ge=0, strict=True, description="Exact A* steps from projected position to pickup; null when unavailable or unreachable.")
    delivery_cost: int | None = Field(ge=0, strict=True, description="Exact A* steps from pickup to drop-off; null when unavailable or unreachable.")
    total_cost: int | None = Field(ge=0, strict=True, description="Sum of both A* leg costs; null when either leg is unavailable.")
    feasible: bool = Field(strict=True, description="Both legs reachable, robot idle and empty, and projected battery covers their total cost.")
    reason: str | None = Field(description="Reason the robot is infeasible, or null for a feasible candidate.")

    @model_validator(mode="after")
    def consistent_costs(self):
        expected = (self.pickup_cost + self.delivery_cost
                    if self.pickup_cost is not None and self.delivery_cost is not None else None)
        if self.total_cost != expected:
            raise ValueError("Fleet total must equal both reachable leg costs")
        if self.feasible:
            if (self.total_cost is None or self.robot.status != "idle"
                    or self.robot.carried_package_id is not None
                    or self.robot.battery < self.total_cost or self.reason is not None):
                raise ValueError("Feasible Fleet candidate requires availability, reachable legs and battery")
        elif not self.reason:
            raise ValueError("Infeasible Fleet candidate requires a reason")
        return self


class OrderTools:
    """Retrieve pending order data without choosing an assignment."""
    def pending_orders(self, warehouse: WarehouseState) -> tuple[Order, ...]:
        """Return pending orders from the supplied committed or projected snapshot."""
        return tuple(order for order in warehouse.orders if order.status == OrderStatus.PENDING)

    def order_metadata(self, warehouse: WarehouseState, order_id: Identifier) -> Order:
        """Retrieve the trusted order record for a supplied identifier."""
        return _order(warehouse, order_id)


def _order(warehouse: WarehouseState, order_id: Identifier) -> Order:
    """Resolve an order in the supplied snapshot or reject an unknown identifier."""
    for order in warehouse.orders:
        if order.id == order_id:
            return order
    raise KeyError("Unknown order identifier")


def _robot(warehouse: WarehouseState, robot_id: Identifier) -> Robot:
    """Resolve a robot in the supplied snapshot or reject an unknown identifier."""
    for robot in warehouse.robots:
        if robot.id == robot_id:
            return robot
    raise KeyError("Unknown robot identifier")


def routing_path(warehouse, robot_id, start, target, avoid_cells=()):
    """Use identical A* constraints for Fleet costs and Route geometry.

    Other robots occupy their supplied snapshot positions. Only trusted callers
    may add avoid cells; model text never supplies paths or routing constraints.
    """
    if any(not warehouse.contains(cell) for cell in avoid_cells):
        raise ValueError("Avoid cells must be inside the grid")
    blocked = warehouse.blocked_cells | set(avoid_cells) | {
        robot.position for robot in warehouse.robots if robot.id != robot_id}
    return astar_path(warehouse.width, warehouse.height, start, target, warehouse.obstacles, blocked)


class FleetTools:
    """Compute trusted Fleet costs without supplying routes to the Route agent."""
    def robot_records(self, warehouse: WarehouseState) -> tuple[Robot, ...]:
        """Return every robot; Fleet overlays independent forecasts before its fresh decision."""
        return warehouse.robots

    def order_record(self, warehouse: WarehouseState, order_id: Identifier) -> Order:
        """Retrieve this assignment without selecting or reserving a robot."""
        return _order(warehouse, order_id)

    def candidate_records(self, warehouse: WarehouseState, order_id: Identifier,
                          robots: tuple[Robot, ...]) -> tuple[FleetCandidate, ...]:
        """Recompute both A* legs for every eligible robot; discard path coordinates."""
        order = _order(warehouse, order_id)
        candidates = []
        for robot in robots:
            pickup_cost = delivery_cost = total_cost = None
            reason = None
            if order.status != OrderStatus.PENDING:
                reason = "Order is not pending"
            elif robot.status != "idle" or robot.carried_package_id is not None:
                reason = "Robot is unavailable"
            else:
                # Match Route's independent assignment-preview view: only this
                # robot advances along its active chain. Other forecast tails
                # are not simultaneous occupancy (several may share a drop-off).
                # Finalization resolves physical schedule occupancy separately.
                pickup = routing_path(warehouse, robot.id, robot.position, order.package.pickup)
                delivery = routing_path(warehouse, robot.id, order.package.pickup, order.dropoff)
                pickup_cost = len(pickup) - 1 if pickup is not None else None
                delivery_cost = len(delivery) - 1 if delivery is not None else None
                if pickup_cost is None or delivery_cost is None:
                    reason = "Pickup or drop-off is unreachable"
                else:
                    total_cost = pickup_cost + delivery_cost
                    if robot.battery < total_cost:
                        reason = "Insufficient projected battery"
            candidates.append(FleetCandidate(robot=robot, pickup_cost=pickup_cost,
                delivery_cost=delivery_cost, total_cost=total_cost, feasible=reason is None, reason=reason))
        return tuple(candidates)


class RouteTools:
    """Generate exact shortest paths for trusted assignments with deterministic A*."""

    def plan_delivery(self, warehouse, order_id, robot_id, avoid_cells=()):
        """Generate shortest endpoint-inclusive legs without executing movement."""
        order, robot = _order(warehouse, order_id), _robot(warehouse, robot_id)
        pickup = routing_path(warehouse, robot_id, robot.position, order.package.pickup, avoid_cells)
        delivery = routing_path(warehouse, robot_id, order.package.pickup, order.dropoff, avoid_cells)
        if pickup is None or delivery is None:
            return None
        return DeliveryPlan(robot_id=robot_id, order_id=order_id, pickup_route=pickup,
            delivery_route=delivery, total_steps=len(pickup) + len(delivery) - 2,
            warehouse_revision=warehouse.revision)

    def plan_parking(self, warehouse, robot_id, target, avoid_cells=()):
        """Generate shortest final departure to the trusted reserved target."""
        path = routing_path(warehouse, robot_id, _robot(warehouse, robot_id).position, target, avoid_cells)
        return None if path is None else MovementPlan(robot_id=robot_id, route=path, warehouse_revision=warehouse.revision)



class SafetyTools:
    """Inspect hard constraints without executing movement; LLM may add rejection."""

    def inspect_delivery(self, warehouse, plan):
        return validate_delivery_plan(warehouse, plan)

    def inspect_parking(self, warehouse, plan, *, expected_target=None, reserved_cells=()):
        return validate_parking_plan(warehouse, plan, expected_target=expected_target,
                                     reserved_cells=reserved_cells)

    def current_context(self, warehouse: WarehouseState, order_id: Identifier, robot_id: Identifier) -> dict:
        """Describe the supplied snapshot for the Safety LLM, without validating movement."""
        return {"warehouse": warehouse.model_dump(mode="json"),
                "selected_order": _order(warehouse, order_id).model_dump(mode="json"),
                "selected_robot": _robot(warehouse, robot_id).model_dump(mode="json")}
