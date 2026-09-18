"""Serializable workflow records with one authoritative warehouse snapshot.

These schemas do not execute commands, validate route safety, or apply updates.
Tools receive committed or projected warehouse data from trusted runtime code.
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
    """One pending order chosen by the Order LLM for this batch iteration."""

    order_id: Identifier = Field(description="ID of one supplied eligible pending order; never invent an ID.")
    explanation: ShortText = Field(description="Brief reason for selecting this order now.")


class FleetSelection(WorkflowModel):
    """A fresh Fleet LLM choice among feasible candidates with minimum total A* cost."""

    robot_id: Identifier | None = Field(description="ID of a supplied feasible minimum-total-cost robot; null only if none is feasible. Use exact A* costs, never estimated distances. Previous assignments create no preference.")
    explanation: ShortText = Field(description="Why this robot has minimum supplied A* total cost given its actual projected position, battery, workload and availability; explain any choice among tied minima.")


class LLMRoutePlan(WorkflowModel):
    """Structured deterministic pickup and delivery legs, excluding final parking/staging.

    The drop-off is a temporary service cell: the package remains delivered there,
    while the robot continues to its next pickup or a separately planned parking cell.
    """

    robot_id: Identifier = Field(description="The supplied selected robot ID; do not select another robot.")
    order_id: Identifier = Field(description="The supplied selected order ID; do not select another order.")
    outcome: Literal["planned", "unreachable"] = Field(default="planned", description="planned requires both route legs; unreachable requires both legs to be null.")
    route_to_pickup: tuple[Position, ...] | None = Field(default=None, min_length=1, max_length=201,
        description="Endpoint-inclusive orthogonal cells from supplied robot position to this pickup. For later assigned work, this is previous drop-off -> next pickup directly, with no parking visit in between. Null only when unreachable.")
    route_to_dropoff: tuple[Position, ...] | None = Field(default=None, min_length=1, max_length=201,
        description="Endpoint-inclusive orthogonal cells from this pickup to its drop-off. The package remains delivered at the drop-off; this leg does not specify the robot's permanent final position or parking movement. Null only when unreachable.")
    explanation: ShortText = Field(description="Brief deterministic route rationale or reason the assignment is unreachable.")

    @model_validator(mode="after")
    def routes_match_outcome(self) -> Self:
        if self.outcome == "planned" and (self.route_to_pickup is None or self.route_to_dropoff is None):
            raise ValueError("A planned route requires both nonempty legs")
        if self.outcome == "unreachable" and (self.route_to_pickup is not None or self.route_to_dropoff is not None):
            raise ValueError("An unreachable result cannot include a route")
        return self


class SafetyDecision(WorkflowModel):
    """LLM interpretation of trusted deterministic findings for the currently supplied route.

    Approval applies only to this delivery or parking/staging movement;
    Hard deterministic failures cannot be overridden by the LLM; execution also
    protects state integrity. Rejections identify constraints that must change before deterministic replanning.
    """

    approved: bool = Field(strict=True, description="True only if trusted hard checks pass and the LLM approves this supplied route; hard failures require false. Does not approve other routes or execute movement.")
    conflicts: tuple[ShortText, ...] = Field(default=(), max_length=100,
        description="Specific rejection findings: identify the leg, cell or robot and needed correction where applicable. Must be empty when approved.")
    explanation: ShortText = Field(description="Reason for the decision; when rejected, identify specific constraints requiring correction before replanning.")

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
    """One batch assignment and its delivery result, separate from the robot's final parking.

    Delivered means the package stays at its order's drop-off; robot departure
    belongs to the next assignment's pickup leg or the robot schedule's parking leg.
    """

    order_id: Identifier = Field(description="Order ID uniquely identifying this batch assignment.")
    selection: OrderSelection | None = Field(default=None, description="Order LLM selection that produced this assignment.")
    robot_id: Identifier | None = Field(default=None, description="Robot selected by a fresh Fleet decision, or null when no assignment is available.")
    delivery_plan: DeliveryPlan | None = Field(default=None, description="Deterministic A*-generated pickup and delivery routes; excludes final parking/staging.")
    safety: SafetyDecision | None = Field(default=None, description="Safety assessment for this delivery; null means unchecked.")
    status: Literal["approved", "unplannable", "stale", "delivered", "failed", "not_executed"] = Field(description="Planning or execution result for the package delivery, independent of final parking status.")
    reason: ShortText | None = Field(default=None, description="Reason this assignment is stale, unplannable, failed or not executed.")

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
    """Planning-only robot state after its assignment sequence, before final parking.

    A drop-off endpoint is temporary, not idle parking. Independent forecasts
    may share that service cell; finalized schedules resolve physical occupancy.
    """
    robot: Robot = Field(description="Projected position, remaining battery and availability after these assignments; not committed state. Position may temporarily be the latest drop-off. Final parking cost is not yet included.")
    order_ids: tuple[Identifier, ...] = Field(default=(), description="Assignment order for this robot so far. Each later pickup starts directly from the preceding drop-off; an empty sequence means no batch work yet.")


class LLMMovementPlan(WorkflowModel):
    """Structured deterministic final departure to parking/staging when no assigned order remains.

    The delivered package stays at the drop-off while the empty robot leaves.
    Continuation to another pickup belongs to that assignment, not this result.
    """
    robot_id: Identifier = Field(description="ID of the supplied robot with no further assigned work.")
    route: tuple[Position, ...] = Field(min_length=1, max_length=201,
        description="Endpoint-inclusive orthogonal cells from supplied current position, normally the final drop-off, to the reserved free parking/staging cell. Never end at a pickup or drop-off.")
    explanation: ShortText = Field(description="Why this deterministic departure reaches the reserved staging cell under current constraints.")


class PlannedParking(WorkflowModel):
    """Final parking/staging proposal and result, committed separately from delivery."""
    plan: MovementPlan = Field(description="Empty-robot departure after its last assignment, bound to the projected input revision.")
    safety: SafetyDecision | None = Field(description="Safety LLM decision for this parking route; null means unchecked or invalidated.")
    status: Literal["approved", "stale", "completed", "failed", "not_executed"] = Field(default="approved", description="Parking movement result; failure does not undo already completed package deliveries.")
    reason: ShortText | None = Field(default=None, description="Diagnostic for stale, failed or unexecuted parking movement.")

    @model_validator(mode="after")
    def approval(self):
        if self.status in ("approved", "completed") and (self.safety is None or not self.safety.approved):
            raise ValueError("Parking requires model approval")
        return self


class RobotSchedule(WorkflowModel):
    """Ordered robot assignments with direct pickup chaining and one final parking leg.

    Final projected position is parking/staging, never a drop-off. An empty
    assignment sequence permits parking-only recovery after a failed departure.
    """
    robot_id: Identifier = Field(description="Robot that owns this schedule and its exclusive parking reservation.")
    order_ids: tuple[Identifier, ...] = Field(default=(), description="Ordered references to this robot's PlannedDelivery records. Follow each drop-off directly with the next pickup; no intermediate parking. Empty for parking-only recovery.")
    parking: PlannedParking = Field(description="One final parking/staging movement after all listed assignments; no other robot may reserve its destination.")
    projected_robot: Robot = Field(description="Final projected idle robot at parking/staging with battery reduced by all delivery and final departure steps; not committed until execution succeeds.")

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
    Safety stores the model decision constrained by deterministic hard findings. The execution simulator retains
    its own deterministic data-integrity and atomic-movement checks.
    """

    warehouse: WarehouseState = Field(description="Authoritative committed warehouse at graph level; role-local views may contain planning-only projected state.")
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
    planned_deliveries: tuple[PlannedDelivery, ...] = Field(default=(), description="Batch assignments and delivery outcomes; finalized records follow per-robot schedule execution order.")
    batch_revision: int | None = Field(default=None, ge=0, strict=True)
    # Scratch channels exist only while planning. warehouse remains authoritative.
    projected_warehouse: WarehouseState | None = None
    planning_queue: tuple[Identifier, ...] = ()
    planning_index: int = Field(default=0, ge=0, strict=True)
    robot_forecasts: tuple[RobotForecast, ...] = Field(default=(), description="Independent planning-only timelines supplied to each fresh Fleet decision; cleared after finalization.")
    robot_schedules: tuple[RobotSchedule, ...] = Field(default=(), description="Finalized sequential robot schedules, each ending at a distinct parking/staging cell after its last assignment.")

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
