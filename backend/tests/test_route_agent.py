"""Groq intent precedes trusted A* geometry, including retries and continuations."""
import pytest
from app.graph.agents import route_agent
from app.graph.state import RouteIntent, OrderSelection, WarehouseGraphState
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



def output(state, **changes):
    robot = next(r for r in state.warehouse.robots if r.id == state.selected_robot_id)
    return dict(robot_id=robot.id, order_id=state.order_selection.order_id,
                route_type="continuation" if robot.position in state.warehouse.dropoff_locations else "delivery",
                explanation="Use trusted endpoints and shortest A* path", **changes)


@pytest.mark.parametrize("start,pickup,dropoff", [((0,0),(2,0),(9,0)), ((2,0),(2,0),(9,0)),
                                               ((0,0),(2,0),(2,0)), ((2,0),(2,0),(2,0))])
def test_groq_intent_then_exact_astar_routes(start, pickup, dropoff):
    state = state_for(start=start, pickup=pickup, dropoff=dropoff)
    expected = plan_delivery(state.warehouse, "o", "r1")
    model = client(output(state))
    update = route_agent(state, client=model)
    assert update["run_outcome"] == "running" and update["safety"] is None
    assert update["delivery_plan"] == expected
    assert model.calls[0][0] is RouteIntent and len(model.calls) == 1
    assert "other_occupied_cells" in model.calls[0][1]
    assert "never exact coordinate paths" in model.calls[0][2]


@pytest.mark.parametrize("change", [
    {"order_id": "wrong"}, {"robot_id": "wrong"}, {"route_type": "parking"},
    {"avoid_cells": [{"x": 10, "y": 0}]}, {"avoid_cells": [{"x": 0.5, "y": 0}]},
    {"explanation": " "}, {"route_to_pickup": [{"x": 0, "y": 0}, {"x": 2, "y": 0}]},
    {"retry_feedback_acknowledged": "true"},
])
def test_invalid_intent_fails_without_deterministic_fallback(change):
    state = state_for()
    update = route_agent(state, client=client({**output(state), **change}))
    assert update["run_outcome"] == "failed" and update["delivery_plan"] is None


def test_astar_avoids_blocked_cells_and_obeys_intent_constraints():
    state = state_for(blocked=((1, 0),), other=(0, 3))
    model = client(output(state, avoid_cells=[{"x": 1, "y": 1}]))
    update = route_agent(state, client=model)
    plan = update["delivery_plan"]
    assert plan.total_steps == 13  # Pickup detours through y=2, then delivery along y=0.
    assert Position(x=1, y=0) not in plan.pickup_route
    assert Position(x=1, y=1) not in plan.pickup_route
    assert all(abs(a.x-b.x) + abs(a.y-b.y) == 1 for a,b in zip(plan.pickup_route, plan.pickup_route[1:]))


def test_astar_reports_unreachable_after_calling_groq():
    model = client()
    update = route_agent(state_for(blocked=((2, 0),)), client=model)
    assert update["run_outcome"] == "unreachable" and update["delivery_plan"] is None
    assert len(model.calls) == 1


def test_retry_feedback_reaches_groq_and_recomputes_astar(monkeypatch):
    from app.graph import tools
    from app.graph.state import SafetyDecision
    original = tools.astar_path
    calls = []
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(tools, "astar_path", counted)
    state = state_for()
    first = route_agent(state, client=client())["delivery_plan"]
    retry_state = WarehouseGraphState.model_validate({**state.model_dump(), "delivery_plan": first,
        "safety": SafetyDecision(approved=False, conflicts=["Avoid (1,0) for revised routing"], explanation="Use another corridor")})
    model = client(output(state, avoid_cells=[{"x": 1, "y": 0}], retry_feedback_acknowledged=True))
    updated = route_agent(retry_state, client=model)
    assert updated["run_outcome"] == "running" and updated["delivery_plan"] != first
    assert len(calls) == 4
    assert model.calls[0][1]["safety_feedback"]["conflicts"] == ["Avoid (1,0) for revised routing"]
    assert model.calls[0][1]["previous_route"] == first.model_dump(mode="json")
    # Refusing to acknowledge feedback fails instead of reusing the old route.
    assert route_agent(retry_state, client=client(output(state)))["delivery_plan"] is None


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


def test_route_provider_failure_does_not_run_astar(monkeypatch):
    from app.graph import tools
    def forbidden(*args):
        raise AssertionError("No planning without valid Groq intent")
    monkeypatch.setattr(tools, "astar_path", forbidden)
    result = route_agent(state_for(), client=client({}))
    assert result["run_outcome"] == "failed" and result["delivery_plan"] is None
