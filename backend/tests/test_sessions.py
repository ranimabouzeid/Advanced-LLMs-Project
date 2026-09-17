"""Real in-memory checkpoints and workflow; injected offline model only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from uuid import UUID

import pytest

from app.sessions import (
    InvalidMutation, SessionBusy, SessionCoordinator, SessionExecutionError, UnknownSession,
)
from app.graph.state import WarehouseGraphState
from app.graph.graph import build_graph
from app.warehouse import Position, WarehouseSimulation
from test_graph_workflow import client


@pytest.fixture
def coordinator():
    return SessionCoordinator(client=client())


def order(coordinator, sid, name="o"):
    return coordinator.create_order(sid, name, "p-" + name,
                                    Position(x=2, y=0), Position(x=9, y=0))


def prepared(coordinator):
    sid = coordinator.create_session().session_id
    order(coordinator, sid)
    return sid


def test_creation_checkpoint_uuid_and_no_agents(coordinator):
    created = coordinator.create_session()
    assert str(UUID(created.session_id)) == created.session_id
    assert created.state == coordinator.get_state(created.session_id)
    assert created.state.warehouse == WarehouseSimulation().state
    assert created.state.node_activity == ()
    entry = coordinator._registry[created.session_id]
    assert set(vars(entry)) == {"committed", "guard"}
    assert entry.committed["configurable"]["thread_id"] == created.session_id
    checkpoint = coordinator._graph.get_state(entry.committed)
    assert checkpoint.next == ()
    assert WarehouseGraphState.model_validate(checkpoint.values) == created.state


def test_unique_sessions(coordinator):
    assert len({coordinator.create_session().session_id for _ in range(10)}) == 10


def test_plan_persistence_and_execute_continuity(coordinator):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    ready = coordinator.plan(sid)
    assert ready.run_outcome == "ready"
    assert ready.warehouse == before.warehouse
    assert coordinator.get_state(sid) == ready
    delivered = coordinator.execute(sid)
    assert delivered.run_outcome == "delivered"
    assert coordinator.get_state(sid) == delivered
    assert delivered.warehouse.revision == before.warehouse.revision + 1
    assert delivered.delivery_plan is delivered.order_selection is delivered.safety is None


def test_two_session_order_block_robot_and_battery_isolation(coordinator):
    a = prepared(coordinator)
    b = coordinator.create_session()
    coordinator.add_blocked_cell(a, Position(x=5, y=5))
    coordinator.plan(a)
    delivered = coordinator.execute(a)
    assert delivered.warehouse.robots != b.state.warehouse.robots
    assert any(robot.battery < 100 for robot in delivered.warehouse.robots)
    assert delivered.warehouse.orders and delivered.warehouse.blocked_cells
    assert coordinator.get_state(b.session_id) == b.state


@pytest.mark.parametrize("operation", ["get_state", "plan", "execute", "reset",
                                        "create_order", "add_blocked_cell", "remove_blocked_cell"])
def test_unknown_session(coordinator, operation):
    args = {"create_order": ("o", "p", Position(x=2, y=0), Position(x=9, y=0)),
            "add_blocked_cell": (Position(x=5, y=0),),
            "remove_blocked_cell": (Position(x=5, y=0),)}
    with pytest.raises(UnknownSession):
        getattr(coordinator, operation)("missing", *args.get(operation, ()))


@pytest.mark.parametrize("operation", ["get_state", "plan", "execute", "reset",
                                        "create_order", "add_blocked_cell", "remove_blocked_cell"])
def test_same_session_busy_all_public_operations(coordinator, operation):
    sid = prepared(coordinator)
    args = {"create_order": ("other", "other-p", Position(x=2, y=0), Position(x=9, y=0)),
            "add_blocked_cell": (Position(x=5, y=0),),
            "remove_blocked_cell": (Position(x=5, y=0),)}
    with coordinator._guard(sid):
        with pytest.raises(SessionBusy):
            getattr(coordinator, operation)(sid, *args.get(operation, ()))
    assert coordinator.get_state(sid)


def test_running_command_does_not_block_other_session(coordinator, monkeypatch):
    a, b = prepared(coordinator), coordinator.create_session().session_id
    entered, release = Event(), Event()
    original = coordinator._graph.invoke

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(coordinator._graph, "invoke", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.plan, a)
        try:
            assert entered.wait(10)
            with pytest.raises(SessionBusy):
                coordinator.get_state(a)
            assert order(coordinator, b).warehouse.orders
            assert coordinator.get_state(b)
        finally:
            release.set()
        assert future.result(timeout=10).run_outcome == "ready"
    assert coordinator.get_state(a).run_outcome == "ready"


def test_failed_intermediate_hidden_and_next_command_uses_commit(coordinator, monkeypatch):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    committed = deepcopy(coordinator._registry[sid].committed)
    original = coordinator._graph.invoke

    def interrupted(inputs, config, **kwargs):
        original(inputs, config, interrupt_after=["order"], **kwargs)
        raise RuntimeError("failure after persisted intermediate state")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator._graph, "invoke", interrupted)
        with pytest.raises(SessionExecutionError):
            coordinator.plan(sid)
    latest = coordinator._graph.get_state({"configurable": {"thread_id": sid}})
    assert latest.next == ("fleet",)
    assert latest.values["order_selection"] is not None
    assert coordinator._registry[sid].committed == committed
    assert coordinator.get_state(sid) == before

    def checked(inputs, config, **kwargs):
        assert config == committed
        assert inputs["node_activity"] == ()
        assert inputs["order_selection"] is None
        return original(inputs, config, **kwargs)

    monkeypatch.setattr(coordinator._graph, "invoke", checked)
    assert coordinator.plan(sid).run_outcome == "ready"
    assert len(coordinator.get_state(sid).node_activity) == 4


def test_model_failure_does_not_commit(coordinator):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    coordinator._graph = build_graph(
        client=client({"order_id": "invented", "explanation": "Invalid"}),
        checkpointer=coordinator._saver)
    with pytest.raises(SessionExecutionError):
        coordinator.plan(sid)
    assert coordinator.get_state(sid) == before


@pytest.mark.parametrize("stage", ["write_before", "write_after", "read", "invalid_read"])
def test_checkpoint_failures_preserve_commit_and_release_lock(coordinator, monkeypatch, stage):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    original_write, original_read = coordinator._graph.update_state, coordinator._graph.get_state

    def broken_write(*args, **kwargs):
        if stage == "write_after":
            original_write(*args, **kwargs)
        raise RuntimeError("checkpoint write error")

    calls = 0

    def broken_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original_read(*args, **kwargs)
        if calls == 2:
            if stage == "invalid_read":
                return result._replace(values={"warehouse": "bad"})
            raise RuntimeError("checkpoint read error")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(coordinator._graph, "update_state" if stage.startswith("write") else "get_state",
                      broken_write if stage.startswith("write") else broken_read)
        with pytest.raises(SessionExecutionError):
            coordinator.add_blocked_cell(sid, Position(x=5, y=0))
    assert coordinator.get_state(sid) == before
    assert coordinator.plan(sid).run_outcome == "ready"


def test_mutations_revision_invalidation_and_noops(coordinator):
    sid = prepared(coordinator)
    ready = coordinator.plan(sid)
    changed = coordinator.add_blocked_cell(sid, Position(x=5, y=0))
    assert changed.warehouse_revision == ready.warehouse_revision + 1
    assert changed.safety is None and not changed.execution_requested
    assert changed.planning_outcome == "stale"
    assert coordinator.add_blocked_cell(sid, Position(x=5, y=0)) == changed
    removed = coordinator.remove_blocked_cell(sid, Position(x=5, y=0))
    assert removed.warehouse_revision == changed.warehouse_revision + 1
    assert coordinator.remove_blocked_cell(sid, Position(x=5, y=0)) == removed
    coordinator.plan(sid)
    added = order(coordinator, sid, "second")
    assert added.safety is None and added.planning_outcome == "stale"


def test_stale_replacement_is_review_only(coordinator):
    sid = prepared(coordinator)
    coordinator.plan(sid)
    changed = coordinator.add_blocked_cell(sid, Position(x=5, y=0))
    replacement = coordinator.execute(sid)
    assert replacement.run_outcome == "ready"
    assert replacement.warehouse == changed.warehouse
    assert not replacement.execution_requested
    assert coordinator.execute(sid).run_outcome == "delivered"


@pytest.mark.parametrize("mutation", ["duplicate_order", "occupied_cell", "out_of_bounds"])
def test_invalid_mutation_atomic_and_lock_released(coordinator, mutation):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    with pytest.raises(InvalidMutation):
        if mutation == "duplicate_order":
            order(coordinator, sid)
        elif mutation == "occupied_cell":
            coordinator.add_blocked_cell(sid, before.warehouse.robots[0].position)
        else:
            coordinator.remove_blocked_cell(sid, Position(x=100, y=0))
    assert coordinator.get_state(sid) == before
    assert coordinator.plan(sid).run_outcome == "ready"


def test_duplicate_execute_no_movement_cost_completion_or_other_order_change(coordinator):
    sid = prepared(coordinator)
    with_other = order(coordinator, sid, "other")
    unrelated = with_other.warehouse.orders[1]
    coordinator.plan(sid)
    delivered = coordinator.execute(sid)
    for _ in range(2):
        with pytest.raises(SessionExecutionError):
            coordinator.execute(sid)
        assert coordinator.get_state(sid) == delivered
    assert delivered.warehouse.orders[1] == unrelated
    assert delivered.warehouse.orders[0].status.value == "delivered"


def test_reset_fresh_thread_retires_old_and_no_checkpoint_leaks(coordinator):
    sid = prepared(coordinator)
    coordinator.add_blocked_cell(sid, Position(x=5, y=5))
    coordinator.plan(sid)
    old_config = deepcopy(coordinator._registry[sid].committed)
    fresh = coordinator.reset(sid)
    assert fresh.session_id != sid
    assert fresh.state == WarehouseGraphState(warehouse=WarehouseSimulation().state, command="plan")
    assert coordinator._registry[fresh.session_id].committed["configurable"]["thread_id"] == fresh.session_id
    with pytest.raises(UnknownSession):
        coordinator.execute(sid)
    with pytest.raises(SessionExecutionError):
        coordinator.execute(fresh.session_id)
    assert coordinator.get_state(fresh.session_id) == fresh.state
    assert coordinator._graph.get_state(old_config).values["delivery_plan"] is not None


def test_failed_reset_leaves_old_session_available(coordinator, monkeypatch):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    with monkeypatch.context() as patch:
        patch.setattr(coordinator._graph, "update_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        with pytest.raises(SessionExecutionError):
            coordinator.reset(sid)
    assert coordinator.get_state(sid) == before
    assert list(coordinator._registry) == [sid]


def test_fresh_coordinator_has_no_previous_sessions(coordinator):
    sid = prepared(coordinator)
    with pytest.raises(UnknownSession):
        SessionCoordinator(client=client()).get_state(sid)


def test_returned_state_does_not_expose_checkpoint_mutability(coordinator):
    sid = prepared(coordinator)
    state = coordinator.get_state(sid)
    dumped = state.model_dump()
    dumped["warehouse"]["orders"] = ()
    assert coordinator.get_state(sid) == state


def test_failed_execute_after_delivery_checkpoint_can_retry_once(coordinator, monkeypatch):
    sid = prepared(coordinator)
    ready = coordinator.plan(sid)
    original = coordinator._graph.invoke

    def fail_after_delivery(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("publication failed after delivery checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator._graph, "invoke", fail_after_delivery)
        with pytest.raises(SessionExecutionError):
            coordinator.execute(sid)
    assert coordinator.get_state(sid) == ready
    assert coordinator._graph.get_state({"configurable": {"thread_id": sid}}).values["run_outcome"] == "delivered"
    delivered = coordinator.execute(sid)
    assert delivered.warehouse_revision == ready.warehouse_revision + 1
    assert delivered.run_outcome == "delivered"
    with pytest.raises(SessionExecutionError):
        coordinator.execute(sid)
    assert coordinator.get_state(sid) == delivered


def test_command_final_checkpoint_read_failure_keeps_commit(coordinator, monkeypatch):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)
    original = coordinator._graph.get_state

    def fail_latest(config, **kwargs):
        if "checkpoint_id" not in config["configurable"]:
            raise RuntimeError("final checkpoint unavailable")
        return original(config, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(coordinator._graph, "get_state", fail_latest)
        with pytest.raises(SessionExecutionError):
            coordinator.plan(sid)
    assert coordinator.get_state(sid) == before
    assert coordinator.plan(sid).run_outcome == "ready"


def test_creation_failure_does_not_register_session(coordinator, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(coordinator._graph, "update_state", fail)
    with pytest.raises(SessionExecutionError):
        coordinator.create_session()
    assert coordinator._registry == {}


def test_model_exception_keeps_commit_and_releases_guard(coordinator, monkeypatch):
    sid = prepared(coordinator)
    before = coordinator.get_state(sid)

    def fail(*args, **kwargs):
        raise TimeoutError("fake provider timeout")

    with monkeypatch.context() as patch:
        patch.setattr("test_graph_workflow.Fake.with_structured_output", fail)
        with pytest.raises(SessionExecutionError):
            coordinator.plan(sid)
    assert coordinator.get_state(sid) == before
    assert coordinator.plan(sid).run_outcome == "ready"
