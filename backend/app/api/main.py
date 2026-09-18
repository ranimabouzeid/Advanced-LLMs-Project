"""Synchronous FastAPI adapter. All session behavior goes through the coordinator."""

import os
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import create_model_client
from app.sessions import (
    InvalidMutation, SessionBusy, SessionCommandRejected, SessionCoordinator,
    SessionExecutionError, UnknownSession,
)
from app.warehouse.models import Position
from .schemas import ApiError, CommandResponse, CreateOrderRequest, ErrorResponse, SessionResponse
from .sessions import get_coordinator


Coordinator = Annotated[SessionCoordinator, Depends(get_coordinator)]
logger = logging.getLogger(__name__)


async def require_no_body(request: Request) -> None:
    """Even unknown JSON on command endpoints must not silently be ignored."""
    if await request.body():
        raise RequestValidationError([{"loc": ("body",), "msg": "Body not accepted",
                                       "type": "extra_forbidden"}])


def _error(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ApiError(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def create_app(*, coordinator: SessionCoordinator | None = None,
               allowed_origins: list[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Importing this module never creates a client or reads model settings.
        app.state.coordinator = (coordinator if coordinator is not None else
                                 SessionCoordinator(client=create_model_client()))
        try:
            yield
        finally:
            del app.state.coordinator

    app = FastAPI(title="Warehouse session API", lifespan=lifespan, debug=False)
    origins = (allowed_origins if allowed_origins is not None else
               [value.strip() for value in os.environ.get(
                   "CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if value.strip()])
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return _error(422, "invalid_request", "Invalid request body or path parameters")

    @app.exception_handler(UnknownSession)
    async def unknown_session(request: Request, exc: UnknownSession):
        return _error(404, "unknown_session", "Unknown or retired session")

    @app.exception_handler(SessionBusy)
    async def busy_session(request: Request, exc: SessionBusy):
        return _error(409, "session_busy", "Session is busy; retry after the current operation")

    @app.exception_handler(InvalidMutation)
    async def invalid_mutation(request: Request, exc: InvalidMutation):
        return _error(422, "invalid_mutation", "Warehouse mutation rejected")

    @app.exception_handler(SessionCommandRejected)
    async def rejected_command(request: Request, exc: SessionCommandRejected):
        response = CommandResponse(session_id=request.path_params["session_id"], state=exc.state,
                                   outcome="failed", error=ApiError(code=exc.code, message=exc.message))
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    @app.exception_handler(SessionExecutionError)
    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("API operation failed: %s %s", request.method, request.url.path,
                     exc_info=(type(exc), exc, exc.__traceback__))
        return _error(500, "internal_error", "Session operation could not be completed")

    errors = {status: {"model": ErrorResponse} for status in (404, 409, 422, 500)}
    no_body = [Depends(require_no_body)]

    @app.post("/api/sessions", status_code=201, response_model=SessionResponse,
              dependencies=no_body, responses=errors)
    def create_session(service: Coordinator):
        created = service.create_session()
        return SessionResponse(session_id=created.session_id, state=created.state)

    @app.get("/api/sessions/{session_id}/state", response_model=SessionResponse, responses=errors)
    def get_state(session_id: str, service: Coordinator):
        return SessionResponse(session_id=session_id, state=service.get_state(session_id))

    @app.post("/api/sessions/{session_id}/orders", status_code=201,
              response_model=SessionResponse, responses=errors)
    def create_order(session_id: str, body: CreateOrderRequest, service: Coordinator):
        state = service.create_order(session_id, body.order_id, body.package_id, body.pickup, body.dropoff)
        return SessionResponse(session_id=session_id, state=state)

    def command_response(session_id, state):
        error = (ApiError(code="workflow_rejected", message="Command rejected by workflow validation")
                 if state.run_outcome == "failed" else None)
        return CommandResponse(session_id=session_id, state=state, outcome=state.run_outcome, error=error)

    @app.post("/api/sessions/{session_id}/plan", response_model=CommandResponse,
              dependencies=no_body, responses=errors)
    def plan(session_id: str, service: Coordinator):
        return command_response(session_id, service.plan(session_id))

    @app.post("/api/sessions/{session_id}/execute", response_model=CommandResponse,
              dependencies=no_body, responses=errors)
    def execute(session_id: str, service: Coordinator):
        return command_response(session_id, service.execute(session_id))

    @app.post("/api/sessions/{session_id}/blocked-cells", response_model=SessionResponse, responses=errors)
    def add_blocked_cell(session_id: str, body: Position, service: Coordinator):
        return SessionResponse(session_id=session_id, state=service.add_blocked_cell(session_id, body))

    @app.delete("/api/sessions/{session_id}/blocked-cells/{x}/{y}",
                response_model=SessionResponse, responses=errors)
    def remove_blocked_cell(session_id: str, x: int, y: int, service: Coordinator):
        return SessionResponse(session_id=session_id,
                               state=service.remove_blocked_cell(session_id, Position(x=x, y=y)))

    @app.post("/api/sessions/{session_id}/reset", status_code=201,
              response_model=SessionResponse, dependencies=no_body, responses=errors)
    def reset(session_id: str, service: Coordinator):
        created = service.reset(session_id)
        return SessionResponse(session_id=created.session_id, state=created.state)

    return app


app = create_app()
