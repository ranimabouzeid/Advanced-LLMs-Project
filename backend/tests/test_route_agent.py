"""Route coordinates are supplied by the model, with schema/identity checks only."""
import pytest
from app.graph.agents import route_agent
from app.graph.state import LLMRoutePlan, OrderSelection, WarehouseGraphState
from app.graph.tools import RouteTools
from app.warehouse import Order, Package, Position, Robot, WarehouseState, plan_delivery
from llm_fakes import client


def state_for(*, start=(0, 0), pickup=(2, 0), dropoff=(9, 0), blocked=(), other=(0, 2)):
    pos = lambda xy: Position(x=xy[0], y=xy[1])
    warehouse = WarehouseState(revision=7,
        robots=(Robot(id="r1", position=pos(start)), Robot(id="r2", position=pos(other)),
                Robot(id="r3", position=pos((0, 4)))),
        orders=(Order(id="o", package=Package(id="p", pickup=pos(pickup)), dropoff=pos(dropoff)),),
        dropoff_locations=frozenset({pos(dropoff)}), blocked_cells=frozenset(map(pos, blocked)))
    return WarehouseGraphState(warehouse=warehouse, command="plan", selected_robot_id="r1",
                               order_selection=OrderSelection(order_id="o", explanation="Pending"))



def output(state):
    plan = plan_delivery(state.warehouse, "o", "r1")
    return dict(robot_id="r1", order_id="o", route_to_pickup=plan.model_dump(mode="json")["pickup_route"],
                route_to_dropoff=plan.model_dump(mode="json")["delivery_route"], explanation="Model route")


@pytest.mark.parametrize("start,pickup,dropoff", [((0,0),(2,0),(9,0)), ((2,0),(2,0),(9,0)),
                                               ((0,0),(2,0),(2,0)), ((2,0),(2,0),(2,0))])
def test_typed_route_uses_model_coordinates(start, pickup, dropoff, monkeypatch):
    state = state_for(start=start, pickup=pickup, dropoff=dropoff)
    response = output(state)
    def forbidden(*args, **kwargs):
        raise AssertionError("No deterministic Route generation")
    monkeypatch.setattr("app.warehouse.routing.plan_delivery", forbidden)
    monkeypatch.setattr("app.warehouse.routing.astar_path", forbidden)
    model = client(response)
    update = route_agent(state, client=model)
    assert update["run_outcome"] == "running" and update["safety"] is None
    assert update["delivery_plan"].model_dump(mode="json")["pickup_route"] == response["route_to_pickup"]
    assert model.calls[0][0] is LLMRoutePlan and len(model.calls) == 1
    assert "other_occupied_cells" in model.calls[0][1]
    assert "construct the route only" in model.calls[0][2]


@pytest.mark.parametrize("change", [
    {"order_id": "wrong"}, {"robot_id": "wrong"}, {"route_to_pickup": []},
    {"route_to_dropoff": None}, {"route_to_pickup": [{"x": 10, "y": 0}]},
    {"route_to_pickup": [{"x": 0.5, "y": 0}]}, {"explanation": " "},
    {"route_to_pickup": [{"x": 0, "y": 0}] * 202},
])
def test_malformed_routes_fail_without_fallback(change):
    state = state_for()
    update = route_agent(state, client=client({**output(state), **change}))
    assert update["run_outcome"] == "failed" and update["delivery_plan"] is None


def test_route_does_not_replace_nonadjacent_model_geometry():
    state = state_for()
    data = output(state)
    data["route_to_pickup"] = [{"x": 0, "y": 0}, {"x": 2, "y": 0}]
    update = route_agent(state, client=client(data))
    assert update["run_outcome"] == "running"
    assert update["delivery_plan"].pickup_route == (Position(x=0,y=0), Position(x=2,y=0))


def test_model_reports_unreachable():
    update = route_agent(state_for(), client=client(dict(robot_id="r1", order_id="o",
        outcome="unreachable", route_to_pickup=None, route_to_dropoff=None, explanation="No path")))
    assert update["run_outcome"] == "unreachable" and update["delivery_plan"] is None


@pytest.mark.parametrize("missing", ["order_selection", "selected_robot_id"])
def test_missing_selection_does_not_call_model(missing):
    state = WarehouseGraphState.model_validate({**state_for().model_dump(), missing: None})
    model = client()
    assert route_agent(state, client=model)["run_outcome"] == "failed"
    assert model.calls == []


def test_route_context_lookup_failure_is_sanitized():
    class Broken(RouteTools):
        def grid_context(self, *args):
            raise RuntimeError("private")
    update = route_agent(state_for(), client=client(), tools=Broken())
    assert update["run_outcome"] == "failed" and "private" not in update["error_message"]
