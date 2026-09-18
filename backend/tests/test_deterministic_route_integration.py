"""API/checkpoint regressions for deterministic Route with hybrid Fleet and Safety."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import LLMSettings, create_model_client
from app.graph.state import WarehouseGraphState
from app.sessions import SessionCoordinator
from app.warehouse import Position, Robot, WarehouseState
from llm_fakes import client
from test_batch import seed_session


def p(x, y):
    return Position(x=x, y=y)


def test_three_order_api_plan_execute_uses_fresh_hybrid_fleet_and_zero_route_model_calls(monkeypatch):
    # The actual ChatGroq parser is used, but its HTTP completion boundary is mocked.
    model = create_model_client(LLMSettings(model="offline", api_key="test-placeholder"))
    requests = []
    choices = {"o1": "robot-1", "o2": "robot-3", "o3": "robot-1"}

    def completion(**kwargs):
        name = kwargs["tool_choice"]["function"]["name"]
        data = json.loads(kwargs["messages"][-1]["content"])
        requests.append((name, data))
        assert name in {"OrderSelection", "FleetSelection", "SafetyDecision"}
        if name == "OrderSelection":
            output = dict(order_id=data["pending_orders"][0]["id"], explanation="Next order")
        elif name == "FleetSelection":
            output = dict(robot_id=choices[data["selected_order"]["id"]], explanation="Supplied minimum")
        else:
            assert data["trusted_findings"]["route_valid"]
            output = dict(approved=True, conflicts=[], explanation="Hard checks pass")
        return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": f"mock-{len(requests)}",
                "type": "function", "function": {"name": name, "arguments": json.dumps(output)}}]}}],
                "model": "offline"}

    monkeypatch.setattr(model.client, "create", completion)
    service = SessionCoordinator(client=model)
    warehouse = WarehouseState(robots=tuple(Robot(id=f"robot-{i+1}", position=p(0, i*4)) for i in range(3)),
                               dropoff_locations=frozenset({p(4, 0)}))
    sid = seed_session(service, WarehouseGraphState(warehouse=warehouse, command="plan"))
    with TestClient(create_app(coordinator=service), raise_server_exceptions=False) as http:
        for i, pickup in enumerate((p(1, 0), p(1, 8), p(5, 0)), 1):
            response = http.post(f"/api/sessions/{sid}/orders", json=dict(
                order_id=f"o{i}", package_id=f"p{i}", pickup=pickup.model_dump(), dropoff=p(4, 0).model_dump()))
            assert response.status_code == 201
        before = service.get_state(sid).warehouse
        response = http.post(f"/api/sessions/{sid}/plan")
        assert response.status_code == 200, response.text
        ready = WarehouseGraphState.model_validate(response.json()["state"])
        assert ready.run_outcome == "ready" and ready.planning_outcome == "planned"
        assert ready.warehouse == before and service.get_state(sid) == ready
        records = {item.order_id: item for item in ready.planned_deliveries}
        assert {oid: item.robot_id for oid, item in records.items()} == choices
        assert ready.robot_schedules[0].order_ids == ("o1", "o3")
        assert ready.robot_schedules[1].order_ids == ("o2",)
        assert records["o3"].delivery_plan.pickup_route[0] == records["o1"].delivery_plan.delivery_route[-1] == p(4, 0)
        assert records["o3"].delivery_plan.warehouse_revision == records["o1"].delivery_plan.warehouse_revision + 1
        assert ready.robot_schedules[0].parking.plan.warehouse_revision == records["o3"].delivery_plan.warehouse_revision + 1
        assert records["o2"].delivery_plan.warehouse_revision == ready.robot_schedules[0].parking.plan.warehouse_revision + 1
        assert len({s.parking.plan.route[-1] for s in ready.robot_schedules}) == 2

        fleet = [data for name, data in requests if name == "FleetSelection"]
        assert len(fleet) == 3 and all(len(data["candidates"]) == 3 for data in fleet)
        assert [[c["total_cost"] for c in data["candidates"]] for data in fleet] == [
            [4, 8, 12], [22, 16, 12], [2, 10, 2]]
        assert fleet[1]["robots"][0]["position"] == p(4, 0).model_dump()
        assert fleet[1]["robots"][0]["battery"] == 96
        assert fleet[2]["robots"][2]["position"] == p(4, 0).model_dump()
        assert fleet[2]["robots"][2]["battery"] == 88
        for data in fleet:
            oid = data["selected_order"]["id"]
            candidate = next(c for c in data["candidates"] if c["robot"]["id"] == choices[oid])
            plan = records[oid].delivery_plan
            assert plan.pickup_route[0].model_dump() == candidate["robot"]["position"]
            assert (len(plan.pickup_route)-1, len(plan.delivery_route)-1, plan.total_steps) == (
                candidate["pickup_cost"], candidate["delivery_cost"], candidate["total_cost"])
        assert [name for name, _ in requests].count("SafetyDecision") == 8  # 3 previews, 3 finalized, 2 parking.

        response = http.post(f"/api/sessions/{sid}/execute")
        assert response.status_code == 200 and response.json()["outcome"] == "delivered"
        committed = service.get_state(sid)
        assert committed.warehouse.revision == before.revision + 5
        assert all(o.status == "delivered" and o.dropoff == p(4, 0) for o in committed.warehouse.orders)
        for schedule in ready.robot_schedules:
            robot = next(r for r in committed.warehouse.robots if r.id == schedule.robot_id)
            assert robot == schedule.projected_robot and robot.position in before.parking_cells
            assert robot.battery == 100 - sum(records[oid].delivery_plan.total_steps for oid in schedule.order_ids) - schedule.parking.plan.total_steps
        assert all(r.position != p(4, 0) for r in committed.warehouse.robots)


@pytest.mark.parametrize("stage", ["pickup", "delivery", "parking", "no_parking", "parking_safety"])
def test_expected_routing_failures_are_typed_api_results_not_500(stage):
    model = client()
    service = SessionCoordinator(client=model)
    sid = service.create_session().session_id
    service.create_order(sid, "o", "pkg", p(2, 0), p(9, 0))
    cells = {"pickup": [p(2, 0)], "delivery": [p(9, 0)],
             "parking": [p(6, 9), p(8, 9), p(7, 8)],
             "no_parking": list(service.get_state(sid).warehouse.parking_cells), "parking_safety": []}[stage]
    for cell in cells:
        service.add_blocked_cell(sid, cell)
    if stage == "parking_safety":
        yes = dict(approved=True, conflicts=[], explanation="Approved")
        model.scripts["SafetyDecision"] = [yes, yes, dict(approved=False, conflicts=["Corridor concern"], explanation="Review corridor")]
    before = service.get_state(sid).warehouse
    with TestClient(create_app(coordinator=service), raise_server_exceptions=False) as http:
        response = http.post(f"/api/sessions/{sid}/plan")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == ("failed" if stage == "parking_safety" else "unreachable")
        assert result["state"]["robot_schedules"] == []
        assert all(item["status"] == "unplannable" for item in result["state"]["planned_deliveries"])
        assert all(item["status"] != "failed" for item in result["state"]["node_activity"])
        assert service.get_state(sid).warehouse == before
        assert not service.get_state(sid).execution_requested
