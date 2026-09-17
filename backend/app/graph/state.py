"""Serializable workflow records with one authoritative warehouse snapshot.

These schemas do not execute commands, validate route safety, or apply updates.
Future tools receive warehouse from trusted runtime code, never LLM arguments.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.warehouse.models import DeliveryPlan, Identifier, OrderStatus, Position, Robot, WarehouseState
from app.warehouse.movement import MovementPlan


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Command = Literal["plan", "execute"]
PlanningOutcome = Literal["not_planned", "planned", "stale", "unreachable", "failed"]
RunOutcome = Literal["idle", "running", "ready", "delivered", "partial", "no_work", "no_robot", "unreachable", "failed"]


class WorkflowModel(BaseModel):
    """Immutable records, rejecting misspelled or undeclared fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OrderSelection(WorkflowModel):
    """Structured selection; the enclosing state checks pending-order eligibility."""

    order_id: Identifier
    explanation: ShortText


class FleetSelection(WorkflowModel):
    """The model chooses an available robot, or explicitly reports no suitable one."""

    robot_id: Identifier | None
    explanation: ShortText


class LLMRoutePlan(WorkflowModel):
    """Model-authored geometry; no planner fills in or repairs these coordinates."""

    robot_id: Identifier
    order_id: Identifier
    outcome: Literal["planned", "unreachable"] = "planned"
    route_to_pickup: tuple[Position, ...] | None = Field(default=None, min_length=1, max_length=201)
    route_to_dropoff: tuple[Position, ...] | None = Field(default=None, min_length=1, max_length=201)
    explanation: ShortText

    @model_validator(mode="after")
    def routes_match_outcome(self) -> Self:
        if self.outcome == "planned" and (self.route_to_pickup is None or self.route_to_dropoff is None):
            raise ValueError("A planned route requires both nonempty legs")
        if self.outcome == "unreachable" and (self.route_to_pickup is not None or self.route_to_dropoff is not None):
            raise ValueError("An unreachable result cannot include a route")
        return self


class SafetyDecision(WorkflowModel):
    """LLM approval is a decision, not a deterministic validation certificate."""

    approved: bool = Field(strict=True)
    conflicts: tuple[ShortText, ...] = Field(default=(), max_length=100)
    explanation: ShortText

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        if self.approved and self.conflicts:
            raise ValueError("Approval cannot also report conflicts")
        return self


class NodeActivity(WorkflowModel):
    """One actual activity; tuple position supplies its order in the run."""

    node: Literal["order", "fleet", "route", "safety", "execution"]
    status: Literal["completed", "rejected", "failed"]
    message: ShortText | None = None


class PlannedDelivery(WorkflowModel):
    """One ordered proposal/result; no parallel assignment or status arrays."""

    order_id: Identifier
    selection: OrderSelection | None = None
    robot_id: Identifier | None = None
    delivery_plan: DeliveryPlan | None = None
    safety: SafetyDecision | None = None
    status: Literal["approved", "unplannable", "stale", "delivered", "failed", "not_executed"]
    reason: ShortText | None = None

    @model_validator(mode="after")
    def consistent_assignment(self) -> Self:
        if self.selection is not None and self.selection.order_id != self.order_id:
            raise ValueError("Selection must match the batch order")
        if self.delivery_plan is not None:
            if (self.delivery_plan.order_id != self.order_id
                    or self.delivery_plan.robot_id != self.robot_id):
                raise ValueError("Batch assignment must match its plan")
        if self.status in ("approved", "delivered"):
            if (self.delivery_plan is None or self.selection is None or self.safety is None
                    or not self.safety.approved):
                raise ValueError("Approved/delivered records require a selected, checked plan")
        elif not self.reason:
            raise ValueError("An unsuccessful or stale record requires a reason")
        return self


class RobotForecast(WorkflowModel):
    """Independent assignment timeline; endpoints need not be simultaneous occupancy."""
    robot: Robot
    order_ids: tuple[Identifier, ...] = ()


class LLMMovementPlan(WorkflowModel):
    robot_id: Identifier
    route: tuple[Position, ...] = Field(min_length=1, max_length=201)
    explanation: ShortText


class PlannedParking(WorkflowModel):
    plan: MovementPlan
    safety: SafetyDecision | None
    status: Literal["approved", "stale", "completed", "failed", "not_executed"] = "approved"
    reason: ShortText | None = None

    @model_validator(mode="after")
    def approval(self):
        if self.status in ("approved", "completed") and (self.safety is None or not self.safety.approved):
            raise ValueError("Parking requires model approval")
        return self


class RobotSchedule(WorkflowModel):
    robot_id: Identifier
    order_ids: tuple[Identifier, ...] = ()
    parking: PlannedParking
    projected_robot: Robot

    @model_validator(mode="after")
    def identity(self):
        if self.robot_id != self.parking.plan.robot_id or self.projected_robot.id != self.robot_id:
            raise ValueError("Schedule robot identifiers must match")
        if self.projected_robot.position != self.parking.plan.route[-1]:
            raise ValueError("Final endpoint must match parking")
        if len(set(self.order_ids)) != len(self.order_ids):
            raise ValueError("Schedule cannot repeat orders")
        return self


class WarehouseGraphState(WorkflowModel):
    """Typed shared workflow data, without runtime clients or hidden decisions.

    warehouse.revision is the only current revision. A plan's revision describes
    its historical input snapshot. None safety means unchecked, never approval.
    Safety stores the model's explicit decision. The execution simulator retains
    its own deterministic data-integrity and atomic-movement checks.
    """

    warehouse: WarehouseState
    command: Command
    execution_requested: bool = Field(default=False, strict=True)
    order_selection: OrderSelection | None = None
    selected_robot_id: Identifier | None = None
    delivery_plan: DeliveryPlan | None = None
    planning_outcome: PlanningOutcome = "not_planned"
    safety: SafetyDecision | None = None
    replan_count: int = Field(default=0, ge=0, strict=True)
    max_replans: int = Field(default=3, ge=0, le=3, strict=True)
    run_outcome: RunOutcome = "idle"
    error_message: ShortText | None = None
    node_activity: tuple[NodeActivity, ...] = Field(default_factory=tuple)
    planned_deliveries: tuple[PlannedDelivery, ...] = ()
    batch_revision: int | None = Field(default=None, ge=0, strict=True)
    # Scratch channels exist only while planning. warehouse remains authoritative.
    projected_warehouse: WarehouseState | None = None
    planning_queue: tuple[Identifier, ...] = ()
    planning_index: int = Field(default=0, ge=0, strict=True)
    robot_forecasts: tuple[RobotForecast, ...] = ()
    robot_schedules: tuple[RobotSchedule, ...] = ()

    @property
    def warehouse_revision(self) -> int:
        """Read-only nonnegative revision; deliberately not a serialized field."""
        return self.warehouse.revision

    @model_validator(mode="after")
    def validate_references_and_limits(self) -> Self:
        if self.robot_forecasts:
            ids = [f.robot.id for f in self.robot_forecasts]
            if len(ids) != len(set(ids)) or set(ids) != {r.id for r in self.warehouse.robots}:
                raise ValueError("Forecasts must contain every robot exactly once")
            orders = [oid for f in self.robot_forecasts for oid in f.order_ids]
            if len(orders) != len(set(orders)) or not set(orders) <= {o.id for o in self.warehouse.orders}:
                raise ValueError("Forecast orders must exist and be assigned once")
            if any(not self.warehouse.contains(f.robot.position) for f in self.robot_forecasts):
                raise ValueError("Forecast endpoints must be inside the warehouse")
        schedule_ids = [s.robot_id for s in self.robot_schedules]
        if len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("One schedule per robot")
        destinations = [s.parking.plan.route[-1] for s in self.robot_schedules]
        if len(set(destinations)) != len(destinations):
            raise ValueError("Robots cannot reserve the same parking cell")
        for schedule in self.robot_schedules:
            for order_id in schedule.order_ids:
                if not any(d.order_id == order_id and d.robot_id == schedule.robot_id for d in self.planned_deliveries):
                    raise ValueError("Schedule must reference its robot's batch assignments")
            if schedule.parking.plan.route[-1] not in self.warehouse.parking_cells:
                raise ValueError("Schedule destination must be a designated parking cell")
        if self.robot_schedules:
            scheduled = [oid for s in self.robot_schedules for oid in s.order_ids]
            expected = [d.order_id for d in self.planned_deliveries if d.status != "unplannable"]
            if len(set(scheduled)) != len(scheduled) or set(scheduled) != set(expected):
                raise ValueError("Schedules must cover actionable batch assignments exactly once")
        identifiers = [item.order_id for item in self.planned_deliveries]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("A batch cannot contain duplicate orders")
        if self.planning_index > len(self.planning_queue):
            raise ValueError("Planning cursor exceeds queue")
        if len(set(self.planning_queue)) != len(self.planning_queue):
            raise ValueError("Planning queue cannot repeat an order")
        for item in self.planned_deliveries:
            if not any(order.id == item.order_id for order in self.warehouse.orders):
                raise ValueError("Batch order must exist")
            if item.robot_id is not None and not any(r.id == item.robot_id for r in self.warehouse.robots):
                raise ValueError("Batch robot must exist")
        if self.replan_count > self.max_replans:
            raise ValueError("Replan count cannot exceed max_replans")
        if self.execution_requested and self.command != "execute":
            raise ValueError("Execution intent requires an execute command")
        if self.order_selection is not None:
            if not any(order.id == self.order_selection.order_id and order.status == OrderStatus.PENDING
                       for order in self.warehouse.orders):
                raise ValueError("Selected order must be an eligible pending order")
        if self.selected_robot_id is not None:
            if not any(robot.id == self.selected_robot_id for robot in self.warehouse.robots):
                raise ValueError("Selected robot must exist in the warehouse")
        return self
