"""Fleet selection against real A*, with optional credential-free model fakes."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.graph.agents import FleetSelection, fleet_agent
from app.graph.state import NodeActivity, OrderSelection, WarehouseGraphState
from app.graph.tools import FleetTools, OrderTools
from app.warehouse import (
    Order, Package, Position, Robot, WarehouseState, plan_delivery, validate_delivery_plan,
)


def make_state(*, batteries=(100, 100, 100), statuses=("idle", "idle", "idle"),
               positions=((0, 0), (0, 2), (0, 4)), blocked=(), pickup=(2, 0), reverse=False):
    robots = tuple(Robot(id=f"r{i + 1}", position=Position(x=xy[0], y=xy[1]),
                         battery=batteries[i], status=statuses[i]) for i, xy in enumerate(positions))
    order = Order(id="o", package=Package(id="p", pickup=Position(x=pickup[0], y=pickup[1])),
                  dropoff=Position(x=9, y=0))
    warehouse = WarehouseState(robots=tuple(reversed(robots)) if reverse else robots,
                               orders=(order,), dropoff_locations=frozenset({order.dropoff}),
                               blocked_cells=frozenset(Position(x=x, y=y) for x, y in blocked))
    return WarehouseGraphState(warehouse=warehouse, command="plan",
                               order_selection=OrderSelection(order_id="o", explanation="Pending"))


class FleetFake(FakeMessagesListChatModel):
    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert schema is FleetSelection and method == "function_calling"
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def fake(output):
    return FleetFake(responses=[AIMessage(content=json.dumps(output))])


def test_one_eligible_robot():
    state = make_state(statuses=("offline", "idle", "busy"))
    update = fleet_agent(state)
    assert update["selected_robot_id"] == "r2" and update["run_outcome"] == "running"
    assert update["delivery_plan"] is update["safety"] is None


def test_multiple_eligible_uses_actual_cost():
    state = make_state()
    tools = FleetTools()
    costs = [tools.evaluate_candidate(state.warehouse, "o", f"r{i}").total_steps for i in (1, 2, 3)]
    assert costs == [9, 11, 13]
    assert fleet_agent(state)["selected_robot_id"] == "r1"


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_cost_tie_break_is_stable(reverse):
    state = make_state(positions=((1, 0), (0, 1), (0, 4)), pickup=(2, 2), reverse=reverse)
    tools = FleetTools()
    assert tools.evaluate_candidate(state.warehouse, "o", "r1").total_steps == tools.evaluate_candidate(state.warehouse, "o", "r2").total_steps
    assert fleet_agent(state)["selected_robot_id"] == "r1"


@pytest.mark.parametrize("status", ["busy", "offline", "charging"])
def test_non_idle_excluded(status):
    state = make_state(statuses=(status, "idle", "idle"))
    result = FleetTools().evaluate_candidate(state.warehouse, "o", "r1")
    assert result.outcome == "unavailable" and result.total_steps is None
    assert fleet_agent(state)["selected_robot_id"] == "r2"


def test_detour_battery_uses_astar_not_manhattan():
    state = make_state(batteries=(9, 100, 100), blocked=((1, 0),))
    result = FleetTools().evaluate_candidate(state.warehouse, "o", "r1")
    assert result.lower_bound == 9 and result.total_steps == 11
    assert result.outcome == "insufficient_battery"
    assert fleet_agent(state)["selected_robot_id"] == "r2"


@pytest.mark.parametrize("battery, expected", [(8, "insufficient_battery"), (9, "eligible")])
def test_exact_battery_boundary(battery, expected):
    state = make_state(batteries=(battery, 0, 0))
    assert FleetTools().evaluate_candidate(state.warehouse, "o", "r1").outcome == expected


def test_zero_step_delivery_can_use_zero_battery():
    state = make_state(batteries=(0, 0, 0), positions=((9, 0), (0, 2), (0, 4)), pickup=(9, 0))
    result = FleetTools().evaluate_candidate(state.warehouse, "o", "r1")
    assert result.outcome == "eligible" and result.total_steps == 0
    assert fleet_agent(state)["selected_robot_id"] == "r1"


def test_unreachable_robot_excluded():
    state = make_state(blocked=((1, 0), (0, 1)))
    assert FleetTools().evaluate_candidate(state.warehouse, "o", "r1").outcome == "unreachable"
    assert fleet_agent(state)["selected_robot_id"] == "r2"


@pytest.mark.parametrize("state", [
    make_state(batteries=(0, 0, 0)),
    make_state(statuses=("busy", "offline", "charging")),
    make_state(blocked=((2, 0),)),
    make_state(blocked=((9, 0),)),
])
def test_no_eligible_is_no_robot_without_model_call(state):
    class MustNotCall(FleetFake):
        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("Should skip model")
    before = state.model_dump_json()
    update = fleet_agent(state, client=MustNotCall(responses=[]))
    assert update["run_outcome"] == "no_robot" and update["selected_robot_id"] is None
    assert update["error_message"] is None
    assert state.model_dump_json() == before and "warehouse" not in update


def test_missing_selected_order_fails_safely():
    state = WarehouseGraphState(warehouse=make_state().warehouse, command="plan")
    update = fleet_agent(state)
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None


def test_injected_model_sees_only_verified_candidates(monkeypatch):
    state = make_state(statuses=("idle", "busy", "idle"), batteries=(100, 100, 0))
    captured = []
    original = FleetFake.with_structured_output
    def structured(self, schema, **kwargs):
        return RunnableLambda(lambda messages: captured.append(messages) or messages) | original(self, schema, **kwargs)
    monkeypatch.setattr(FleetFake, "with_structured_output", structured)
    update = fleet_agent(state, client=fake({"robot_id": "r1", "explanation": "Least cost"}))
    assert update["selected_robot_id"] == "r1"
    data = json.loads(captured[0][1].content)
    assert list(data) == ["eligible_candidates"]
    assert [item["robot_id"] for item in data["eligible_candidates"]] == ["r1"]
    assert data["eligible_candidates"][0]["total_steps"] == 9


@pytest.mark.parametrize("robot_id", ["missing", "r2", "r3"])
def test_model_cannot_select_outside_eligible_set(robot_id):
    state = make_state(statuses=("idle", "busy", "idle"), batteries=(100, 100, 0))
    update = fleet_agent(state, client=fake({"robot_id": robot_id, "explanation": "Claimed eligible"}))
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None
    assert update["error_message"] == "Model selected an ineligible robot"


@pytest.mark.parametrize("state", [make_state(), make_state(positions=((1, 0), (0, 1), (0, 4)), pickup=(2, 2))])
def test_model_cannot_override_cost_or_tie_policy(state):
    update = fleet_agent(state, client=fake({"robot_id": "r2", "explanation": "Choose other"}))
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None
    assert update["error_message"] == "Model violated fleet selection policy"


@pytest.mark.parametrize("output", [{}, {"robot_id": "r1"}, {"robot_id": "r1", "explanation": " "},
    {"robot_id": "r1", "explanation": "OK", "total_steps": 0}, [{"robot_id": "r1", "explanation": "OK"}]])
def test_malformed_model_output(output):
    update = fleet_agent(make_state(), client=fake(output))
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None


@pytest.mark.parametrize("error", [RuntimeError("private error"), TimeoutError("private timeout")])
def test_model_errors(error):
    class Broken(FleetFake):
        def with_structured_output(self, *args, **kwargs):
            def fail(messages):
                raise error
            return RunnableLambda(fail)
    update = fleet_agent(make_state(), client=Broken(responses=[]))
    assert update["run_outcome"] == "failed" and "private" not in update["error_message"]


@pytest.mark.parametrize("method", ["robot_status", "delivery_lower_bound", "evaluate_candidate"])
def test_tool_failures(method, monkeypatch):
    def fail(*args):
        raise RuntimeError("private tool error")
    monkeypatch.setattr(FleetTools, method, fail)
    update = fleet_agent(make_state())
    assert update["run_outcome"] == "failed" and update["error_message"] == "Fleet tool failed"


@pytest.mark.parametrize("fail", [False, True])
def test_exact_field_ownership_and_invalidation(fail):
    initial = make_state()
    plan = plan_delivery(initial.warehouse, "o", "r1")
    record = NodeActivity(node="order", status="completed")
    state = WarehouseGraphState.model_validate({**initial.model_dump(), "command": "execute",
        "execution_requested": True, "selected_robot_id": "r1", "delivery_plan": plan,
        "safety": validate_delivery_plan(initial.warehouse, plan), "planning_outcome": "planned",
        "replan_count": 2, "error_message": "Old error", "node_activity": (record,)})
    update = fleet_agent(state, client=fake({"robot_id": "missing", "explanation": "Invalid"}) if fail else None)
    assert set(update) == {"selected_robot_id", "delivery_plan", "safety", "planning_outcome", "error_message",
                           "execution_requested", "run_outcome", "replan_count", "node_activity"}
    merged = WarehouseGraphState.model_validate({**state.model_dump(), **update})
    assert merged.order_selection == state.order_selection and merged.warehouse == state.warehouse
    assert merged.delivery_plan is merged.safety is None
    assert not merged.execution_requested and merged.replan_count == 0
    assert merged.node_activity[0] == record and merged.node_activity[-1].node == "fleet"
    assert state.safety.route_valid and state.replan_count == 2


def test_toolsets_are_distinct_and_unknown_ids_rejected():
    tools = FleetTools()
    assert not hasattr(tools, "pending_orders") and not hasattr(OrderTools(), "evaluate_candidate")
    with pytest.raises(KeyError):
        tools.robot_status(make_state().warehouse, "missing")
    with pytest.raises(ValueError):
        tools.evaluate_candidate(make_state().warehouse, "missing", "r1")
