"""Immutable domain records and validation for the initial 10x10 warehouse."""

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer, model_validator


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DomainModel(BaseModel):
    """Reject unknown fields and protect validated records from mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Position(DomainModel):
    """An integer (x, y) cell; warehouse bounds are checked by WarehouseState."""

    x: int = Field(strict=True, description="Zero-based grid column, increasing to the right; must fit the supplied warehouse bounds.")
    y: int = Field(strict=True, description="Zero-based grid row, increasing downward; must fit the supplied warehouse bounds.")


class RobotStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    CHARGING = "charging"
    OFFLINE = "offline"


class Robot(DomainModel):
    id: Identifier
    position: Position
    battery: int = Field(default=100, ge=0, le=100, strict=True)
    status: RobotStatus = RobotStatus.IDLE
    carried_package_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_carrying_status(self) -> Self:
        if self.carried_package_id is not None and self.status != RobotStatus.BUSY:
            raise ValueError("A robot carrying a package must be busy")
        return self


class Package(DomainModel):
    """Package identity and original pickup cell; after delivery its location is the order drop-off."""

    id: Identifier
    pickup: Position


class OrderStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    DELIVERED = "delivered"


class Order(DomainModel):
    """One package, destination, and lifecycle; assignment is retained as history."""

    id: Identifier
    package: Package
    dropoff: Position = Field(description="Temporary robot service cell where this package remains after delivery; the robot must continue to later work or parking/staging.")
    status: OrderStatus = OrderStatus.PENDING
    assigned_robot_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_assignment(self) -> Self:
        if self.status == OrderStatus.PENDING and self.assigned_robot_id is not None:
            raise ValueError("A pending order cannot have an assigned robot")
        if self.status != OrderStatus.PENDING and self.assigned_robot_id is None:
            raise ValueError("A non-pending order requires an assigned robot")
        return self


class DeliveryPlan(DomainModel):
    """A proposal for a complete delivery starting with a pending order."""

    order_id: Identifier
    robot_id: Identifier
    pickup_route: tuple[Position, ...] = Field(min_length=1)
    delivery_route: tuple[Position, ...] = Field(min_length=1)
    total_steps: int = Field(ge=0, strict=True)
    warehouse_revision: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_step_count(self) -> Self:
        steps = len(self.pickup_route) - 1 + len(self.delivery_route) - 1
        if self.total_steps != steps:
            raise ValueError("Total steps must equal the sum of both route lengths minus two")
        return self


class WarehouseState(DomainModel):
    """A validated snapshot with immutable nested records and collections."""

    width: Literal[10] = 10
    height: Literal[10] = 10
    revision: int = Field(default=0, ge=0, strict=True)
    robots: tuple[Robot, ...] = Field(min_length=3, max_length=3)
    obstacles: frozenset[Position] = frozenset()
    blocked_cells: frozenset[Position] = frozenset()
    dropoff_locations: frozenset[Position] = Field(min_length=1)
    orders: tuple[Order, ...] = ()
    parking_cells: tuple[Position, ...] = Field(
        default=(Position(x=7, y=9), Position(x=8, y=9), Position(x=8, y=8)),
        description="Ordered designated idle/final staging cells, distinct from shelves, pickups and drop-offs. Allocation excludes temporarily blocked, occupied or reserved cells.")

    @field_serializer("obstacles", "blocked_cells", "dropoff_locations")
    def serialize_cells(self, cells: frozenset[Position]) -> list[dict[str, int]]:
        # Model dictionaries are unhashable; serialize cell sets as stable lists.
        return [{"x": cell.x, "y": cell.y} for cell in sorted(cells, key=lambda p: (p.x, p.y))]

    def contains(self, cell: Position) -> bool:
        return 0 <= cell.x < self.width and 0 <= cell.y < self.height

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        cells = (
            self.obstacles | self.blocked_cells | self.dropoff_locations
            | set(self.parking_cells)
            | {robot.position for robot in self.robots}
            | {order.package.pickup for order in self.orders}
            | {order.dropoff for order in self.orders}
        )
        if any(not self.contains(cell) for cell in cells):
            raise ValueError("All warehouse cells must be inside the 10x10 grid")
        if self.obstacles & self.blocked_cells:
            raise ValueError("Permanent obstacles and temporary blocked cells must be distinct")
        if self.obstacles & self.dropoff_locations:
            raise ValueError("Drop-off locations cannot be obstacles")
        if len(set(self.parking_cells)) != len(self.parking_cells):
            raise ValueError("Parking cells must be unique")
        if set(self.parking_cells) & (self.obstacles | self.dropoff_locations |
                                      {order.package.pickup for order in self.orders}):
            raise ValueError("Parking cells cannot overlap shelves, pickups, or drop-offs")

        robot_ids = [robot.id for robot in self.robots]
        positions = [robot.position for robot in self.robots]
        if len(set(robot_ids)) != len(robot_ids):
            raise ValueError("Robot identifiers must be unique")
        if len(set(positions)) != len(positions):
            raise ValueError("Robots cannot occupy the same cell")
        if set(positions) & (self.obstacles | self.blocked_cells):
            raise ValueError("Robots cannot occupy obstacles or blocked cells")

        order_ids = [order.id for order in self.orders]
        package_ids = [order.package.id for order in self.orders]
        if len(set(order_ids)) != len(order_ids):
            raise ValueError("Order identifiers must be unique")
        if len(set(package_ids)) != len(package_ids):
            raise ValueError("Each package can belong to only one order")
        for order in self.orders:
            if order.package.pickup in self.obstacles:
                raise ValueError("Package pickup cells cannot be obstacles")
            if order.dropoff not in self.dropoff_locations:
                raise ValueError("Order destination must be a registered drop-off location")
        robots_by_id = {robot.id: robot for robot in self.robots}
        active_robots: set[str] = set()
        picked_up_packages: dict[str, str] = {}
        for order in self.orders:
            if order.assigned_robot_id is not None and order.assigned_robot_id not in robots_by_id:
                raise ValueError("Assigned robot must exist in the warehouse")
            if order.status not in (OrderStatus.ASSIGNED, OrderStatus.PICKED_UP):
                continue
            robot = robots_by_id[order.assigned_robot_id]
            if robot.id in active_robots:
                raise ValueError("A robot cannot have more than one active order")
            active_robots.add(robot.id)
            if robot.status != RobotStatus.BUSY:
                raise ValueError("An assigned robot must be busy")
            if order.status == OrderStatus.ASSIGNED and robot.carried_package_id is not None:
                raise ValueError("An assigned order has not yet been picked up")
            if order.status == OrderStatus.PICKED_UP:
                if robot.carried_package_id != order.package.id:
                    raise ValueError("A picked-up order must be carried by its assigned robot")
                picked_up_packages[order.package.id] = robot.id
        for robot in self.robots:
            if robot.carried_package_id is not None:
                if picked_up_packages.get(robot.carried_package_id) != robot.id:
                    raise ValueError("A carried package must match a picked-up order for that robot")
        return self
