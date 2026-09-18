"""Compiled command paths using mocked LLM decisions and real atomic delivery."""

import pytest

import app.graph.graph as workflow
from app.graph import batch
from pydantic import ValidationError
from app.graph.state import WarehouseGraphState
from app.graph.tools import RouteTools, SafetyTools
from app.graph.updates import replace_warehouse
from app.warehouse import Position, WarehouseSimulation, WarehouseState


from llm_fakes import Fake, client


def merge(state, update):
    return WarehouseGraphState.model_validate({**state.model_dump(), **update})


@pytest.fixture
def initial():
    sim = WarehouseSimulation()
    sim.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    return WarehouseGraphState(warehouse=sim.state, command="plan")


def run(state, model=None, **kwargs):
    return WarehouseGraphState.model_validate(workflow.build_graph(client=model or client(), **kwargs).invoke(state))


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
    assert [next(iter(patch)) for patch in patches] == ["dispatch", "order", "fleet", "route", "safety", "collect", "finish"]
    assert all("warehouse" not in update for patch in patches for update in patch.values())
    result = run(initial)
    assert result.run_outcome == "ready" and result.replan_count == 0
    assert result.warehouse == initial.warehouse and not result.execution_requested
    assert activities(result) == ["order", "fleet", "route", "safety", "route", "safety", "route", "safety"]


def test_route_unreachable_stops_once(initial):
    class Unreachable(RouteTools):
        def plan_delivery(self, *args):
            return None
    result = run(initial, route_tools=Unreachable())
    assert result.run_outcome == "unreachable"
    assert result.planned_deliveries[0].status == "unplannable"
    assert result.warehouse == initial.warehouse


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_retry_budget_no_extra_attempt(initial, budget):
    state = run(merge(initial, {"max_replans": budget}))
    for attempt in range(budget):
        state = run(merge(changed(state, Position(x=8, y=attempt + 1)), {"command": "execute"}))
        assert state.run_outcome == "ready" and state.replan_count == attempt + 1
        assert not state.execution_requested
    previous = changed(state, Position(x=8, y=5))
    result = run(merge(previous, {"command": "execute"}))
    assert result.run_outcome == "failed" and result.error_message == "Replan limit exhausted"
    assert result.replan_count == budget and result.warehouse == previous.warehouse


@pytest.mark.parametrize("failure", [False, True])
def test_retry_failure_consumes_attempt(initial, failure):
    state = merge(changed(run(initial)), {"command": "execute"})
    class Tool(RouteTools):
        calls = 0
        def plan_delivery(self, *args):
            self.calls += 1
            if failure:
                raise RuntimeError("private")
            return None
    tool = Tool()
    result = run(state, route_tools=tool)
    assert result.run_outcome == ("failed" if failure else "unreachable")
    assert result.replan_count == 1 and tool.calls == 1
    assert result.warehouse == state.warehouse and result.safety is None


def test_execute_publishes_delivery_and_parking_revisions(initial):
    proposal = run(initial)
    state = merge(proposal, {"command": "execute"})
    result = run(state)
    assert result.run_outcome == "delivered"
    assert result.warehouse_revision == state.warehouse_revision + 2
    assert result.warehouse.orders[0].status == "delivered"
    assert result.delivery_plan is result.safety is result.order_selection is None
    assert result.selected_robot_id is None and not result.execution_requested
    assert activities(result)[-2:] == ["safety", "execution"]


@pytest.mark.parametrize("bad", ["intent", "command", "stale", "outcome"])
def test_execution_node_independent_guards(initial, bad):
    state = merge(run(initial), {"command": "execute", "execution_requested": True})
    if bad == "intent":
        state = merge(state, {"execution_requested": False})
    elif bad == "command":
        state = merge(state, {"command": "plan", "execution_requested": False})
    elif bad == "stale":
        state = changed(state)
    else:
        state = merge(state, {"run_outcome": "failed"})
    result = batch.execution(state)
    assert result["run_outcome"] == "failed" and "warehouse" not in result


def test_stale_execute_requires_second_explicit_command(initial):
    proposal = run(initial)
    state = merge(changed(proposal), {"command": "execute"})
    replacement = run(state)
    assert replacement.run_outcome == "ready" and not replacement.execution_requested
    assert replacement.replan_count == 1 and replacement.warehouse == state.warehouse
    assert replacement.delivery_plan != proposal.delivery_plan
    assert "execution" not in activities(replacement)
    assert batch.execution(replacement)["run_outcome"] == "failed"
    delivered = run(replacement)  # A new invocation is the new explicit execute command.
    assert delivered.run_outcome == "delivered"


def test_missing_proposal(initial):
    result = run(merge(initial, {"command": "execute"}))
    assert result.run_outcome == "failed" and result.warehouse == initial.warehouse
    assert activities(result) == ["safety"]


def test_malformed_schema_is_rejected_before_dispatch(initial):
    data = {**initial.model_dump(), "command": "execute", "delivery_plan": {"safe": True}}
    with pytest.raises(ValidationError):
        workflow.build_graph(client=client()).invoke(data)


def test_malformed_geometry_cannot_be_overridden(initial):
    proposal = run(initial)
    plan = proposal.delivery_plan.model_dump()
    plan.update(pickup_route=[Position(x=0, y=0), Position(x=2, y=0)], total_steps=8)
    records = [item.model_dump() for item in proposal.planned_deliveries]
    records[0]["delivery_plan"] = plan
    state = merge(proposal, {"command": "execute", "planned_deliveries": records})
    graph = workflow.build_graph(client=client(scripts={"SafetyDecision": [{"approved": True, "conflicts": [], "explanation": "Everything is safe"}]}))
    result = WarehouseGraphState.model_validate(graph.invoke(state))
    assert result.run_outcome == "failed" and not result.execution_requested
    assert result.replan_count == 0 and result.warehouse == state.warehouse
    assert any(a.node == "safety" and a.status == "rejected" for a in result.node_activity)


@pytest.mark.parametrize("kind", ["tool", "model", "timeout", "malformed"])
def test_operational_safety_failure_never_replans(initial, kind):
    state = merge(changed(run(initial)), {"command": "execute"})
    class BrokenTool(SafetyTools):
        def current_context(self, *args):
            raise RuntimeError("private")
    class BrokenModel(Fake):
        def with_structured_output(self, *args, **kwargs):
            raise TimeoutError() if kind == "timeout" else RuntimeError()
    model = client({"invalid": True}) if kind == "malformed" else BrokenModel(responses=[])
    graph = workflow.build_graph(client=model,
                                 safety_tools=BrokenTool() if kind == "tool" else None)
    result = WarehouseGraphState.model_validate(graph.invoke(state))
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert activities(result)[-1] == "safety" and result.node_activity[-1].status == "failed"
    assert result.warehouse == state.warehouse and not result.execution_requested


def test_atomic_failure_does_not_publish(initial, monkeypatch):
    state = merge(run(initial), {"command": "execute"})
    from app.warehouse.simulation import DeliveryExecutionResult
    monkeypatch.setattr(batch, "apply_delivery", lambda *args: DeliveryExecutionResult(
        success=False, failed_stage="delivery", error="Atomic failure"))
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
        assert result.selected_robot_id == "robot-2"
        assert result.delivery_plan.pickup_route[0] == Position(x=1, y=0)
    else:
        assert result.delivery_plan.pickup_route == proposal.delivery_plan.pickup_route
        assert result.delivery_plan.delivery_route == proposal.delivery_plan.delivery_route


def test_rejected_route_is_replaced_for_review(initial):
    proposal = run(initial)
    state = changed(proposal)
    # Valid shape but a bad proposal claiming it already used this snapshot.
    plan = state.delivery_plan.model_dump()
    plan["warehouse_revision"] = state.warehouse_revision
    records = [item.model_dump() for item in state.planned_deliveries]
    records[0]["delivery_plan"] = plan
    state = merge(state, {"command": "execute", "planned_deliveries": records})
    result = run(state)
    assert result.run_outcome == "ready" and result.replan_count == 1
    assert result.safety.approved and result.warehouse == state.warehouse
    assert not result.execution_requested
    assert run(result).run_outcome == "delivered"


def test_unchecked_execute_must_run_safety_before_execution(initial):
    state = merge(run(initial), {"command": "execute", "safety": None})
    patches = list(workflow.build_graph(client=client()).stream(state, stream_mode="updates"))
    assert [next(iter(patch)) for patch in patches] == ["dispatch", "safety", "execution"]
    assert patches[1]["safety"]["safety"].approved
    assert patches[2]["execution"]["run_outcome"] == "delivered"


@pytest.mark.parametrize("error", [RuntimeError("private"), TimeoutError("private")])
def test_initial_route_failure_ends(initial, error):
    class Broken(RouteTools):
        def plan_delivery(self, *args):
            raise error
    result = run(initial, route_tools=Broken())
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert activities(result) == ["order", "fleet", "route"]
    assert result.warehouse == initial.warehouse
