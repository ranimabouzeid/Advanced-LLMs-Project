"""Process-local sessions whose only authoritative state is a committed checkpoint."""

from contextlib import contextmanager
import logging
from _thread import LockType
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.graph.graph import build_graph
from app.graph.state import NodeActivity, OrderSelection, PlannedDelivery, SafetyDecision, WarehouseGraphState
from app.graph.state import RobotForecast, RobotSchedule, PlannedParking, RouteRetryFeedback
from app.warehouse.movement import MovementPlan
from app.graph.updates import replace_warehouse
from app.warehouse import (
    DeliveryPlan, Order, OrderStatus, Package, Position, Robot, RobotConflict,
    RobotStatus, ValidationIssue, ValidationResult, WarehouseSimulation, WarehouseState,
)


logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Base service error; provider/checkpointer details are not public messages."""


class UnknownSession(SessionError):
    """The session does not exist or has been retired."""


class SessionBusy(SessionError):
    """Another operation holds this session's guard; retry later."""


class InvalidMutation(SessionError):
    """An existing domain operation rejected the requested mutation."""


class SessionExecutionError(SessionError):
    """Workflow or checkpoint publication failed; the prior commit is retained."""


class SessionCommandRejected(SessionExecutionError):
    """Expected command precondition failure, carrying only the committed state.

    A subtype preserves existing service callers' execution-error handling.
    HTTP adapters must handle this specific type before SessionExecutionError.
    """

    def __init__(self, state: WarehouseGraphState):
        self.code: Literal["missing_proposal", "consumed_proposal"] = (
            "consumed_proposal" if any(item.status in ("delivered", "failed", "not_executed")
                                       for item in state.planned_deliveries) else "missing_proposal")
        self.message = "No delivery proposal is available; plan before executing"
        self.state = state
        super().__init__(self.message)


@dataclass(frozen=True)
class SessionCreated:
    session_id: str
    state: WarehouseGraphState


@dataclass
class _Session:
    committed: RunnableConfig
    guard: LockType = field(default_factory=Lock)


class SessionCoordinator:
    """One graph/saver, with a private checkpoint reference and lock per session.

    Callers supply a model client once, never state, plans or checkpoint IDs.
    All public state operations acquire the same nonblocking session guard.
    """

    def __init__(self, *, client: BaseChatModel):
        serializer = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=[
            WarehouseGraphState, OrderSelection, PlannedDelivery, SafetyDecision, NodeActivity, WarehouseState,
            RobotForecast, RobotSchedule, PlannedParking, MovementPlan, RouteRetryFeedback,
            DeliveryPlan, ValidationResult, ValidationIssue, RobotConflict,
            Order, OrderStatus, Package, Position, Robot, RobotStatus,
        ])
        self._saver = InMemorySaver(serde=serializer)
        self._graph = build_graph(client=client, checkpointer=self._saver)
        self._registry: dict[str, _Session] = {}
        self._registry_lock = Lock()

    @contextmanager
    def _guard(self, session_id: str):
        """Hold a nonblocking per-session guard so reads and commands observe one committed checkpoint."""
        # Resolve and acquire together so reset cannot retire a resolved entry.
        with self._registry_lock:
            entry = self._registry.get(session_id)
            if entry is None:
                raise UnknownSession("Unknown or retired session")
            if not entry.guard.acquire(blocking=False):
                raise SessionBusy("Session is busy")
        try:
            yield entry
        except SessionError:
            raise
        except Exception as exc:
            logger.exception("Session operation failed; committed checkpoint retained")
            raise SessionExecutionError("Session operation failed; committed state preserved") from exc
        finally:
            entry.guard.release()

    def _read(self, config: RunnableConfig):
        """Load only the specified checkpoint and revalidate its typed warehouse and schedules."""
        snapshot = self._graph.get_state(deepcopy(config))
        if not snapshot.config or not snapshot.config.get("configurable", {}).get("checkpoint_id"):
            raise SessionExecutionError("Checkpoint is missing")
        state = WarehouseGraphState.model_validate(snapshot.values)
        return snapshot, state

    def _publish(self, config: RunnableConfig, state: WarehouseGraphState):
        # execution has only an END edge. Attribution writes data without calling
        # that node or scheduling any agent; every channel is explicitly replaced.
        """Publish a complete checkpoint without invoking agents or scheduling execution."""
        updated = self._graph.update_state(deepcopy(config), state.model_dump(), as_node="execution")
        snapshot, reconstructed = self._read(updated)
        if snapshot.next or reconstructed != state:
            raise SessionExecutionError("Checkpoint publication did not finish")
        return deepcopy(snapshot.config), reconstructed

    def _fresh(self):
        session_id = str(uuid4())
        initial = WarehouseGraphState(warehouse=WarehouseSimulation().state, command="plan")
        config, state = self._publish({"configurable": {"thread_id": session_id}}, initial)
        return SessionCreated(session_id, state), _Session(config)

    def create_session(self) -> SessionCreated:
        try:
            result, entry = self._fresh()
            with self._registry_lock:
                self._registry[result.session_id] = entry
            return result
        except SessionError:
            raise
        except Exception as exc:
            raise SessionExecutionError("Session creation failed") from exc

    def get_state(self, session_id: str) -> WarehouseGraphState:
        with self._guard(session_id) as entry:
            return self._read(entry.committed)[1]

    def _command(self, session_id: str, command: str) -> WarehouseGraphState:
        """Run one guarded command and publish only its completed, validated checkpoint.

        Typed execution failures may retain completed deliveries; unexpected failures
        leave the previous committed checkpoint authoritative."""
        with self._guard(session_id) as entry:
            state = self._read(entry.committed)[1]
            if command == "execute" and not any(
                item.status in ("approved", "stale") for item in state.planned_deliveries
            ) and not any(s.parking.status in ("approved", "stale") for s in state.robot_schedules):
                raise SessionCommandRejected(state)
            inputs = {**state.model_dump(), "command": command, "execution_requested": False}
            try:
                self._graph.invoke(inputs, deepcopy(entry.committed), durability="sync")
            except Exception:
                logger.exception("Session command=%s graph invocation failed", command)
                raise
            # No other writer can use this thread under the guard. Only after
            # invoke finishes may its newest checkpoint be considered for commit.
            snapshot, result = self._read({"configurable": {"thread_id": session_id}})
            new_activity = result.node_activity[len(state.node_activity):]
            if snapshot.next or any(item.status == "failed" for item in new_activity):
                logger.error("Session command=%s rejected checkpoint: next=%r run_outcome=%s activity=%r",
                             command, snapshot.next, result.run_outcome,
                             [item.model_dump(mode="json") for item in new_activity])
                raise SessionExecutionError("Workflow operation failed; committed state preserved")
            entry.committed = deepcopy(snapshot.config)
            return result

    def plan(self, session_id: str) -> WarehouseGraphState:
        """Plan assignments and final parking against projected state without committed movement."""
        return self._command(session_id, "plan")

    def execute(self, session_id: str) -> WarehouseGraphState:
        """Review and execute an unconsumed delivery or parking-only schedule under the session guard."""
        return self._command(session_id, "execute")

    def _mutate(self, session_id: str, operation, *args) -> WarehouseGraphState:
        """Apply one validated domain mutation and invalidate pending delivery and parking approvals."""
        with self._guard(session_id) as entry:
            state = self._read(entry.committed)[1]
            simulation = WarehouseSimulation(state.warehouse)
            try:
                operation(simulation, *args)
            except (ValueError, TypeError) as exc:
                raise InvalidMutation("Warehouse mutation rejected") from exc
            update = replace_warehouse(state, simulation.state)
            candidate = WarehouseGraphState.model_validate({**state.model_dump(), **update})
            config, result = self._publish(entry.committed, candidate)
            entry.committed = config
            return result

    def create_order(self, session_id: str, order_id: str, package_id: str,
                     pickup: Position, dropoff: Position) -> WarehouseGraphState:
        return self._mutate(session_id, WarehouseSimulation.create_order,
                            order_id, package_id, pickup, dropoff)

    def add_blocked_cell(self, session_id: str, position: Position) -> WarehouseGraphState:
        return self._mutate(session_id, WarehouseSimulation.add_blocked_cell, position)

    def remove_blocked_cell(self, session_id: str, position: Position) -> WarehouseGraphState:
        return self._mutate(session_id, WarehouseSimulation.remove_blocked_cell, position)

    def reset(self, session_id: str) -> SessionCreated:
        with self._guard(session_id):
            result, entry = self._fresh()
            with self._registry_lock:
                self._registry[result.session_id] = entry
                del self._registry[session_id]
            return result
