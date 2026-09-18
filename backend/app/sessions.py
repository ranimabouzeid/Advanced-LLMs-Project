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
from app.graph.state import (
    NodeActivity,
    OrderSelection,
    PlannedDelivery,
    SafetyDecision,
    WarehouseGraphState,
    RobotForecast,
    RobotSchedule,
    PlannedParking,
)
from app.warehouse.movement import MovementPlan
from app.graph.updates import replace_warehouse
from app.warehouse import (
    DeliveryPlan,
    Order,
    OrderStatus,
    Package,
    Position,
    Robot,
    RobotConflict,
    RobotStatus,
    ValidationIssue,
    ValidationResult,
    WarehouseSimulation,
    WarehouseState,
)


logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Base service error; provider/checkpointer details are not public messages."""


class UnknownSession(SessionError):
    """The requested session does not exist or has been retired."""


class SessionBusy(SessionError):
    """Another operation currently owns this session's execution guard."""


class InvalidMutation(SessionError):
    """A requested warehouse mutation was rejected by the domain model."""


class SessionExecutionError(SessionError):
    """An unexpected workflow or checkpoint failure occurred.

    These errors represent infrastructure, execution, or checkpoint problems.
    Normal agent/workflow rejections should instead be returned through the
    typed WarehouseGraphState with run_outcome="failed".
    """


class SessionCommandRejected(SessionExecutionError):
    """Expected command precondition failure carrying committed session state.

    This subtype preserves compatibility with callers that already handle
    SessionExecutionError while allowing the HTTP adapter to treat command
    precondition failures separately.
    """

    def __init__(self, state: WarehouseGraphState):
        self.code: Literal["missing_proposal", "consumed_proposal"] = (
            "consumed_proposal"
            if any(
                item.status in ("delivered", "failed", "not_executed")
                for item in state.planned_deliveries
            )
            else "missing_proposal"
        )

        self.message = "No delivery proposal is available; plan before executing"
        self.state = state

        super().__init__(self.message)


@dataclass(frozen=True)
class SessionCreated:
    """Result returned after creating a new isolated warehouse session."""

    session_id: str
    state: WarehouseGraphState


@dataclass
class _Session:
    """Internal session entry containing its committed checkpoint and guard."""

    committed: RunnableConfig
    guard: LockType = field(default_factory=Lock)


class SessionCoordinator:
    """Coordinate LangGraph execution and committed state for each session.

    A single graph/checkpointer instance is shared across sessions, while each
    session maintains its own authoritative checkpoint reference and lock.

    The model client is injected once when the coordinator is created. Callers
    never directly provide graph state, plans, or checkpoint identifiers.
    """

    def __init__(self, *, client: BaseChatModel):
        serializer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=[
                WarehouseGraphState,
                OrderSelection,
                PlannedDelivery,
                SafetyDecision,
                NodeActivity,
                WarehouseState,
                RobotForecast,
                RobotSchedule,
                PlannedParking,
                MovementPlan,
                DeliveryPlan,
                ValidationResult,
                ValidationIssue,
                RobotConflict,
                Order,
                OrderStatus,
                Package,
                Position,
                Robot,
                RobotStatus,
            ],
        )

        self._saver = InMemorySaver(serde=serializer)
        self._graph = build_graph(client=client, checkpointer=self._saver)

        self._registry: dict[str, _Session] = {}
        self._registry_lock = Lock()

    @contextmanager
    def _guard(self, session_id: str):
        """Hold a nonblocking per-session guard around one state operation.

        The guard prevents two operations from reading or publishing competing
        checkpoints for the same session at the same time.
        """

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
            logger.exception(
                "Session operation failed; committed checkpoint retained"
            )

            raise SessionExecutionError(
                "Session operation failed; committed state preserved"
            ) from exc

        finally:
            entry.guard.release()

    def _read(self, config: RunnableConfig):
        """Load and validate the graph state stored at one checkpoint."""

        snapshot = self._graph.get_state(deepcopy(config))

        if (
            not snapshot.config
            or not snapshot.config.get("configurable", {}).get("checkpoint_id")
        ):
            raise SessionExecutionError("Checkpoint is missing")

        state = WarehouseGraphState.model_validate(snapshot.values)

        return snapshot, state

    def _publish(
        self,
        config: RunnableConfig,
        state: WarehouseGraphState,
    ):
        """Publish a complete validated checkpoint without invoking agents."""

        # Execution has only an END edge. Attribution through the execution
        # node lets LangGraph persist the complete state without running an
        # agent or scheduling additional workflow work.
        updated = self._graph.update_state(
            deepcopy(config),
            state.model_dump(),
            as_node="execution",
        )

        snapshot, reconstructed = self._read(updated)

        if snapshot.next or reconstructed != state:
            raise SessionExecutionError(
                "Checkpoint publication did not finish"
            )

        return deepcopy(snapshot.config), reconstructed

    def _fresh(self):
        """Create a new warehouse state and publish its initial checkpoint."""

        session_id = str(uuid4())

        initial = WarehouseGraphState(
            warehouse=WarehouseSimulation().state,
            command="plan",
        )

        config, state = self._publish(
            {"configurable": {"thread_id": session_id}},
            initial,
        )

        return SessionCreated(session_id, state), _Session(config)

    def create_session(self) -> SessionCreated:
        """Create and register a new independent warehouse session."""

        try:
            result, entry = self._fresh()

            with self._registry_lock:
                self._registry[result.session_id] = entry

            return result

        except SessionError:
            raise

        except Exception as exc:
            logger.exception("Session creation failed")

            raise SessionExecutionError(
                "Session creation failed"
            ) from exc

    def get_state(self, session_id: str) -> WarehouseGraphState:
        """Return the current committed state for a session."""

        with self._guard(session_id) as entry:
            return self._read(entry.committed)[1]

    def _command(
        self,
        session_id: str,
        command: str,
    ) -> WarehouseGraphState:
        """Run one graph command and commit its completed terminal checkpoint.

        Normal workflow-level rejection is represented by
        ``run_outcome="failed"`` and failed NodeActivity entries. Such a result
        is still a valid completed graph state and is returned to the API.

        Unexpected Python exceptions or unfinished LangGraph checkpoints remain
        SessionExecutionError conditions and preserve the previous committed
        checkpoint.
        """

        with self._guard(session_id) as entry:
            state = self._read(entry.committed)[1]

            # Execute requires an approved/stale delivery or parking proposal.
            if (
                command == "execute"
                and not any(
                    item.status in ("approved", "stale")
                    for item in state.planned_deliveries
                )
                and not any(
                    schedule.parking.status in ("approved", "stale")
                    for schedule in state.robot_schedules
                )
            ):
                raise SessionCommandRejected(state)

            inputs = {
                **state.model_dump(),
                "command": command,
                "execution_requested": False,
            }

            try:
                self._graph.invoke(
                    inputs,
                    deepcopy(entry.committed),
                    durability="sync",
                )

            except Exception:
                logger.exception(
                    "Session command=%s graph invocation failed",
                    command,
                )
                raise

            # No other writer can use this session thread while the guard is
            # held. Only after invoke() finishes do we inspect the newest
            # checkpoint for possible publication.
            snapshot, result = self._read(
                {"configurable": {"thread_id": session_id}}
            )

            new_activity = result.node_activity[
                len(state.node_activity):
            ]

            # A non-empty `next` means LangGraph did not reach a terminal
            # checkpoint. That is an unexpected execution/checkpoint problem
            # and must NOT become authoritative session state.
            if snapshot.next:
                logger.error(
                    (
                        "Session command=%s left unfinished checkpoint: "
                        "next=%r run_outcome=%s activity=%r"
                    ),
                    command,
                    snapshot.next,
                    result.run_outcome,
                    [
                        item.model_dump(mode="json")
                        for item in new_activity
                    ],
                )

                raise SessionExecutionError(
                    "Workflow operation did not reach a terminal checkpoint"
                )

            # A failed node is different from a Python/server failure.
            #
            # The graph reached a valid terminal state and intentionally
            # rejected part of the workflow. Preserve that state so the API can
            # return run_outcome="failed" and expose the typed workflow result
            # instead of incorrectly converting it to HTTP 500.
            if any(
                item.status == "failed"
                for item in new_activity
            ):
                logger.warning(
                    (
                        "Session command=%s completed with workflow failure: "
                        "run_outcome=%s activity=%r"
                    ),
                    command,
                    result.run_outcome,
                    [
                        item.model_dump(mode="json")
                        for item in new_activity
                    ],
                )

            # The graph reached a terminal, validated checkpoint. It becomes the
            # new authoritative session state whether the workflow succeeded or
            # returned a controlled workflow-level rejection.
            entry.committed = deepcopy(snapshot.config)

            return result

    def plan(self, session_id: str) -> WarehouseGraphState:
        """Plan assignments and parking against projected, uncommitted state."""

        return self._command(session_id, "plan")

    def execute(self, session_id: str) -> WarehouseGraphState:
        """Review and execute an available delivery or parking schedule."""

        return self._command(session_id, "execute")

    def _mutate(
        self,
        session_id: str,
        operation,
        *args,
    ) -> WarehouseGraphState:
        """Apply one validated warehouse mutation and publish the result.

        Domain mutations invalidate pending delivery and parking approvals
        through the existing replace_warehouse update logic.
        """

        with self._guard(session_id) as entry:
            state = self._read(entry.committed)[1]

            simulation = WarehouseSimulation(state.warehouse)

            try:
                operation(simulation, *args)

            except (ValueError, TypeError) as exc:
                raise InvalidMutation(
                    "Warehouse mutation rejected"
                ) from exc

            update = replace_warehouse(
                state,
                simulation.state,
            )

            candidate = WarehouseGraphState.model_validate(
                {
                    **state.model_dump(),
                    **update,
                }
            )

            config, result = self._publish(
                entry.committed,
                candidate,
            )

            entry.committed = config

            return result

    def create_order(
        self,
        session_id: str,
        order_id: str,
        package_id: str,
        pickup: Position,
        dropoff: Position,
    ) -> WarehouseGraphState:
        """Create one order through the validated warehouse simulation."""

        return self._mutate(
            session_id,
            WarehouseSimulation.create_order,
            order_id,
            package_id,
            pickup,
            dropoff,
        )

    def add_blocked_cell(
        self,
        session_id: str,
        position: Position,
    ) -> WarehouseGraphState:
        """Add one validated blocked warehouse cell."""

        return self._mutate(
            session_id,
            WarehouseSimulation.add_blocked_cell,
            position,
        )

    def remove_blocked_cell(
        self,
        session_id: str,
        position: Position,
    ) -> WarehouseGraphState:
        """Remove one blocked warehouse cell."""

        return self._mutate(
            session_id,
            WarehouseSimulation.remove_blocked_cell,
            position,
        )

    def reset(self, session_id: str) -> SessionCreated:
        """Retire the current session and replace it with a fresh session."""

        with self._guard(session_id):
            result, entry = self._fresh()

            with self._registry_lock:
                self._registry[result.session_id] = entry
                del self._registry[session_id]

            return result