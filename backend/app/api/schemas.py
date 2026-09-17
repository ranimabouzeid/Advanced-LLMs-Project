"""HTTP envelopes reusing the authoritative domain and workflow models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.graph.state import RunOutcome, WarehouseGraphState
from app.warehouse.models import Identifier, Position


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateOrderRequest(ApiModel):
    order_id: Identifier
    package_id: Identifier
    pickup: Position
    dropoff: Position


class ApiError(ApiModel):
    code: Literal["invalid_request", "unknown_session", "session_busy", "invalid_mutation",
                  "missing_proposal", "consumed_proposal", "workflow_rejected", "internal_error"]
    message: str


class ErrorResponse(ApiModel):
    error: ApiError


class SessionResponse(ApiModel):
    session_id: str
    state: WarehouseGraphState


class CommandResponse(SessionResponse):
    """Outcome describes this attempt; state always describes the committed checkpoint."""

    outcome: RunOutcome
    error: ApiError | None = None
