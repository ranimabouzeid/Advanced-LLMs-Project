"""Sequential, deterministic warehouse actions. No routing or agent logic."""

from .models import Order, Package, Position, Robot, RobotStatus, WarehouseState


class WarehouseSimulation:
    """Own a snapshot and replace it only after an entire action validates.

    Invalid actions raise ValueError; unknown robot IDs raise KeyError.
    The returned state is immutable, so callers cannot bypass action validation.
    """

    def __init__(self, state: WarehouseState | None = None) -> None:
        if state is None:
            state = WarehouseState(
                robots=tuple(
                    Robot(id=f"robot-{index + 1}", position=Position(x=0, y=index))
                    for index in range(3)
                ),
                obstacles=frozenset(
                    Position(x=x, y=y) for x in (3, 6) for y in range(2, 8)
                ),
                dropoff_locations=frozenset({Position(x=9, y=0), Position(x=9, y=9)}),
            )
        # Revalidate even a supplied snapshot before accepting it.
        self._state = WarehouseState.model_validate(state.model_dump())

    @property
    def state(self) -> WarehouseState:
        return self._state

    def robot_positions(self) -> dict[str, Position]:
        """Return a detached mapping of robot IDs to immutable positions."""
        return {robot.id: robot.position for robot in self.state.robots}

    def obstacles(self) -> frozenset[Position]:
        return self.state.obstacles

    def get_robot(self, robot_id: str) -> Robot:
        for robot in self.state.robots:
            if robot.id == robot_id:
                return robot
        raise KeyError(f"Unknown robot: {robot_id}")

    def create_order(
        self, order_id: str, package_id: str, pickup: Position, dropoff: Position,
    ) -> Order:
        """Create a pending order without assigning, picking up, or delivering it."""
        order = Order(
            id=order_id, package=Package(id=package_id, pickup=pickup), dropoff=dropoff,
        )
        candidate = WarehouseState.model_validate({
            **self.state.model_dump(), "orders": (*self.state.orders, order),
        })
        self._state = candidate
        return order

    def add_blocked_cell(self, cell: Position) -> None:
        """Block a free cell; repeating the request is a no-op.

        Pickup and drop-off cells may be temporarily blocked. A robot's current
        cell and permanent obstacles cannot be marked as temporary blocks.
        """
        candidate = WarehouseState.model_validate({
            **self.state.model_dump(), "blocked_cells": self.state.blocked_cells | {cell},
        })
        self._state = candidate

    def remove_blocked_cell(self, cell: Position) -> None:
        """Unblock an in-bounds cell; removing a missing block is a no-op."""
        if not self.state.contains(cell):
            raise ValueError("Cell is outside the grid")
        candidate = WarehouseState.model_validate({
            **self.state.model_dump(), "blocked_cells": self.state.blocked_cells - {cell},
        })
        self._state = candidate

    def move_robot(self, robot_id: str, destination: Position) -> Robot:
        """Move one orthogonal step, consuming one battery percentage point.

        IDLE and BUSY robots can move. Status remains unchanged because this
        synchronous primitive does not assign work or model elapsed travel time.
        """
        robot = self.get_robot(robot_id)
        if not self.state.contains(destination):
            raise ValueError("Destination is outside the grid")
        distance = abs(destination.x - robot.position.x) + abs(destination.y - robot.position.y)
        if distance != 1:
            raise ValueError("Destination must be an orthogonally adjacent cell")
        if destination in self.state.obstacles:
            raise ValueError("Destination is an obstacle")
        if destination in self.state.blocked_cells:
            raise ValueError("Destination is blocked")
        if destination in self.robot_positions().values():
            raise ValueError("Destination is occupied by another robot")
        if robot.status not in (RobotStatus.IDLE, RobotStatus.BUSY):
            raise ValueError("Robot status does not permit movement")
        if robot.battery < 1:
            raise ValueError("Robot battery is depleted")

        moved = Robot(
            id=robot.id, position=destination, battery=robot.battery - 1, status=robot.status,
        )
        candidate = WarehouseState.model_validate({
            **self.state.model_dump(),
            "robots": tuple(moved if item.id == robot_id else item for item in self.state.robots),
        })
        self._state = candidate
        return moved
