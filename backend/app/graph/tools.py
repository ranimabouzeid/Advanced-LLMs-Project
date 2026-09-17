"""Role-specific retrieval only. No routing, ranking, approval, or mutation."""

from app.warehouse.models import Identifier, Order, OrderStatus, Robot, WarehouseState


class OrderTools:
    def pending_orders(self, warehouse: WarehouseState) -> tuple[Order, ...]:
        return tuple(order for order in warehouse.orders if order.status == OrderStatus.PENDING)

    def order_metadata(self, warehouse: WarehouseState, order_id: Identifier) -> Order:
        return _order(warehouse, order_id)


def _order(warehouse: WarehouseState, order_id: Identifier) -> Order:
    for order in warehouse.orders:
        if order.id == order_id:
            return order
    raise KeyError("Unknown order identifier")


def _robot(warehouse: WarehouseState, robot_id: Identifier) -> Robot:
    for robot in warehouse.robots:
        if robot.id == robot_id:
            return robot
    raise KeyError("Unknown robot identifier")


class FleetTools:
    def robot_records(self, warehouse: WarehouseState) -> tuple[Robot, ...]:
        return warehouse.robots

    def order_record(self, warehouse: WarehouseState, order_id: Identifier) -> Order:
        return _order(warehouse, order_id)


class RouteTools:
    def grid_context(self, warehouse: WarehouseState, order_id: Identifier, robot_id: Identifier) -> dict:
        return {"warehouse": warehouse.model_dump(mode="json"),
                "selected_order": _order(warehouse, order_id).model_dump(mode="json"),
                "selected_robot": _robot(warehouse, robot_id).model_dump(mode="json"),
                "other_occupied_cells": [robot.position.model_dump() for robot in warehouse.robots
                                         if robot.id != robot_id]}


class SafetyTools:
    def current_context(self, warehouse: WarehouseState, order_id: Identifier, robot_id: Identifier) -> dict:
        return {"warehouse": warehouse.model_dump(mode="json"),
                "selected_order": _order(warehouse, order_id).model_dump(mode="json"),
                "selected_robot": _robot(warehouse, robot_id).model_dump(mode="json")}
