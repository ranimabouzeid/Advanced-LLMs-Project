"""Battery accounting across actual movement, projection, schedules and HTTP commits."""

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from app.sessions import SessionCoordinator
from app.warehouse import Order, Package, Position, Robot, WarehouseSimulation, WarehouseState, plan_delivery
from llm_fakes import client
from test_batch import initial, seed_session


def p(x, y):
    return Position(x=x, y=y)


def scenario(count=2):
    pairs = [(p(5, 0), p(5, 5)), (p(7, 5), p(9, 8))][:count]
    return WarehouseGraphState(command="plan", warehouse=WarehouseState(
        robots=(Robot(id="R1", position=p(0, 0)),
                Robot(id="R2", position=p(0, 8), status="offline"),
                Robot(id="R3", position=p(0, 9), status="offline")),
        orders=tuple(Order(id=f"o{i}", package=Package(id=f"p{i}", pickup=pickup), dropoff=drop)
                     for i, (pickup, drop) in enumerate(pairs, 1)),
        dropoff_locations=frozenset(drop for _, drop in pairs),
        parking_cells=(p(5, 9) if count == 1 else p(9, 4),)))


def test_ten_step_atomic_delivery_commits_ninety_battery():
    before = scenario(1).warehouse
    simulation = WarehouseSimulation(before)
    plan = plan_delivery(before, "o1", "R1")
    assert plan.total_steps == 10
    result = simulation.execute_delivery(plan)
    assert result.success and result.committed_steps == 10
    assert result.final_state == simulation.state
    assert result.final_state.robots[0].battery == 90
    assert result.final_state.revision == before.revision + 1
    assert result.final_state.robots[1:] == before.robots[1:]
    assert before.robots[0].battery == 100


@pytest.mark.parametrize("count,remaining", [(1, 86), (2, 79)])
def test_http_execute_and_checkpoint_keep_delivery_and_parking_battery(count, remaining):
    coordinator = SessionCoordinator(client=client())
    before = scenario(count)
    sid = seed_session(coordinator, before)
    path = f"/api/sessions/{sid}"
    with TestClient(create_app(coordinator=coordinator)) as http:
        planned = http.post(f"{path}/plan")
        assert planned.status_code == 200
        ready = WarehouseGraphState.model_validate(planned.json()["state"])
        assert ready.run_outcome == "ready"
        assert ready.warehouse == before.warehouse
        assert [d.delivery_plan.total_steps for d in ready.planned_deliveries] == [10, 7][:count]
        schedule, = ready.robot_schedules
        assert schedule.parking.plan.total_steps == 4
        assert schedule.projected_robot.battery == remaining
        executed = http.post(f"{path}/execute")
        assert executed.status_code == 200
        result = WarehouseGraphState.model_validate(executed.json()["state"])
        assert result.run_outcome == "delivered"
        assert result.warehouse.robots[0].battery == remaining
        assert result.warehouse.robots[0] == schedule.projected_robot
        assert result.warehouse.robots[1:] == before.warehouse.robots[1:]
        assert result.warehouse_revision == before.warehouse_revision + count + 1
        assert http.get(f"{path}/state").json()["state"] == executed.json()["state"]
        assert coordinator.get_state(sid) == result


def test_plan_forecasts_accumulate_without_mutating_committed_battery():
    before = scenario()
    states = [WarehouseGraphState.model_validate(value) for value in
              build_graph(client=client()).stream(before, stream_mode="values")]
    assert all(state.warehouse == before.warehouse for state in states)
    forecasts = {forecast.order_ids: forecast.robot.battery for state in states
                 for forecast in state.robot_forecasts if forecast.robot.id == "R1"}
    assert forecasts == {(): 100, ("o1",): 90, ("o1", "o2"): 83}
    assert states[-1].robot_forecasts == ()  # Scratch forecasts end at finalization.
    assert states[-1].robot_schedules[0].projected_robot.battery == 79


def test_each_robot_pays_only_its_own_schedule_cost():
    coordinator = SessionCoordinator(client=client())
    before = initial(count=2)
    sid = seed_session(coordinator, before)
    ready = coordinator.plan(sid)
    assert {s.robot_id for s in ready.robot_schedules} == {"robot-1", "robot-2"}
    costs = {s.robot_id: s.parking.plan.total_steps + sum(
        d.delivery_plan.total_steps for d in ready.planned_deliveries if d.robot_id == s.robot_id)
        for s in ready.robot_schedules}
    result = coordinator.execute(sid)
    assert result.run_outcome == "delivered"
    for old, new in zip(before.warehouse.robots, result.warehouse.robots):
        assert new.battery == old.battery - costs.get(old.id, 0)
    assert result.warehouse.robots[2] == before.warehouse.robots[2]


@pytest.mark.parametrize("failed_order,remaining,revision", [("o1", 100, 0), ("o2", 90, 1)])
def test_late_delivery_failure_discards_only_that_delivery_battery(
        monkeypatch, failed_order, remaining, revision):
    coordinator = SessionCoordinator(client=client())
    before = scenario()
    sid = seed_session(coordinator, before)
    coordinator.plan(sid)
    original = WarehouseSimulation.deliver_package
    attempted_batteries = []

    def fail_after_movement(simulation, order_id, robot_id):
        if order_id == failed_order:
            attempted_batteries.append(simulation.get_robot(robot_id).battery)
            raise ValueError("Injected failure after all delivery movement")
        return original(simulation, order_id, robot_id)

    monkeypatch.setattr(WarehouseSimulation, "deliver_package", fail_after_movement)
    result = coordinator.execute(sid)
    assert attempted_batteries == [90 if failed_order == "o1" else 83]
    assert result.run_outcome == ("failed" if failed_order == "o1" else "partial")
    assert result.warehouse.robots[0].battery == remaining
    assert result.warehouse_revision == before.warehouse_revision + revision
    assert result.robot_schedules[0].parking.status == "not_executed"
    assert coordinator.get_state(sid) == result


def test_late_parking_failure_discards_parking_battery_and_recovery_uses_remaining(monkeypatch):
    coordinator = SessionCoordinator(client=client())
    before = scenario()
    sid = seed_session(coordinator, before)
    ready = coordinator.plan(sid)
    original = WarehouseSimulation.move_robot
    attempted_batteries = []
    fail_at = ready.robot_schedules[0].parking.plan.route[2]

    def fail_during_parking(simulation, robot_id, cell):
        moved = original(simulation, robot_id, cell)
        if all(o.status == "delivered" for o in simulation.state.orders) and cell == fail_at:
            attempted_batteries.append(moved.battery)
            raise ValueError("Injected failure after two parking steps")
        return moved

    monkeypatch.setattr(WarehouseSimulation, "move_robot", fail_during_parking)
    result = coordinator.execute(sid)
    assert attempted_batteries == [81]
    assert result.run_outcome == "partial"
    assert result.warehouse.robots[0].battery == 83
    assert result.warehouse.robots[0].position == p(9, 8)
    assert result.warehouse_revision == before.warehouse_revision + 2
    assert coordinator.get_state(sid) == result
    monkeypatch.setattr(WarehouseSimulation, "move_robot", original)
    recovery = coordinator.plan(sid)
    assert recovery.warehouse == result.warehouse
    assert recovery.robot_schedules[0].order_ids == ()
    parked = coordinator.execute(sid)
    assert parked.run_outcome == "delivered"
    assert parked.warehouse.robots[0].battery == 79
    assert parked.warehouse_revision == before.warehouse_revision + 3


def test_later_batch_starts_from_committed_parked_battery():
    coordinator = SessionCoordinator(client=client())
    sid = seed_session(coordinator, scenario())
    coordinator.plan(sid)
    first = coordinator.execute(sid)
    assert first.warehouse.robots[0].battery == 79
    coordinator.create_order(sid, "o3", "p3", p(9, 5), p(9, 8))
    ready = coordinator.plan(sid)
    assert ready.warehouse.robots[0].battery == 79
    assert ready.planned_deliveries[0].delivery_plan.total_steps == 4
    assert ready.robot_schedules[0].parking.plan.total_steps == 4
    result = coordinator.execute(sid)
    assert result.run_outcome == "delivered"
    assert result.warehouse.robots[0].battery == 71
    assert result.warehouse_revision == first.warehouse_revision + 3
    assert coordinator.get_state(sid) == result
