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

    x: int = Field(strict=True)
    y: int = Field(strict=True)


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


class Package(DomainModel):
    """A package awaiting pickup at a walkable access cell."""

    id: Identifier
    pickup: Position


class OrderStatus(str, Enum):
    PENDING = "pending"


class Order(DomainModel):
    """One package and its requested destination; fulfillment is a later phase."""

    id: Identifier
    package: Package
    dropoff: Position
    status: OrderStatus = OrderStatus.PENDING


class WarehouseState(DomainModel):
    """A validated snapshot with immutable nested records and collections."""

    width: Literal[10] = 10
    height: Literal[10] = 10
    robots: tuple[Robot, ...] = Field(min_length=3, max_length=3)
    obstacles: frozenset[Position] = frozenset()
    blocked_cells: frozenset[Position] = frozenset()
    dropoff_locations: frozenset[Position] = Field(min_length=1)
    orders: tuple[Order, ...] = ()

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
        return self
