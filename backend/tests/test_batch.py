"""Projected assignment, per-delivery atomicity, and committed batch acceptance."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import create_app
from app.graph import batch
from app.graph.graph import build_graph
from app.graph.state import PlannedDelivery, WarehouseGraphState
from app.graph.tools import FleetTools
from app.graph.updates import replace_warehouse
from app.sessions import SessionCoordinator, SessionCommandRejected, SessionExecutionError
from app.warehouse import Order, Package, Position, Robot, WarehouseSimulation, WarehouseState
from app.warehouse.simulation import DeliveryExecutionResult
from test_graph_workflow import client, merge


def p(x, y):
    return Position(x=x, y=y)


def initial(*, battery=100, blocked=(), count=4):
    pairs = [(p(1, 0), p(4, 0)), (p(1, 4), p(4, 4)),
             (p(1, 8), p(4, 8)), (p(5, 0), p(6, 0))][:count]
    warehouse = WarehouseState(
        robots=tuple(Robot(id=f"robot-{i+1}", position=p(0, i*4), battery=battery if i == 0 else 100)
                     for i in range(3)),
        orders=tuple(Order(id=f"o{i+1}", package=Package(id=f"p{i+1}", pickup=pickup), dropoff=drop)
                     for i, (pickup, drop) in enumerate(pairs)),
        blocked_cells=frozenset(blocked), dropoff_locations=frozenset(drop for _, drop in pairs))
    return WarehouseGraphState(warehouse=warehouse, command="plan")


def run(state, **kwargs):
    return WarehouseGraphState.model_validate(build_graph(client=client(), **kwargs).invoke(state))


def seed_session(coordinator, state):
    sid = coordinator.create_session().session_id
    entry = coordinator._registry[sid]
    entry.committed, _ = coordinator._publish(entry.committed, state)
    return sid


class RecordingFleet(FleetTools):
    def __init__(self):
        self.calls = []

    def order_record(self, warehouse, order_id):
        self.calls.extend((order_id, robot.id, warehouse) for robot in warehouse.robots)
        return super().order_record(warehouse, order_id)


def test_every_order_evaluates_every_robot_again_on_projected_state():
    state, model = initial(), client()
    ready = WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))
    assert ready.run_outcome == "ready" and ready.warehouse == state.warehouse
    calls = [data for schema, data, _ in model.calls if schema.__name__ == "FleetSelection"]
    assert len(calls) == 4
    for index, data in enumerate(calls):
        assert data["selected_order"]["id"] == f"o{index+1}"
        assert [r["id"] for r in data["robots"]] == ["robot-1", "robot-2", "robot-3"]
    assert calls[3]["robots"][0]["position"] == {"x": 4, "y": 0}
    assert calls[3]["robots"][0]["battery"] == 96
    assert [(d.order_id, d.robot_id) for d in ready.planned_deliveries] == [
        ("o1", "robot-1"), ("o4", "robot-1"), ("o2", "robot-2"), ("o3", "robot-3")]
    assert ready.robot_forecasts == ()


def test_low_projected_battery_is_supplied_and_final_parking_requires_energy():
    state, model = initial(battery=5), client()
    result = WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))
    calls = [data for schema, data, _ in model.calls if schema.__name__ == "FleetSelection"]
    assert calls[3]["robots"][0]["battery"] == 1
    # The mock does not reserve parking energy. Final Safety must stop this batch.
    assert result.run_outcome == "failed" and result.warehouse == state.warehouse
    assert not result.execution_requested


def test_unreachable_first_order_does_not_prevent_other_orders():
    state = initial(blocked=(p(1, 0),))
    ready = run(state)
    assert ready.run_outcome == "ready" and ready.warehouse == state.warehouse
    first = next(d for d in ready.planned_deliveries if d.order_id == "o1")
    rest = [d for d in ready.planned_deliveries if d.order_id != "o1"]
    assert first.status == "unplannable" and "unreachable" in first.reason
    assert all(item.status == "approved" for item in rest)
    delivered = run(merge(ready, {"command": "execute"}))
    assert delivered.warehouse.orders[0].status == "pending"
    assert all(order.status == "delivered" for order in delivered.warehouse.orders[1:])


def test_obstacle_detour_changes_robot_in_batch():
    from test_fleet_agent import make_state
    state = make_state(batteries=(9, 100, 100), blocked=((1, 0),))
    ready = run(state)
    assert ready.planned_deliveries[0].robot_id == "r2"
    plan = ready.planned_deliveries[0].delivery_plan
    assert p(1, 0) not in (*plan.pickup_route, *plan.delivery_route)


@pytest.mark.parametrize("reverse", [False, True])
def test_mocked_fleet_choice_is_used(reverse):
    from test_fleet_agent import make_state
    state = make_state(positions=((1, 0), (0, 1), (0, 4)), pickup=(2, 2), reverse=reverse)
    assert run(state).planned_deliveries[0].robot_id == "r1"


def test_full_batch_execution_and_later_plan_excludes_delivered():
    state = initial()
    ready = run(state)
    delivered = run(merge(ready, {"command": "execute"}))
    assert delivered.run_outcome == "delivered"
    assert delivered.warehouse_revision == state.warehouse_revision + 4 + len(ready.robot_schedules)
    assert delivered.warehouse.robots == tuple(s.projected_robot for s in ready.robot_schedules)
    assert all(record.status == "delivered" for record in delivered.planned_deliveries)
    repeated = run(merge(delivered, {"command": "plan"}))
    assert repeated.run_outcome == "no_work" and repeated.planned_deliveries == ()
    assert repeated.warehouse == delivered.warehouse


def test_stale_batch_reconsiders_all_robots_and_requires_another_execute():
    state = initial(count=1)
    ready = run(state)
    simulation = WarehouseSimulation(ready.warehouse)
    # Move robot-2 near pickup; it becomes optimal after the first proposal.
    for position in (p(1, 4), p(1, 3), p(1, 2), p(1, 1), p(1, 0)):
        simulation.move_robot("robot-2", position)
    changed = merge(ready, {"warehouse": simulation.state, "command": "execute"})
    tools = RecordingFleet()
    replacement = run(changed, fleet_tools=tools)
    assert replacement.run_outcome == "ready" and replacement.selected_robot_id == "robot-2"
    assert len(tools.calls) == 3 and replacement.replan_count == 1
    assert not replacement.execution_requested and replacement.warehouse == changed.warehouse
    assert run(replacement).run_outcome == "delivered"


def test_repeated_mocked_plan_never_projects_twice():
    state = initial()
    first = run(state)
    second = run(first)
    assert second.planned_deliveries == first.planned_deliveries
    assert second.warehouse == state.warehouse


def test_llm_can_reorder_pending_orders():
    state = initial()
    graph = build_graph(client=client(scripts={"OrderSelection": [{"order_id": "o2", "explanation": "Prioritize second"}]}))
    result = WarehouseGraphState.model_validate(graph.invoke(state))
    assert result.run_outcome == "ready" and result.warehouse == state.warehouse
    assert result.planned_deliveries[0].order_id == "o2"
    assert {item.order_id for item in result.planned_deliveries} == {"o1", "o2", "o3", "o4"}


@pytest.mark.parametrize("field,value", [("order_id", "wrong"), ("robot_id", "robot-2"),
                                        ("safety", None), ("delivery_plan", None)])
def test_batch_record_rejects_mismatched_or_unchecked_approval(field, value):
    record = run(initial(count=1)).planned_deliveries[0]
    with pytest.raises(ValidationError):
        PlannedDelivery.model_validate({**record.model_dump(), field: value})


def test_no_duplicate_order_in_batch_and_json_roundtrip():
    ready = run(initial())
    assert WarehouseGraphState.model_validate_json(ready.model_dump_json()) == ready
    with pytest.raises(ValidationError, match="duplicate"):
        merge(ready, {"planned_deliveries": (*ready.planned_deliveries, ready.planned_deliveries[0])})


@pytest.mark.parametrize("failure_index", [0, 1, 2, 3])
def test_atomic_failure_commits_only_completed_prefix(failure_index, monkeypatch):
    coordinator = SessionCoordinator(client=client())
    state = initial()
    sid = seed_session(coordinator, state)
    ready = coordinator.plan(sid)
    original = batch.apply_delivery
    calls = []

    def fail_one(warehouse, plan):
        calls.append(plan.order_id)
        if len(calls) == failure_index + 1:
            return DeliveryExecutionResult(success=False, failed_stage="delivery", error="Injected failure")
        return original(warehouse, plan)

    monkeypatch.setattr(batch, "apply_delivery", fail_one)
    result = coordinator.execute(sid)
    assert result.run_outcome == ("partial" if failure_index else "failed")
    assert result.warehouse_revision == state.warehouse_revision + failure_index + sum(s.parking.status == "completed" for s in result.robot_schedules)
    assert [item.status for item in result.planned_deliveries] == (
        ["delivered"] * failure_index + ["failed"] + ["not_executed"] * (3 - failure_index))
    done = {d.order_id for d in ready.planned_deliveries[:failure_index]}
    assert all(o.status == ("delivered" if o.id in done else "pending") for o in result.warehouse.orders)
    assert coordinator.get_state(sid) == result
    with pytest.raises(SessionCommandRejected):
        coordinator.execute(sid)
    # Replanning excludes the delivered prefix and uses the actual partial snapshot.
    replacement = coordinator.plan(sid)
    assert {item.order_id for item in replacement.planned_deliveries} == {o.id for o in result.warehouse.orders if o.status == "pending"}
    assert replacement.warehouse == result.warehouse


@pytest.mark.parametrize("stage", ["execution", "checkpoint"])
def test_unexpected_batch_failure_preserves_committed_checkpoint(stage, monkeypatch):
    coordinator = SessionCoordinator(client=client())
    sid = seed_session(coordinator, initial())
    before = coordinator.plan(sid)
    committed = deepcopy(coordinator._registry[sid].committed)
    original = coordinator._graph.invoke

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("After uncommitted final checkpoint")

    if stage == "checkpoint":
        monkeypatch.setattr(coordinator._graph, "invoke", interrupted)
    else:
        original_apply = batch.apply_delivery
        def broken(warehouse, plan):
            if plan.order_id == "o2":
                raise RuntimeError("Unexpected execution failure")
            return original_apply(warehouse, plan)
        monkeypatch.setattr(batch, "apply_delivery", broken)
    with pytest.raises(SessionExecutionError):
        coordinator.execute(sid)
    assert coordinator._registry[sid].committed == committed
    assert coordinator.get_state(sid) == before


def test_batch_api_roundtrip_and_checkpoint_isolation():
    coordinator = SessionCoordinator(client=client())
    sid = seed_session(coordinator, initial())
    other = coordinator.create_session()
    with TestClient(create_app(coordinator=coordinator)) as http:
        ready = http.post(f"/api/sessions/{sid}/plan").json()
        assert ready["outcome"] == "ready" and len(ready["state"]["planned_deliveries"]) == 4
        assert ready["state"]["projected_warehouse"] is None
        result = http.post(f"/api/sessions/{sid}/execute").json()
        assert result["outcome"] == "delivered"
        assert all(item["status"] == "delivered" for item in result["state"]["planned_deliveries"])
        assert http.get(f"/api/sessions/{sid}/state").json()["state"] == result["state"]
    assert coordinator.get_state(other.session_id) == other.state


def test_mutation_invalidates_every_batch_approval():
    ready = run(initial())
    simulation = WarehouseSimulation(ready.warehouse)
    simulation.add_blocked_cell(p(8, 7))
    changed = merge(ready, replace_warehouse(ready, simulation.state))
    assert all(item.status == "stale" and item.safety is None for item in changed.planned_deliveries)
    replacement = run(merge(changed, {"command": "execute"}))
    assert replacement.run_outcome == "ready" and len(replacement.planned_deliveries) == 4
    assert replacement.warehouse == changed.warehouse


def test_batch_larger_than_default_langgraph_step_limit():
    state = initial(count=1)
    orders = tuple(Order(id=f"o{i}", package=Package(id=f"p{i}", pickup=p(4, 0)), dropoff=p(4, 0))
                   for i in range(12))
    state = merge(state, {"warehouse": {**state.warehouse.model_dump(), "orders": orders}})
    ready = run(state)
    assert len(ready.planned_deliveries) == 12 and ready.run_outcome == "ready"
    assert all(item.robot_id == "robot-1" for item in ready.planned_deliveries)
    assert run(merge(ready, {"command": "execute"})).warehouse_revision == 13


def test_batch_partial_result_is_visible_through_api(monkeypatch):
    coordinator = SessionCoordinator(client=client())
    sid = seed_session(coordinator, initial())
    coordinator.plan(sid)
    original = batch.apply_delivery
    def fail_second(warehouse, plan):
        if plan.order_id == "o2":
            return DeliveryExecutionResult(success=False, failed_stage="delivery", error="Injected")
        return original(warehouse, plan)
    monkeypatch.setattr(batch, "apply_delivery", fail_second)
    with TestClient(create_app(coordinator=coordinator)) as http:
        response = http.post(f"/api/sessions/{sid}/execute")
        assert response.status_code == 200
        result = response.json()
        assert result["outcome"] == "partial" and result["error"] is None
        assert [item["status"] for item in result["state"]["planned_deliveries"]] == [
            "delivered", "delivered", "failed", "not_executed"]
        assert http.get(f"/api/sessions/{sid}/state").json()["state"] == result["state"]


def test_second_order_model_failure_discards_entire_uncommitted_plan():
    coordinator = SessionCoordinator(client=client(scripts={"OrderSelection": [
        {"order_id": "o1", "explanation": "First"},
        {"order_id": "invented", "explanation": "Invalid"}]}))
    state = initial()
    sid = seed_session(coordinator, state)
    before = coordinator.get_state(sid)
    with pytest.raises(SessionExecutionError):
        coordinator.plan(sid)
    assert coordinator.get_state(sid) == before
    assert not coordinator.get_state(sid).planned_deliveries


def test_invalid_later_route_rejects_batch_before_any_delivery():
    ready = run(initial())
    records = [item.model_dump() for item in ready.planned_deliveries]
    route = records[1]["delivery_plan"]["pickup_route"]
    records[1]["delivery_plan"]["pickup_route"] = (p(8, 8).model_dump(), *route[1:])
    tampered = merge(ready, {"planned_deliveries": records, "command": "execute"})
    result = run(tampered)
    assert result.run_outcome == "failed" and result.warehouse == ready.warehouse
    assert all(item.status == "stale" for item in result.planned_deliveries)
    assert result.replan_count == 0 and not result.execution_requested
    assert not any(item.node == "execution" for item in result.node_activity)
