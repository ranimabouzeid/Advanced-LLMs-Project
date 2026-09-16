"""Separate read-only role toolsets; snapshots come from trusted calling code."""

from typing import Literal

from pydantic import Field

from app.warehouse.models import DomainModel, Identifier, Order, OrderStatus, Robot, RobotStatus, WarehouseState
from app.warehouse.routing import plan_delivery


class OrderTools:
    """Only order inspection; no fleet, route, safety, or mutation capabilities."""

    def pending_orders(self, warehouse: WarehouseState) -> tuple[Order, ...]:
        """Return eligible pending orders in creation order."""
        return tuple(order for order in warehouse.orders if order.status == OrderStatus.PENDING)

    def order_metadata(self, warehouse: WarehouseState, order_id: Identifier) -> Order:
        """Return the existing immutable record for an identifier."""
        for order in warehouse.orders:
            if order.id == order_id:
                return order
        raise KeyError("Unknown order identifier")


class FleetCandidate(DomainModel):
    """Temporary deterministic evaluation, not another warehouse record in state."""

    robot_id: Identifier
    battery: int = Field(ge=0, le=100, strict=True)
    lower_bound: int = Field(ge=0, strict=True)
    outcome: Literal["eligible", "unavailable", "unreachable", "insufficient_battery"]
    total_steps: int | None = Field(default=None, ge=0, strict=True)


class FleetTools:
    """Robot lookup and delivery feasibility only; no assignment or plan publication."""

    def robot_status(self, warehouse: WarehouseState, robot_id: Identifier) -> Robot:
        for robot in warehouse.robots:
            if robot.id == robot_id:
                return robot
        raise KeyError("Unknown robot identifier")

    def delivery_lower_bound(self, warehouse: WarehouseState, order_id: Identifier,
                             robot_id: Identifier) -> int:
        order = next((order for order in warehouse.orders if order.id == order_id), None)
        if order is None or order.status != OrderStatus.PENDING:
            raise ValueError("Fleet requires an eligible pending order")
        robot = self.robot_status(warehouse, robot_id)
        start, pickup, dropoff = robot.position, order.package.pickup, order.dropoff
        return (abs(start.x - pickup.x) + abs(start.y - pickup.y)
                + abs(pickup.x - dropoff.x) + abs(pickup.y - dropoff.y))

    def evaluate_candidate(self, warehouse: WarehouseState, order_id: Identifier,
                           robot_id: Identifier) -> FleetCandidate:
        robot = self.robot_status(warehouse, robot_id)
        lower_bound = self.delivery_lower_bound(warehouse, order_id, robot_id)
        values = dict(robot_id=robot.id, battery=robot.battery, lower_bound=lower_bound)
        if robot.status != RobotStatus.IDLE or robot.carried_package_id is not None:
            return FleetCandidate(**values, outcome="unavailable")
        # Existing A* handles both legs and treats other robots as occupied cells.
        # Do not use the lower bound as the actual movement/battery cost.
        plan = plan_delivery(warehouse, order_id, robot_id)
        if plan is None:
            return FleetCandidate(**values, outcome="unreachable")
        return FleetCandidate(**values, total_steps=plan.total_steps,
                              outcome="eligible" if robot.battery >= plan.total_steps
                              else "insufficient_battery")
