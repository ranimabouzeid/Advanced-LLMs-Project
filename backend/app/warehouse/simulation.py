"""Sequential, deterministic warehouse actions. No routing or agent logic."""

from .models import Order, OrderStatus, Package, Position, Robot, RobotStatus, WarehouseState


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

    def _commit(self, **changes: object) -> None:
        """Validate one complete action; advance revision only if state changes."""
        candidate = WarehouseState.model_validate({**self.state.model_dump(), **changes})
        if candidate == self.state:
            return
        self._state = WarehouseState.model_validate({
            **candidate.model_dump(), "revision": self.state.revision + 1,
        })

    def get_order(self, order_id: str) -> Order:
        for order in self.state.orders:
            if order.id == order_id:
                return order
        raise KeyError(f"Unknown order: {order_id}")

    def pending_orders(self) -> tuple[Order, ...]:
        """Return only unassigned orders, in creation order."""
        return tuple(order for order in self.state.orders if order.status == OrderStatus.PENDING)

    def _commit_lifecycle(self, order: Order, robot: Robot) -> None:
        self._commit(
            orders=tuple(order if item.id == order.id else item for item in self.state.orders),
            robots=tuple(robot if item.id == robot.id else item for item in self.state.robots),
        )

    def assign_order(self, order_id: str, robot_id: str) -> Order:
        """Reserve one pending order for an idle, empty robot. No battery cost."""
        order, robot = self.get_order(order_id), self.get_robot(robot_id)
        if order.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be assigned")
        if robot.status != RobotStatus.IDLE or robot.carried_package_id is not None:
            raise ValueError("Assignment requires an idle robot with no carried package")
        assigned = Order.model_validate({
            **order.model_dump(), "status": OrderStatus.ASSIGNED, "assigned_robot_id": robot.id,
        })
        busy = Robot.model_validate({**robot.model_dump(), "status": RobotStatus.BUSY})
        self._commit_lifecycle(assigned, busy)
        return assigned

    def pickup_package(self, order_id: str, robot_id: str) -> Order:
        """Collect the assigned package at its pickup cell. No battery cost."""
        order, robot = self.get_order(order_id), self.get_robot(robot_id)
        if order.status != OrderStatus.ASSIGNED:
            raise ValueError("Pickup requires an assigned order that has not been picked up")
        if order.assigned_robot_id != robot.id:
            raise ValueError("Only the assigned robot may pick up this package")
        if robot.status != RobotStatus.BUSY or robot.carried_package_id is not None:
            raise ValueError("Pickup requires a busy robot with no carried package")
        if robot.position != order.package.pickup:
            raise ValueError("Robot must be at the pickup position")
        picked_up = Order.model_validate({**order.model_dump(), "status": OrderStatus.PICKED_UP})
        carrying = Robot.model_validate({**robot.model_dump(), "carried_package_id": order.package.id})
        self._commit_lifecycle(picked_up, carrying)
        return picked_up

    def deliver_package(self, order_id: str, robot_id: str) -> Order:
        """Deliver the carried package at its drop-off and free the robot."""
        order, robot = self.get_order(order_id), self.get_robot(robot_id)
        if order.status != OrderStatus.PICKED_UP:
            raise ValueError("Delivery requires a picked-up order")
        if order.assigned_robot_id != robot.id:
            raise ValueError("Only the assigned robot may deliver this package")
        if robot.status != RobotStatus.BUSY or robot.carried_package_id != order.package.id:
            raise ValueError("Robot must be busy and carry the matching package")
        if robot.position != order.dropoff:
            raise ValueError("Robot must be at the drop-off position")
        delivered = Order.model_validate({**order.model_dump(), "status": OrderStatus.DELIVERED})
        idle = Robot.model_validate({
            **robot.model_dump(), "carried_package_id": None, "status": RobotStatus.IDLE,
        })
        self._commit_lifecycle(delivered, idle)
        return delivered

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
        self._commit(orders=(*self.state.orders, order))
        return order

    def add_blocked_cell(self, cell: Position) -> None:
        """Block a free cell; repeating the request is a no-op.

        Pickup and drop-off cells may be temporarily blocked. A robot's current
        cell and permanent obstacles cannot be marked as temporary blocks.
        """
        self._commit(blocked_cells=self.state.blocked_cells | {cell})

    def remove_blocked_cell(self, cell: Position) -> None:
        """Unblock an in-bounds cell; removing a missing block is a no-op."""
        if not self.state.contains(cell):
            raise ValueError("Cell is outside the grid")
        self._commit(blocked_cells=self.state.blocked_cells - {cell})

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

        moved = Robot.model_validate({
            **robot.model_dump(), "position": destination, "battery": robot.battery - 1,
        })
        self._commit(robots=tuple(moved if item.id == robot_id else item for item in self.state.robots))
        return moved
