"""Per-robot chains, shared service cells, and atomic final parking."""

import pytest
from pydantic import ValidationError

from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from app.graph import schedules
from app.sessions import SessionCoordinator, SessionExecutionError, SessionCommandRejected
from app.warehouse import Position, WarehouseSimulation, WarehouseState
from app.warehouse.movement import MovementPlan, MovementResult, execute_parking
from llm_fakes import client
from test_batch import seed_session


def p(x, y):
    return Position(x=x, y=y)


def scenario(assignments=("robot-1", "robot-2")):
    sim = WarehouseSimulation()
    for index, pickup in enumerate((p(4, 2), p(1, 4), p(4, 4))[:len(assignments)], 1):
        sim.create_order(f"o{index}", f"p{index}", pickup, p(9, 0))
    model = client(scripts={"FleetSelection": [dict(robot_id=rid, explanation="Independent choice") for rid in assignments]})
    return WarehouseGraphState(warehouse=sim.state, command="plan"), model


def run(state, model):
    return WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))


def execute(ready, model):
    return run(WarehouseGraphState.model_validate({**ready.model_dump(), "command": "execute"}), model)


def test_single_delivery_has_one_final_parking_route_and_full_battery_accounting():
    state, model = scenario(("robot-1",))
    ready = run(state, model)
    assert ready.run_outcome == "ready" and ready.warehouse == state.warehouse
    schedule, = ready.robot_schedules
    record, = ready.planned_deliveries
    assert schedule.parking.plan.route[0] == p(9, 0)
    assert schedule.parking.plan.route[-1] == state.warehouse.parking_cells[0]
    assert schedule.projected_robot.battery == 100 - record.delivery_plan.total_steps - schedule.parking.plan.total_steps
    assert schedule.projected_robot.position == schedule.parking.plan.route[-1]
    assert sum(schema.__name__ == "LLMMovementPlan" for schema, _, _ in model.calls) == 1
    result = execute(ready, model)
    assert result.run_outcome == "delivered"
    assert result.robot_schedules[0].parking.status == "completed"
    assert result.warehouse.robots[0] == schedule.projected_robot
    assert result.warehouse_revision == state.warehouse_revision + 2


def test_r1_r2_r1_assignments_chain_without_intermediate_parking():
    state, model = scenario(("robot-1", "robot-2", "robot-1"))
    ready = run(state, model)
    assert ready.run_outcome == "ready", ready.error_message
    r1, r2 = ready.robot_schedules
    assert r1.order_ids == ("o1", "o3") and r2.order_ids == ("o2",)
    first, next_work, other = ready.planned_deliveries
    assert next_work.delivery_plan.pickup_route[0] == first.delivery_plan.delivery_route[-1] == p(9, 0)
    assert next_work.delivery_plan.warehouse_revision == first.delivery_plan.warehouse_revision + 1
    assert r1.parking.plan.warehouse_revision == next_work.delivery_plan.warehouse_revision + 1
    fleet = [data for schema, data, _ in model.calls if schema.__name__ == "FleetSelection"]
    assert len(fleet) == 3 and all(len(data["robots"]) == 3 for data in fleet)
    assert fleet[2]["robots"][0]["position"] == {"x": 9, "y": 0}
    assert fleet[2]["robots"][1]["position"] == {"x": 9, "y": 0}
    assert fleet[2]["robots"][0]["battery"] < 100
    assert fleet[2]["robot_forecasts"][0]["order_ids"] == ["o1"]
    # One movement to parking per robot, after all its own assigned deliveries.
    assert sum(schema.__name__ == "LLMMovementPlan" for schema, _, _ in model.calls) == 2
    result = execute(ready, model)
    assert result.run_outcome == "delivered"
    assert all(o.status == "delivered" for o in result.warehouse.orders)
    assert all(r.position not in result.warehouse.dropoff_locations for r in result.warehouse.robots)


def test_two_robots_share_dropoff_after_departure_and_reserve_distinct_parking():
    state, model = scenario()
    ready = run(state, model)
    assert [(d.order_id, d.robot_id) for d in ready.planned_deliveries] == [("o1", "robot-1"), ("o2", "robot-2")]
    first, second = ready.robot_schedules
    assert first.parking.plan.route[-1] != second.parking.plan.route[-1]
    assert ready.planned_deliveries[1].delivery_plan.warehouse_revision == first.parking.plan.warehouse_revision + 1
    # Inspect the finalized second delivery's actual Safety input, not the preview.
    checks = [data for schema, data, _ in model.calls if schema.__name__ == "SafetyDecision" and "delivery_plan" in data]
    assert checks[-1]["warehouse"]["robots"][0]["position"] == first.projected_robot.position.model_dump()
    result = execute(ready, model)
    assert result.run_outcome == "delivered"
    assert all(s.parking.status == "completed" for s in result.robot_schedules)
    assert result.warehouse.robots[0].position != p(9, 0)
    assert result.warehouse.robots[1].position != p(9, 0)


def test_duplicate_parking_reservation_rejected_by_schema():
    state, model = scenario()
    ready = run(state, model)
    data = ready.model_dump()
    target = data["robot_schedules"][0]["parking"]["plan"]["route"][-1]
    second = data["robot_schedules"][1]
    second["parking"]["plan"]["route"] = (*second["parking"]["plan"]["route"][:-1], target)
    second["projected_robot"]["position"] = target
    with pytest.raises(ValidationError, match="same parking"):
        WarehouseGraphState.model_validate(data)


@pytest.mark.parametrize("kind", ["shelf", "dropoff", "pickup", "outside", "duplicate"])
def test_invalid_designated_parking_cells_rejected(kind):
    state, _ = scenario(("robot-1",))
    cells = {"shelf": [p(3, 2)], "dropoff": [p(9, 0)], "pickup": [p(4, 2)],
             "outside": [p(10, 9)], "duplicate": [p(7, 9), p(7, 9)]}[kind]
    with pytest.raises(ValidationError):
        WarehouseState.model_validate({**state.warehouse.model_dump(), "parking_cells": cells})


def test_allocation_excludes_blocked_occupied_and_reserved_cells():
    state, _ = scenario(("robot-1",))
    data = state.warehouse.model_dump()
    data["blocked_cells"] = [p(7, 9)]
    data["robots"][1]["position"] = p(8, 9)
    warehouse = WarehouseState.model_validate(data)
    assert schedules.parking_target(warehouse, "robot-1", set()) == p(8, 8)
    assert schedules.parking_target(warehouse, "robot-1", {p(8, 8)}) is None


@pytest.mark.parametrize("case", ["all_blocked", "unreachable", "insufficient_battery"])
def test_unavailable_parking_never_publishes_ready_or_changes_warehouse(case):
    state, model = scenario(("robot-1",))
    data = state.warehouse.model_dump()
    if case == "all_blocked":
        data["blocked_cells"] = list(state.warehouse.parking_cells)
    elif case == "unreachable":
        data["parking_cells"] = [p(7, 9)]
        data["blocked_cells"] = [p(6, 9), p(8, 9), p(7, 8)]
    else:
        data["robots"][0]["battery"] = 13  # Delivery consumes all energy.
    state = WarehouseGraphState(warehouse=WarehouseState.model_validate(data), command="plan")
    result = run(state, model)
    assert result.run_outcome == "failed" and result.warehouse == state.warehouse
    assert not result.robot_schedules and not result.execution_requested


def test_post_delivery_failure_keeps_delivery_and_supports_parking_only_recovery(monkeypatch):
    state, model = scenario(("robot-1",))
    coordinator = SessionCoordinator(client=model)
    sid = seed_session(coordinator, state)
    ready = coordinator.plan(sid)
    original = schedules.execute_parking
    monkeypatch.setattr(schedules, "execute_parking", lambda *args: MovementResult(success=False, error="Injected parking failure"))
    result = coordinator.execute(sid)
    assert result.run_outcome == "partial" and result.planned_deliveries[0].status == "delivered"
    assert result.warehouse.orders[0].status == "delivered"
    assert result.warehouse.robots[0].position == p(9, 0)
    assert result.warehouse_revision == state.warehouse_revision + 1
    assert result.robot_schedules[0].parking.status == "failed"
    assert coordinator.get_state(sid) == result
    with pytest.raises(SessionCommandRejected):
        coordinator.execute(sid)
    monkeypatch.setattr(schedules, "execute_parking", original)
    recovery = coordinator.plan(sid)
    assert recovery.run_outcome == "ready" and recovery.planned_deliveries == ()
    assert recovery.robot_schedules[0].order_ids == ()
    parked = coordinator.execute(sid)
    assert parked.warehouse.orders == result.warehouse.orders
    assert parked.warehouse.robots[0].position in parked.warehouse.parking_cells
    assert parked.warehouse_revision == result.warehouse_revision + 1


def test_parking_failure_stops_dependent_robot_but_preserves_checkpoint(monkeypatch):
    state, model = scenario()
    coordinator = SessionCoordinator(client=model)
    sid = seed_session(coordinator, state)
    coordinator.plan(sid)
    monkeypatch.setattr(schedules, "execute_parking", lambda *args: MovementResult(success=False, error="Injected"))
    result = coordinator.execute(sid)
    assert [d.status for d in result.planned_deliveries] == ["delivered", "not_executed"]
    assert [s.parking.status for s in result.robot_schedules] == ["failed", "not_executed"]
    assert coordinator.get_state(sid) == result


def test_unexpected_parking_exception_preserves_whole_previous_commit(monkeypatch):
    state, model = scenario()
    coordinator = SessionCoordinator(client=model)
    sid = seed_session(coordinator, state)
    ready = coordinator.plan(sid)
    def broken(*args):
        raise RuntimeError("Unexpected infrastructure failure")
    monkeypatch.setattr(schedules, "execute_parking", broken)
    with pytest.raises(SessionExecutionError):
        coordinator.execute(sid)
    assert coordinator.get_state(sid) == ready


@pytest.mark.parametrize("bad", ["jump", "stale", "battery", "blocked", "occupied", "wrong_start"])
def test_atomic_parking_integrity_rejection_preserves_delivered_snapshot(bad):
    state, model = scenario(("robot-1",))
    ready = run(state, model)
    sim = WarehouseSimulation(state.warehouse)
    assert sim.execute_delivery(ready.planned_deliveries[0].delivery_plan).success
    delivered = sim.state
    plan = ready.robot_schedules[0].parking.plan
    data = plan.model_dump()
    if bad == "jump":
        data["route"] = (plan.route[0], plan.route[-1])
    elif bad == "stale":
        data["warehouse_revision"] -= 1
    elif bad == "wrong_start":
        data["route"] = (p(8, 0), *plan.route[1:])
    else:
        snapshot = delivered.model_dump()
        if bad == "battery":
            snapshot["robots"][0]["battery"] = 0
        elif bad == "blocked":
            snapshot["blocked_cells"] = [plan.route[-1]]
        else:
            snapshot["robots"][1]["position"] = plan.route[-1]
        delivered = WarehouseState.model_validate(snapshot)
    before = delivered.model_dump()
    result = execute_parking(delivered, MovementPlan.model_validate(data))
    assert not result.success and result.final_state is None
    assert delivered.model_dump() == before and delivered.orders[0].status == "delivered"


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_parking_safety_retries_are_bounded_and_receive_feedback(budget):
    state, model = scenario(("robot-1",))
    ready = run(state, model)
    original = ready.robot_schedules[0].parking.plan.route
    routes = [dict(robot_id="robot-1", explanation="Revised departure",
                   route=([p(9, 0), *([p(8, 0), p(9, 0)] * attempt), *original[1:]]))
              for attempt in range(budget + 1)]
    yes = dict(approved=True, conflicts=[], explanation="Approved")
    no = dict(approved=False, conflicts=["Revise departure"], explanation="Try another path")
    model = client(scripts={"FleetSelection": [dict(robot_id="robot-1", explanation="Chosen")],
                            "LLMMovementPlan": routes, "SafetyDecision": [yes, yes] + [no] * (budget + 1)})
    state = WarehouseGraphState.model_validate({**state.model_dump(), "max_replans": budget})
    result = run(state, model)
    calls = [data for schema, data, _ in model.calls if schema.__name__ == "LLMMovementPlan"]
    assert len(calls) == budget + 1 and result.replan_count == budget
    assert result.run_outcome == "failed" and result.warehouse == state.warehouse
    for data in calls[1:]:
        assert data["safety_feedback"] == no and data["previous_route"] is not None


def test_parking_rejection_on_execute_returns_review_only_replacement():
    state, model = scenario(("robot-1",))
    ready = run(state, model)
    model = client(scripts={"SafetyDecision": [
        dict(approved=True, conflicts=[], explanation="Delivery approved"),
        dict(approved=False, conflicts=["Review departure"], explanation="Parking rejected"),
    ]})
    replacement = execute(ready, model)
    assert replacement.run_outcome == "ready" and replacement.replan_count == 1
    assert replacement.warehouse == ready.warehouse and not replacement.execution_requested
    assert execute(replacement, model).run_outcome == "delivered"


def test_incorrect_llm_parking_approval_cannot_bypass_execution_checks():
    state, model = scenario(("robot-1",))
    model.scripts["LLMMovementPlan"] = [dict(robot_id="robot-1", route=[p(9, 0), p(7, 9)],
                                            explanation="Model proposes a jump")]
    model.scripts["SafetyDecision"] = [dict(approved=True, conflicts=[], explanation="Model approves")] * 5
    ready = run(state, model)
    assert ready.run_outcome == "ready"
    result = execute(ready, model)
    assert result.run_outcome == "partial" and result.warehouse.orders[0].status == "delivered"
    assert result.warehouse.robots[0].position == p(9, 0)
    assert result.robot_schedules[0].parking.status == "failed"
