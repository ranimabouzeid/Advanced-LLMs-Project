"""Exact Fleet costs, constrained model choices, and batch endpoint regressions."""

import pytest

from app.graph import tools as fleet_tools
from app.graph.agents import fleet_agent
from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from app.graph.tools import FleetTools
from app.warehouse import Order, Package, Position, WarehouseSimulation, WarehouseState
from llm_fakes import client
from test_batch import initial
from test_fleet_agent import make_state


def p(x, y):
    return Position(x=x, y=y)


def run(state, model):
    return WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))


def fleet_payloads(model):
    return [payload for schema, payload, _ in model.calls if schema.__name__ == "FleetSelection"]


def choose(robot_id):
    return {"robot_id": robot_id, "explanation": "Choose a supplied minimum"}


@pytest.mark.parametrize("second_pickup, expected, costs", [
    (p(1, 4), "robot-2", [14, 8, 12]),
    (p(5, 0), "robot-1", [2, 10, 14]),
])
def test_shared_dropoff_has_no_preference_except_actual_cost(second_pickup, expected, costs):
    base = initial(count=1).warehouse
    second = Order(id="o2", package=Package(id="p2", pickup=second_pickup), dropoff=p(4, 0))
    state = WarehouseGraphState(command="plan", warehouse=WarehouseState.model_validate({
        **base.model_dump(), "orders": (*base.orders, second)}))
    model = client()
    ready = run(state, model)
    assert ready.run_outcome == "ready", ready.error_message
    assert [(d.order_id, d.robot_id) for d in ready.planned_deliveries] == [
        ("o1", "robot-1"), ("o2", expected)]
    first, second_input = fleet_payloads(model)
    assert [c["total_cost"] for c in second_input["candidates"]] == costs
    assert second_input["candidates"][0]["robot"]["position"] == {"x": 4, "y": 0}
    assert second_input["candidates"][0]["robot"]["battery"] == 96
    assert first["robot_forecasts"][0]["order_ids"] == []
    assert second_input["robot_forecasts"][0]["order_ids"] == ["o1"]
    if expected == "robot-1":
        assert ready.planned_deliveries[1].delivery_plan.pickup_route[0] == p(4, 0)
        assert len(ready.robot_schedules) == 1
    delivered = run(WarehouseGraphState.model_validate({**ready.model_dump(), "command": "execute"}), model)
    assert delivered.run_outcome == "delivered"
    assert all(s.parking.status == "completed" for s in delivered.robot_schedules)


def test_later_batch_costs_start_at_actual_parking_with_remaining_battery():
    model = client()
    ready = run(initial(count=1), model)
    delivered = run(WarehouseGraphState.model_validate({**ready.model_dump(), "command": "execute"}), model)
    assert delivered.run_outcome == "delivered"
    parked = delivered.warehouse.robots[0]
    assert parked.position == p(7, 9) and parked.battery == 84
    simulation = WarehouseSimulation(delivered.warehouse)
    simulation.create_order("later", "later-package", p(1, 4), p(4, 0))
    later_model = client()
    later = run(WarehouseGraphState(warehouse=simulation.state, command="plan"), later_model)
    payload, = fleet_payloads(later_model)
    candidate = payload["candidates"][0]
    assert candidate["robot"] == parked.model_dump(mode="json")
    assert (candidate["pickup_cost"], candidate["delivery_cost"], candidate["total_cost"]) == (11, 7, 18)
    assert payload["robot_forecasts"][0]["order_ids"] == []
    assert later.planned_deliveries[0].robot_id == "robot-2"


def test_astar_recalculates_both_legs_for_all_robots_for_each_order(monkeypatch):
    calls = []
    original = fleet_tools.astar_path
    import inspect

    def counted(*args):
        if inspect.currentframe().f_back.f_code.co_name == "candidate_records":
            calls.append(args)
        return original(*args)

    monkeypatch.setattr(fleet_tools, "astar_path", counted)
    model = client()
    result = run(initial(), model)
    assert result.run_outcome == "ready"
    payloads = fleet_payloads(model)
    assert len(payloads) == 4 and len(calls) == 4 * 3 * 2
    for order_index, payload in enumerate(payloads):
        assert len(payload["candidates"]) == 3
        for robot_index, candidate in enumerate(payload["candidates"]):
            pickup_call, delivery_call = calls[order_index * 6 + robot_index * 2:order_index * 6 + robot_index * 2 + 2]
            assert pickup_call[2].model_dump() == candidate["robot"]["position"]
            assert delivery_call[2].model_dump() == payload["selected_order"]["package"]["pickup"]
            assert candidate["total_cost"] == candidate["pickup_cost"] + candidate["delivery_cost"]
    assert calls[18][2] == p(4, 0)  # Fourth decision starts R1 at its active chain tail.


def test_nonminimum_choice_retries_with_exact_unchanged_costs():
    model = client(choose("r3"), choose("r1"))
    state = make_state()
    update = fleet_agent(state, client=model)
    assert update["run_outcome"] == "running" and update["selected_robot_id"] == "r1"
    first, retry = fleet_payloads(model)
    assert first["candidates"] == retry["candidates"]
    assert "selection_feedback" not in first
    assert retry["selection_feedback"]["rejected_robot_id"] == "r3"
    assert retry["minimum_total_cost"] == 9 and retry["minimum_robot_ids"] == ["r1"]


@pytest.mark.parametrize("bad_choice", ["r2", "missing", None])
def test_repeated_invalid_choice_is_bounded_and_never_silently_replaced(bad_choice):
    model = client(choose(bad_choice))
    update = fleet_agent(make_state(), client=model)
    assert len(fleet_payloads(model)) == 2
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None
    assert "minimum-cost" in update["error_message"]


@pytest.mark.parametrize("robot_id", ["r1", "r2"])
def test_llm_may_choose_either_tied_minimum(robot_id):
    state = make_state(positions=((1, 0), (0, 1), (0, 4)), pickup=(2, 2))
    model = client(choose(robot_id))
    assert fleet_agent(state, client=model)["selected_robot_id"] == robot_id
    payload, = fleet_payloads(model)
    assert payload["minimum_robot_ids"] == ["r1", "r2"]


def test_battery_infeasible_geometric_minimum_cannot_win():
    state = make_state(batteries=(8, 100, 100))
    model = client(choose("r1"), choose("r2"))
    assert fleet_agent(state, client=model)["selected_robot_id"] == "r2"
    candidates = fleet_payloads(model)[0]["candidates"]
    assert candidates[0]["total_cost"] == 9 and not candidates[0]["feasible"]
    assert candidates[0]["reason"] == "Insufficient projected battery"
    assert candidates[1]["total_cost"] == 11 and candidates[1]["feasible"]


def test_costs_are_astar_detours_not_manhattan_estimates():
    state = make_state(blocked=((1, 0),))
    model = client()
    fleet_agent(state, client=model)
    candidate = fleet_payloads(model)[0]["candidates"][0]
    assert candidate["pickup_cost"] == 4  # Manhattan is 2; blocked cell requires detour.
    assert candidate["delivery_cost"] == 7 and candidate["total_cost"] == 11
    assert "route" not in candidate  # Actual Route coordinates remain model-authored.


@pytest.mark.parametrize("kwargs, outcome", [
    ({"statuses": ("offline", "busy", "charging")}, "no_robot"),
    ({"batteries": (0, 0, 0)}, "no_robot"),
    ({"blocked": ((2, 0),)}, "unreachable"),
])
def test_no_feasible_candidate_still_calls_groq_contract_and_preserves_outcome(kwargs, outcome):
    model = client()
    result = fleet_agent(make_state(**kwargs), client=model)
    assert result["run_outcome"] == outcome and result["selected_robot_id"] is None
    payload, = fleet_payloads(model)
    assert payload["minimum_total_cost"] is None and payload["minimum_robot_ids"] == []
    assert all(not c["feasible"] for c in payload["candidates"])
