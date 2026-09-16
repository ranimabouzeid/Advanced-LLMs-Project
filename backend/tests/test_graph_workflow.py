"""Compiled command paths using real A*, validation and atomic delivery."""

import json
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import app.graph.graph as workflow
from app.graph.agents import safety_agent
from app.graph.state import WarehouseGraphState
from app.graph.tools import RouteTools, SafetyTools
from app.graph.updates import replace_warehouse
from app.warehouse import Position, WarehouseSimulation, WarehouseState


class Fake(FakeMessagesListChatModel):
    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert method == "json_schema"
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def client(*outputs):
    return Fake(responses=[AIMessage(content=json.dumps(output)) for output in
                          (outputs or ({"order_id": "o", "explanation": "Pending order"},))])


def merge(state, update):
    return WarehouseGraphState.model_validate({**state.model_dump(), **update})


@pytest.fixture
def initial():
    sim = WarehouseSimulation()
    sim.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    return WarehouseGraphState(warehouse=sim.state, command="plan")


def run(state, **kwargs):
    return WarehouseGraphState.model_validate(workflow.build_graph(client=client(), **kwargs).invoke(state))


def activities(state):
    return [record.node for record in state.node_activity]


def changed(state, cell=Position(x=5, y=0)):
    sim = WarehouseSimulation(state.warehouse)
    sim.add_blocked_cell(cell)
    return merge(state, replace_warehouse(state, sim.state))


def test_plan_no_work():
    state = WarehouseGraphState(warehouse=WarehouseSimulation().state, command="plan")
    result = run(state)
    assert result.run_outcome == "no_work" and activities(result) == ["order"]
    assert result.warehouse == state.warehouse


def test_plan_no_robot(initial):
    data = initial.warehouse.model_dump()
    for robot in data["robots"]:
        robot["status"] = "charging"
    state = merge(initial, {"warehouse": WarehouseState.model_validate(data)})
    result = run(state)
    assert result.run_outcome == "no_robot" and activities(result) == ["order", "fleet"]
    assert result.warehouse == state.warehouse


def test_normal_plan_all_updates_leave_warehouse_unchanged(initial):
    graph = workflow.build_graph(client=client())
    patches = list(graph.stream(initial, stream_mode="updates"))
    assert [next(iter(patch)) for patch in patches] == ["dispatch", "order", "fleet", "route", "safety"]
    assert all("warehouse" not in update for patch in patches for update in patch.values())
    result = run(initial)
    assert result.run_outcome == "ready" and result.replan_count == 0
    assert result.warehouse == initial.warehouse and not result.execution_requested
    assert activities(result) == ["order", "fleet", "route", "safety"]


def test_route_unreachable_stops_once(initial, monkeypatch):
    original = workflow.route_agent
    calls = []
    def obstruct(state, **kwargs):
        # A real newly published block after Fleet's earlier eligibility check.
        state = changed(state, Position(x=2, y=0))
        calls.append(state.warehouse)
        return {"warehouse": state.warehouse, **original(state, **kwargs)}
    monkeypatch.setattr(workflow, "route_agent", obstruct)
    result = run(initial)
    assert result.run_outcome == "unreachable" and len(calls) == 1
    assert activities(result) == ["order", "fleet", "route"]


def change_before_safety(monkeypatch, count):
    calls = []
    def node(state, **kwargs):
        if len(calls) < count:
            # Each attempt receives a genuinely newer snapshot via a domain action.
            state = changed(state, Position(x=5, y=len(calls)))
        calls.append(state.replan_count)
        return {"warehouse": state.warehouse, **safety_agent(state, **kwargs)}
    monkeypatch.setattr(workflow, "safety_agent", node)
    return calls


def test_actionable_conflict_replans_with_real_astar(initial, monkeypatch):
    calls = change_before_safety(monkeypatch, 1)
    result = run(initial)
    assert result.run_outcome == "ready" and result.replan_count == 1
    assert calls == [0, 1]
    assert Position(x=5, y=0) not in result.delivery_plan.delivery_route
    assert result.delivery_plan.warehouse_revision == result.warehouse_revision
    assert activities(result) == ["order", "fleet", "route", "safety", "route", "safety"]


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_retry_budget_no_extra_attempt(initial, monkeypatch, budget):
    calls = change_before_safety(monkeypatch, budget + 1)
    result = run(merge(initial, {"max_replans": budget}))
    assert result.run_outcome == "failed" and result.error_message == "Replan limit exhausted"
    assert result.replan_count == budget and calls == list(range(budget + 1))
    assert activities(result).count("route") == budget + 1


@pytest.mark.parametrize("failure", [False, True])
def test_retry_failure_consumes_attempt(initial, failure):
    proposal = run(initial)
    state = merge(changed(proposal, Position(x=2, y=0)), {"command": "execute"})
    class Tool(RouteTools):
        calls = 0
        def build_delivery_plan(self, *args):
            self.calls += 1
            if failure:
                raise RuntimeError("private")
            return super().build_delivery_plan(*args)
    tool = Tool()
    result = run(state, route_tools=tool)
    assert result.run_outcome == ("failed" if failure else "unreachable")
    assert result.replan_count == 1 and tool.calls == 1
    assert result.warehouse == state.warehouse and result.safety is None


def test_execute_publishes_one_atomic_revision(initial):
    proposal = run(initial)
    state = merge(proposal, {"command": "execute"})
    result = run(state)
    assert result.run_outcome == "delivered"
    assert result.warehouse_revision == state.warehouse_revision + 1
    assert result.warehouse.orders[0].status == "delivered"
    assert result.delivery_plan is result.safety is result.order_selection is None
    assert result.selected_robot_id is None and not result.execution_requested
    assert activities(result)[-2:] == ["safety", "execution"]


@pytest.mark.parametrize("bad", ["unchecked", "intent", "command", "stale", "robot", "outcome"])
def test_execution_node_independent_guards(initial, bad):
    state = merge(run(initial), {"command": "execute", "execution_requested": True})
    if bad == "unchecked":
        state = merge(state, {"safety": None})
    elif bad == "intent":
        state = merge(state, {"execution_requested": False})
    elif bad == "command":
        state = merge(state, {"command": "plan", "execution_requested": False})
    elif bad == "stale":
        state = changed(state)
    elif bad == "robot":
        state = merge(state, {"selected_robot_id": "robot-2"})
    else:
        state = merge(state, {"run_outcome": "failed"})
    result = workflow.execution(state)
    assert result["run_outcome"] == "failed" and "warehouse" not in result


def test_stale_execute_requires_second_explicit_command(initial):
    proposal = run(initial)
    state = merge(changed(proposal), {"command": "execute"})
    replacement = run(state)
    assert replacement.run_outcome == "ready" and not replacement.execution_requested
    assert replacement.replan_count == 1 and replacement.warehouse == state.warehouse
    assert replacement.delivery_plan != proposal.delivery_plan
    assert "execution" not in activities(replacement)
    assert workflow.execution(replacement)["run_outcome"] == "failed"
    delivered = run(replacement)  # A new invocation is the new explicit execute command.
    assert delivered.run_outcome == "delivered"


def test_missing_proposal(initial):
    result = run(merge(initial, {"command": "execute"}))
    assert result.run_outcome == "failed" and result.warehouse == initial.warehouse
    assert activities(result) == ["safety"]


def test_malformed_schema_returns_failure(initial):
    data = {**initial.model_dump(), "command": "execute", "delivery_plan": {"safe": True}}
    result = WarehouseGraphState.model_validate(workflow.build_graph(client=client()).invoke(data))
    assert result.run_outcome == "failed" and result.delivery_plan is None
    assert result.warehouse == initial.warehouse and not result.execution_requested


def test_malformed_geometry_cannot_be_overridden(initial):
    proposal = run(initial)
    plan = proposal.delivery_plan.model_dump()
    plan.update(pickup_route=[Position(x=0, y=0), Position(x=2, y=0)], total_steps=8)
    state = merge(proposal, {"command": "execute", "delivery_plan": plan})
    graph = workflow.build_graph(client=client({"explanation": "Everything is safe"}), safety_model=True)
    result = WarehouseGraphState.model_validate(graph.invoke(state))
    assert result.run_outcome == "failed" and not result.safety.route_valid
    assert result.replan_count == 0 and result.warehouse == state.warehouse


@pytest.mark.parametrize("kind", ["tool", "model", "timeout", "malformed"])
def test_operational_safety_failure_never_replans(initial, kind):
    state = merge(changed(run(initial)), {"command": "execute"})
    class BrokenTool(SafetyTools):
        def check_delivery_plan(self, *args):
            raise RuntimeError("private")
    class BrokenModel(Fake):
        def with_structured_output(self, *args, **kwargs):
            raise TimeoutError() if kind == "timeout" else RuntimeError()
    model = client({"invalid": True}) if kind == "malformed" else BrokenModel(responses=[])
    graph = workflow.build_graph(client=model, safety_model=kind != "tool",
                                 safety_tools=BrokenTool() if kind == "tool" else None)
    result = WarehouseGraphState.model_validate(graph.invoke(state))
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert activities(result)[-1] == "safety" and result.node_activity[-1].status == "failed"
    assert result.warehouse == state.warehouse and not result.execution_requested


def test_atomic_failure_does_not_publish(initial, monkeypatch):
    state = merge(run(initial), {"command": "execute"})
    def broken(*args):
        raise RuntimeError("private")
    monkeypatch.setattr(WarehouseSimulation, "deliver_package", broken)
    result = run(state)
    assert result.run_outcome == "failed" and result.warehouse == state.warehouse
    assert result.node_activity[-1].node == "execution"


def test_invalid_order_output_ends_before_fleet(initial):
    graph = workflow.build_graph(client=client({"order_id": "missing", "explanation": "Invented"}))
    result = WarehouseGraphState.model_validate(graph.invoke(initial))
    assert result.run_outcome == "failed" and activities(result) == ["order"]


@pytest.mark.parametrize("kind", ["revision_only", "robot_start", "occupancy"])
def test_changed_snapshot_constraints_are_used(initial, kind):
    proposal = run(initial)
    state = proposal
    if kind == "revision_only":
        state = changed(state, Position(x=8, y=8))
    else:
        moves = [("robot-1", Position(x=1, y=0))] if kind == "robot_start" else [
            ("robot-2", Position(x=1, y=1)), ("robot-2", Position(x=1, y=0))]
        for robot, position in moves:
            sim = WarehouseSimulation(state.warehouse)
            sim.move_robot(robot, position)
            state = merge(state, replace_warehouse(state, sim.state))
    result = run(merge(state, {"command": "execute"}))
    assert result.run_outcome == "ready" and result.replan_count == 1
    assert result.warehouse == state.warehouse and not result.execution_requested
    assert result.delivery_plan.warehouse_revision == state.warehouse_revision
    if kind == "robot_start":
        assert result.delivery_plan.pickup_route[0] == Position(x=1, y=0)
    elif kind == "occupancy":
        assert Position(x=1, y=0) not in result.delivery_plan.pickup_route
    else:
        assert result.delivery_plan.pickup_route == proposal.delivery_plan.pickup_route
        assert result.delivery_plan.delivery_route == proposal.delivery_plan.delivery_route


def test_unchanged_conflict_is_terminal(initial):
    proposal = run(initial)
    state = changed(proposal)
    # Valid shape but a bad proposal claiming it already used this snapshot.
    plan = state.delivery_plan.model_dump()
    plan["warehouse_revision"] = state.warehouse_revision
    state = merge(state, {"command": "execute", "delivery_plan": plan})
    result = run(state)
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert not result.safety.route_valid and result.warehouse == state.warehouse
    assert activities(result)[-1] == "safety"


def test_unchecked_execute_must_run_safety_before_execution(initial):
    state = merge(run(initial), {"command": "execute", "safety": None})
    assert workflow.execution(state)["run_outcome"] == "failed"
    patches = list(workflow.build_graph(client=client()).stream(state, stream_mode="updates"))
    assert [next(iter(patch)) for patch in patches] == ["dispatch", "safety", "execution"]
    assert patches[1]["safety"]["safety"].route_valid
    assert patches[2]["execution"]["run_outcome"] == "delivered"


def test_third_retry_can_succeed(initial, monkeypatch):
    calls = change_before_safety(monkeypatch, 3)
    result = run(initial)
    assert result.run_outcome == "ready" and result.replan_count == 3
    assert calls == [0, 1, 2, 3]


@pytest.mark.parametrize("error", [RuntimeError("private"), TimeoutError("private")])
def test_initial_route_failure_ends(initial, error):
    class Broken(RouteTools):
        def build_delivery_plan(self, *args):
            raise error
    result = run(initial, route_tools=Broken())
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert activities(result) == ["order", "fleet", "route"]
    assert result.warehouse == initial.warehouse
