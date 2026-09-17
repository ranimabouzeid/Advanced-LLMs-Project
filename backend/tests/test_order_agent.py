"""Order-only contracts using an injected model and real Pydantic parsing."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.graph.agents import order_agent
from app.graph.state import NodeActivity, OrderSelection, WarehouseGraphState
from app.graph.tools import OrderTools
from app.warehouse import Position, WarehouseSimulation, plan_delivery, validate_delivery_plan


class StructuredFake(FakeMessagesListChatModel):
    """Basic LangChain fakes lack structured output; emulate the parsing boundary."""

    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert schema is OrderSelection
        assert method == "function_calling"
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def fake(output):
    return StructuredFake(responses=[AIMessage(content=json.dumps(output))])


@pytest.fixture
def simulation():
    simulation = WarehouseSimulation()
    simulation.create_order("o1", "p1", Position(x=2, y=0), Position(x=9, y=0))
    return simulation


def state_of(simulation):
    return WarehouseGraphState(warehouse=simulation.state, command="plan")


def test_one_pending_order(simulation):
    state = state_of(simulation)
    before = state.model_dump_json()
    update = order_agent(state, client=fake({"order_id": "o1", "explanation": "First pending order"}))
    result = WarehouseGraphState.model_validate({**state.model_dump(), **update})
    assert result.order_selection.order_id == "o1"
    assert result.order_selection.explanation == "First pending order"
    assert result.run_outcome == "running" and result.error_message is None
    assert result.node_activity[-1].node == "order"
    assert state.model_dump_json() == before
    assert "warehouse" not in update and "command" not in update and "max_replans" not in update


def test_multiple_orders_and_tool_context(simulation, monkeypatch):
    simulation.create_order("o2", "p2", Position(x=1, y=1), Position(x=9, y=9))
    captured = []
    original = StructuredFake.with_structured_output
    def structured(self, schema, **kwargs):
        return RunnableLambda(lambda messages: captured.append(messages) or messages) | original(self, schema, **kwargs)
    monkeypatch.setattr(StructuredFake, "with_structured_output", structured)
    update = order_agent(state_of(simulation), client=fake({"order_id": "o2", "explanation": "Eligible order"}))
    assert update["order_selection"].order_id == "o2"
    context = json.loads(captured[0][1].content)
    assert list(context) == ["pending_orders"]
    assert [order["id"] for order in context["pending_orders"]] == ["o1", "o2"]
    assert "warehouse" not in context and "robots" not in context


def test_no_pending_skips_model():
    class MustNotCall(StructuredFake):
        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("Model must not be configured for no work")
    update = order_agent(state_of(WarehouseSimulation()), client=MustNotCall(responses=[]))
    assert update["run_outcome"] == "no_work"
    assert update["order_selection"] is None and update["error_message"] is None


@pytest.mark.parametrize("status", ["missing", "assigned", "picked_up", "delivered"])
def test_rejects_nonexistent_and_ineligible_ids(simulation, status):
    if status == "delivered":
        assert simulation.execute_delivery(plan_delivery(simulation.state, "o1", "robot-1")).success
    elif status in ("assigned", "picked_up"):
        simulation.assign_order("o1", "robot-1")
        if status == "picked_up":
            simulation.move_robot("robot-1", Position(x=1, y=0))
            simulation.move_robot("robot-1", Position(x=2, y=0))
            simulation.pickup_package("o1", "robot-1")
    simulation.create_order("eligible", "p2", Position(x=1, y=1), Position(x=9, y=9))
    update = order_agent(state_of(simulation), client=fake({"order_id": "missing" if status == "missing" else "o1",
                                                         "explanation": "Select this"}))
    assert update["run_outcome"] == "failed" and update["order_selection"] is None
    assert update["error_message"] == "Model selected an ineligible order"


@pytest.mark.parametrize("output", [
    {}, {"order_id": "o1"}, {"order_id": "o1", "explanation": " "},
    {"order_id": "o1", "explanation": "x" * 301},
    {"order_id": "o1", "explanation": "Valid", "priority": 99},
    [{"order_id": "o1", "explanation": "Multiple"}],
])
def test_malformed_structured_output(simulation, output):
    update = order_agent(state_of(simulation), client=fake(output))
    assert update["run_outcome"] == "failed" and update["order_selection"] is None


@pytest.mark.parametrize("error", [RuntimeError("private provider details"), TimeoutError("private timeout")])
def test_model_errors_are_safe_failures(simulation, error):
    class BrokenModel(StructuredFake):
        def with_structured_output(self, *args, **kwargs):
            def fail(messages):
                raise error
            return RunnableLambda(fail)
    update = order_agent(state_of(simulation), client=BrokenModel(responses=[]))
    assert update["run_outcome"] == "failed" and update["safety"] is None
    assert "private" not in update["error_message"]
    if isinstance(error, TimeoutError):
        assert update["error_message"] == "Order model timed out"


@pytest.mark.parametrize("method", ["pending_orders", "order_metadata"])
def test_lookup_failure(simulation, monkeypatch, method):
    def fail(*args):
        raise RuntimeError("Tool failure")
    monkeypatch.setattr(OrderTools, method, fail)
    update = order_agent(state_of(simulation), client=fake({}))
    assert update["run_outcome"] == "failed" and update["error_message"] == "Order lookup failed"


@pytest.mark.parametrize("valid", [True, False])
def test_ownership_and_stale_state_invalidation(simulation, valid):
    plan = plan_delivery(simulation.state, "o1", "robot-1")
    prior = NodeActivity(node="safety", status="completed")
    state = WarehouseGraphState(warehouse=simulation.state, command="execute", execution_requested=True,
        order_selection=OrderSelection(order_id="o1", explanation="Old choice"), selected_robot_id="robot-1",
        delivery_plan=plan, safety=validate_delivery_plan(simulation.state, plan), planning_outcome="planned",
        replan_count=2, run_outcome="ready", error_message="Old error", node_activity=(prior,))
    update = order_agent(state, client=fake({"order_id": "o1" if valid else "missing", "explanation": "Choice"}))
    assert set(update) == {"order_selection", "selected_robot_id", "delivery_plan", "safety", "planning_outcome",
                           "replan_count", "execution_requested", "run_outcome", "error_message", "node_activity"}
    assert update["selected_robot_id"] is update["delivery_plan"] is update["safety"] is None
    assert update["execution_requested"] is False and update["replan_count"] == 0
    assert update["node_activity"][0] == prior and len(update["node_activity"]) == 2
    assert state.safety.route_valid and state.execution_requested


def test_order_tools_only_read_snapshot(simulation):
    tools = OrderTools()
    before = simulation.state
    assert tools.pending_orders(before) == before.orders
    assert tools.order_metadata(before, "o1") is before.orders[0]
    with pytest.raises(KeyError):
        tools.order_metadata(before, "missing")
    assert simulation.state is before


def test_agent_revalidates_unparsed_injected_output(simulation):
    class UnparsedModel(StructuredFake):
        def with_structured_output(self, *args, **kwargs):
            return RunnableLambda(lambda _: {"order_id": "o1", "explanation": " "})
    update = order_agent(state_of(simulation), client=UnparsedModel(responses=[]))
    assert update["run_outcome"] == "failed"
