"""Role context and trusted Fleet A* costs; no Route geometry or Safety decisions."""

from pydantic import BaseModel, ConfigDict, Field
from app.warehouse.models import Identifier, Order, OrderStatus, Robot, WarehouseState
from app.warehouse.routing import astar_path


class FleetCandidate(BaseModel):
    """Trusted shortest delivery costs for one robot at its projected input state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    robot: Robot = Field(description="Projected robot position, battery and availability before this assignment.")
    pickup_cost: int | None = Field(ge=0, description="Exact A* steps from projected position to pickup; null when unavailable or unreachable.")
    delivery_cost: int | None = Field(ge=0, description="Exact A* steps from pickup to drop-off; null when unavailable or unreachable.")
    total_cost: int | None = Field(ge=0, description="Sum of both A* leg costs; null when either leg is unavailable.")
    feasible: bool = Field(description="Both legs reachable, robot idle and empty, and projected battery covers their total cost.")
    reason: str | None = Field(description="Reason the robot is infeasible, or null for a feasible candidate.")


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
                blocked = warehouse.blocked_cells | {r.position for r in warehouse.robots if r.id != robot.id}
                pickup = astar_path(warehouse.width, warehouse.height, robot.position,
                                    order.package.pickup, warehouse.obstacles, blocked)
                delivery = astar_path(warehouse.width, warehouse.height, order.package.pickup,
                                      order.dropoff, warehouse.obstacles, blocked)
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
    """Retrieve grid and endpoint context without computing a route."""
    def grid_context(self, warehouse: WarehouseState, order_id: Identifier, robot_id: Identifier) -> dict:
        """Describe the selected robot, pickup, drop-off and occupied cells for LLM routing."""
        return {"warehouse": warehouse.model_dump(mode="json"),
                "selected_order": _order(warehouse, order_id).model_dump(mode="json"),
                "selected_robot": _robot(warehouse, robot_id).model_dump(mode="json"),
                "other_occupied_cells": [robot.position.model_dump() for robot in warehouse.robots
                                         if robot.id != robot_id]}


class SafetyTools:
    """Retrieve trusted context without making an approval decision."""
    def current_context(self, warehouse: WarehouseState, order_id: Identifier, robot_id: Identifier) -> dict:
        """Describe the supplied snapshot for the Safety LLM, without validating movement."""
        return {"warehouse": warehouse.model_dump(mode="json"),
                "selected_order": _order(warehouse, order_id).model_dump(mode="json"),
                "selected_robot": _robot(warehouse, robot_id).model_dump(mode="json")}
