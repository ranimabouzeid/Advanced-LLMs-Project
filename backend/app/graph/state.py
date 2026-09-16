"""Serializable workflow records with one authoritative warehouse snapshot.

These schemas do not execute commands, validate route safety, or apply updates.
Future tools receive warehouse from trusted runtime code, never LLM arguments.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.warehouse.models import DeliveryPlan, Identifier, OrderStatus, WarehouseState
from app.warehouse.validation import ValidationResult


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Command = Literal["plan", "execute"]
PlanningOutcome = Literal["not_planned", "planned", "stale", "unreachable", "failed"]
RunOutcome = Literal["idle", "running", "ready", "delivered", "no_work", "no_robot", "unreachable", "failed"]


class WorkflowModel(BaseModel):
    """Immutable records, rejecting misspelled or undeclared fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OrderSelection(WorkflowModel):
    """Structured selection; the enclosing state checks pending-order eligibility."""

    order_id: Identifier
    explanation: ShortText


class NodeActivity(WorkflowModel):
    """One actual activity; tuple position supplies its order in the run."""

    node: Literal["order", "fleet", "route", "safety", "execution"]
    status: Literal["completed", "rejected", "failed"]
    message: ShortText | None = None


class WarehouseGraphState(WorkflowModel):
    """Typed state for future LangGraph use, without runtime objects or reducers.

    warehouse.revision is the only current revision. A plan's revision describes
    its historical input snapshot. None safety means unchecked, never approval.
    Construction validates data, not the authenticity of a safety finding;
    future Safety code must obtain that finding from deterministic validation.
    """

    warehouse: WarehouseState
    command: Command
    execution_requested: bool = Field(default=False, strict=True)
    order_selection: OrderSelection | None = None
    selected_robot_id: Identifier | None = None
    delivery_plan: DeliveryPlan | None = None
    planning_outcome: PlanningOutcome = "not_planned"
    safety: ValidationResult | None = None
    replan_count: int = Field(default=0, ge=0, strict=True)
    max_replans: int = Field(default=3, ge=0, le=3, strict=True)
    run_outcome: RunOutcome = "idle"
    error_message: ShortText | None = None
    node_activity: tuple[NodeActivity, ...] = Field(default_factory=tuple)

    @property
    def warehouse_revision(self) -> int:
        """Read-only nonnegative revision; deliberately not a serialized field."""
        return self.warehouse.revision

    @model_validator(mode="after")
    def validate_references_and_limits(self) -> Self:
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
