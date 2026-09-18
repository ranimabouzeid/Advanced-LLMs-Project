"""Inspect real ChatGroq requests offline, without automatic model decisions."""

from copy import deepcopy
import json

import pytest

from app.config import LLMSettings, create_model_client
from app.graph import batch
from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from app.sessions import SessionCoordinator
from app.warehouse import DeliveryPlan, Order, Package, Position, Robot, WarehouseSimulation, WarehouseState


def p(x, y):
    return Position(x=x, y=y)


@pytest.mark.parametrize("second_robot", ["robot-3", "robot-1"])
@pytest.mark.parametrize("checkpointed", [False, True])
def test_each_order_has_fresh_fleet_request_with_all_projected_robots(
        second_robot, checkpointed, monkeypatch, caplog):
    # Separate rows clearly favor R1 then R3; the alternate scenario legitimately
    # favors R1 again, near its first drop-off. No planner chooses the responses.
    second_pickup, second_drop = ((p(1, 8), p(4, 8)) if second_robot == "robot-3"
                                  else (p(5, 0), p(6, 0)))
    orders = (
        Order(id="o1", package=Package(id="p1", pickup=p(1, 0)), dropoff=p(4, 0)),
        Order(id="o2", package=Package(id="p2", pickup=second_pickup), dropoff=second_drop),
    )
    state = WarehouseGraphState(command="plan", warehouse=WarehouseState(
        robots=tuple(Robot(id=f"robot-{i+1}", position=p(0, i*4)) for i in range(3)),
        orders=orders, dropoff_locations=frozenset(order.dropoff for order in orders)))
    model = create_model_client(LLMSettings(model="offline-test", api_key="test-placeholder"))
    responses = []
    for index, robot_id in enumerate(("robot-1", second_robot)):
        order = orders[index]
        responses.extend([
            ("OrderSelection", dict(order_id=order.id, explanation="Scripted order")),
            ("FleetSelection", dict(robot_id=robot_id, explanation="Fresh scripted Fleet decision")),
            ("RouteIntent", dict(order_id=order.id, robot_id=robot_id,
                                 route_type="continuation" if index and robot_id == "robot-1" else "delivery",
                                 explanation="Scripted routing intent")),
            ("SafetyDecision", dict(approved=True, conflicts=[], explanation="Scripted approval")),
        ])
    initial_responses = list(responses)
    groups = [("robot-1", [0, 1])] if second_robot == "robot-1" else [("robot-1", [0]), ("robot-3", [1])]
    for robot_id, indices in groups:
        for index in indices:
            responses.extend(initial_responses[index * 4 + 2:index * 4 + 4])
        responses.extend([
            ("RouteIntent", dict(robot_id=robot_id, order_id=None, route_type="parking", explanation="Scripted parking intent")),
            ("SafetyDecision", dict(approved=True, conflicts=[], explanation="Scripted parking approval")),
        ])
    requests = []

    def completion(**kwargs):
        requests.append(deepcopy(kwargs))
        name, output = responses[len(requests) - 1]
        assert kwargs["tool_choice"]["function"]["name"] == name
        return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": f"offline-{len(requests)}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(output)}}]}}], "model": "offline-test"}

    monkeypatch.setattr(model.client, "create", completion)
    fleet_states = []
    original_fleet = batch.fleet_agent

    def inspect_fleet(current, *, client, tools):
        assert client is model
        fleet_states.append(current)
        return original_fleet(current, client=client, tools=tools)

    monkeypatch.setattr(batch, "fleet_agent", inspect_fleet)
    caplog.set_level("INFO", logger="app.graph.batch")
    if checkpointed:
        coordinator = SessionCoordinator(client=model)
        sid = coordinator.create_session().session_id
        entry = coordinator._registry[sid]
        entry.committed, _ = coordinator._publish(entry.committed, state)
        result = coordinator.plan(sid)
        assert coordinator.get_state(sid) == result
    else:
        result = WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))

    fleet_requests = [request for request in requests
                      if request["tool_choice"]["function"]["name"] == "FleetSelection"]
    assert len(requests) == len(responses) and len(fleet_requests) == len(fleet_states) == 2
    payloads = [json.loads(request["messages"][-1]["content"]) for request in fleet_requests]
    for index, (current, payload) in enumerate(zip(fleet_states, payloads)):
        assert payload["selected_order"]["id"] == f"o{index+1}"
        assert len(payload["candidates"]) == 3
        assert [c["robot"] for c in payload["candidates"]] == payload["robots"]
        for candidate in payload["candidates"]:
            assert candidate["feasible"]
            assert candidate["total_cost"] == candidate["pickup_cost"] + candidate["delivery_cost"]
        assert payload["minimum_total_cost"] == min(c["total_cost"] for c in payload["candidates"])
        assert payload["minimum_robot_ids"] == ["robot-1" if index == 0 else second_robot]
        assert {robot["id"] for robot in payload["robots"]} == {"robot-1", "robot-2", "robot-3"}
        assert payload["robots"] == payload["warehouse"]["robots"]
        assert payload["robots"] == [f.robot.model_dump(mode="json") for f in current.robot_forecasts]
        assert current.selected_robot_id is current.delivery_plan is current.safety is None
        assert current.planning_outcome == "not_planned" and current.run_outcome == "running"
        assert current.error_message is None and not current.execution_requested
        assert "selected_robot_id" not in payload
    before = {robot["id"]: robot for robot in payloads[0]["robots"]}
    assert [c["total_cost"] for c in payloads[0]["candidates"]] == [4, 8, 12]
    assert [c["total_cost"] for c in payloads[1]["candidates"]] == (
        [14, 8, 4] if second_robot == "robot-3" else [2, 10, 14])
    after = {robot["id"]: robot for robot in payloads[1]["robots"]}
    assert before["robot-1"]["position"] == {"x": 0, "y": 0}
    assert before["robot-1"]["battery"] == 100
    assert after["robot-1"]["position"] == orders[0].dropoff.model_dump()
    assert after["robot-1"]["battery"] == 100 - result.planned_deliveries[0].delivery_plan.total_steps == 96
    assert after["robot-2"] == before["robot-2"] and after["robot-3"] == before["robot-3"]
    assert payloads[1]["robot_forecasts"][0]["order_ids"] == ["o1"]
    assert result.run_outcome == "ready" and result.warehouse == state.warehouse
    assert [(item.order_id, item.robot_id) for item in result.planned_deliveries] == [
        ("o1", "robot-1"), ("o2", second_robot)]
    assert all(item.status == "approved" for item in result.planned_deliveries)
    assert [activity.node for activity in result.node_activity[:8]] == ["order", "fleet", "route", "safety"] * 2
    logs = [record.getMessage() for record in caplog.records if record.name == "app.graph.batch"]
    assert len(logs) == 2
    assert "order='o1'" in logs[0] and "selected='robot-1'" in logs[0]
    assert "order='o2'" in logs[1] and f"selected='{second_robot}'" in logs[1]
    assert "('robot-1', 4, 0, 96, 'idle')" in logs[1]


def test_reported_shared_dropoff_requires_its_occupant_to_leave_before_another_robot_delivers():
    simulation = WarehouseSimulation()
    simulation.create_order("o1", "p1", p(4, 2), p(9, 0))
    simulation.create_order("o2", "p2", p(1, 4), p(9, 0))

    def plan(order_id, robot_id, pickup, delivery, revision):
        return DeliveryPlan(order_id=order_id, robot_id=robot_id, pickup_route=pickup,
                            delivery_route=delivery, total_steps=len(pickup) + len(delivery) - 2,
                            warehouse_revision=revision)

    first = plan("o1", "robot-1",
                 (*[p(x, 0) for x in range(5)], p(4, 1), p(4, 2)),
                 (p(4, 2), p(4, 1), *[p(x, 0) for x in range(4, 10)]), simulation.state.revision)
    projected = batch.project(simulation.state, first)
    executed = simulation.execute_delivery(first)
    assert executed.success and executed.final_state == projected
    assert projected.robots[0].position == p(9, 0) and projected.robots[0].battery == 87

    delivery = (*[p(1, y) for y in reversed(range(5))], *[p(x, 0) for x in range(2, 10)])
    other_robot = plan("o2", "robot-3", (p(0, 2), p(1, 2), p(1, 3), p(1, 4)),
                       delivery, projected.revision)
    # Even an LLM-approved route cannot project two robots onto the same drop-off.
    with pytest.raises(ValueError, match="Robots cannot occupy the same cell"):
        batch.project(projected, other_robot)
    rejected = WarehouseSimulation(projected).execute_delivery(other_robot)
    assert not rejected.success
    assert any(conflict.robot_id == "robot-1" and conflict.cell == p(9, 0)
               for conflict in rejected.validation.conflicts)

    same_robot = plan("o2", "robot-1", tuple(reversed(delivery)), delivery, projected.revision)
    accepted = WarehouseSimulation(projected).execute_delivery(same_robot)
    assert accepted.success and same_robot.total_steps == 24
    assert accepted.final_state.robots[0].battery == 63
