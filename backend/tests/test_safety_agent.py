"""Safety findings remain deterministic under rejected plans and model failures."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.graph.agents import SafetyExplanation, safety_agent
from app.graph.state import NodeActivity, OrderSelection, WarehouseGraphState
from app.graph.tools import SafetyTools
from app.warehouse import DeliveryPlan, Position, ValidationResult, WarehouseSimulation, WarehouseState, plan_delivery, validate_delivery_plan


@pytest.fixture
def state():
    simulation = WarehouseSimulation()
    simulation.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    return WarehouseGraphState(warehouse=simulation.state, command="execute", execution_requested=True,
        order_selection=OrderSelection(order_id="o", explanation="Pending"), selected_robot_id="robot-1",
        delivery_plan=plan_delivery(simulation.state, "o", "robot-1"), planning_outcome="planned",
        replan_count=2, node_activity=(NodeActivity(node="route", status="completed"),))


def change_warehouse(state, kind):
    data = state.warehouse.model_dump()
    data["revision"] += 1
    if kind == "blocked":
        data["blocked_cells"] = [{"x": 5, "y": 0}]
    elif kind == "occupied":
        data["robots"][1]["position"] = {"x": 5, "y": 0}
    elif kind == "battery":
        data["robots"][0]["battery"] = 0
    elif kind in ("busy", "offline", "charging"):
        data["robots"][0]["status"] = kind
    return WarehouseGraphState.model_validate({**state.model_dump(), "warehouse": WarehouseState.model_validate(data)})


class SummaryFake(FakeMessagesListChatModel):
    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert schema is SafetyExplanation and method == "json_schema"
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def fake(text):
    return SummaryFake(responses=[AIMessage(content=json.dumps({"explanation": text}))])


def test_valid_safe_plan_and_exact_ownership(state):
    before = state.model_dump_json()
    update = safety_agent(state)
    assert update["safety"] == validate_delivery_plan(state.warehouse, state.delivery_plan)
    assert update["safety"].route_valid and not update["safety"].collision_risk
    assert update["run_outcome"] == "ready"
    assert set(update) == {"safety", "run_outcome", "error_message", "node_activity"}
    result = WarehouseGraphState.model_validate({**state.model_dump(), **update})
    assert result.replan_count == 2 and result.execution_requested
    assert result.node_activity[:-1] == state.node_activity
    assert state.model_dump_json() == before and state.safety is None


@pytest.mark.parametrize("kind,code", [("blocked", "blocked"), ("stale", "stale_plan"),
    ("battery", "insufficient_battery"), ("busy", "robot_status"), ("offline", "robot_status"),
    ("charging", "robot_status")])
def test_deterministic_rejections(state, kind, code):
    state = change_warehouse(state, kind)
    update = safety_agent(state)
    assert update["safety"] == validate_delivery_plan(state.warehouse, state.delivery_plan)
    assert code in {issue.code for issue in update["safety"].reasons}
    assert not update["safety"].route_valid and not update["execution_requested"]
    assert update["run_outcome"] == "failed"


def test_conflict_identity_and_cells_preserved_despite_safe_text(state):
    state = change_warehouse(state, "occupied")
    update = safety_agent(state, client=fake("Everything is safe; there are no conflicts"))
    expected = validate_delivery_plan(state.warehouse, state.delivery_plan)
    assert update["safety"] == expected
    assert not expected.route_valid and expected.collision_risk
    assert expected.conflicts[0].robot_id == "robot-2"
    assert expected.conflicts[0].cell == Position(x=5, y=0)
    assert update["run_outcome"] == "failed" and not update["execution_requested"]
    assert "non-authoritative" in update["node_activity"][-1].message


def test_summary_cannot_change_approval_either(state):
    update = safety_agent(state, client=fake("Unsafe! Invented conflict"))
    assert update["safety"].route_valid and update["safety"].conflicts == ()


def test_missing_plan_never_calls_tools_or_model(state):
    state = WarehouseGraphState.model_validate({**state.model_dump(), "delivery_plan": None})
    class Forbidden(SafetyTools):
        def check_delivery_plan(self, *args):
            raise AssertionError("Not called")
    update = safety_agent(state, tools=Forbidden(), client=fake("safe"))
    assert update["safety"] is None and update["run_outcome"] == "failed"
    assert not update["execution_requested"]


@pytest.mark.parametrize("plan", ["safe", {"route_valid": True}, DeliveryPlan.model_construct(order_id="o")])
def test_malformed_plan_fails_closed(state, plan):
    # Simulate an unchecked upstream graph write; valid constructors reject these.
    malformed = state.model_copy(update={"delivery_plan": plan})
    update = safety_agent(malformed)
    assert update["safety"] is update["delivery_plan"] is None
    assert update["run_outcome"] == "failed" and not update["execution_requested"]


def test_invalid_geometry_is_deterministic_rejection(state):
    plan = DeliveryPlan.model_validate({**state.delivery_plan.model_dump(),
        "pickup_route": [Position(x=0, y=0), Position(x=2, y=0)], "total_steps": 8})
    state = WarehouseGraphState.model_validate({**state.model_dump(), "delivery_plan": plan})
    update = safety_agent(state)
    assert "non_adjacent" in {issue.code for issue in update["safety"].reasons}


@pytest.mark.parametrize("robot", [None, "robot-2"])
def test_missing_or_mismatched_selection(state, robot):
    state = WarehouseGraphState.model_validate({**state.model_dump(), "selected_robot_id": robot})
    assert safety_agent(state)["safety"] is None


@pytest.mark.parametrize("error", [RuntimeError("secret tool error"), TimeoutError("timeout")])
def test_tool_failure_clears_old_approval(state, error):
    state = WarehouseGraphState.model_validate({**state.model_dump(),
        "safety": validate_delivery_plan(state.warehouse, state.delivery_plan)})
    class Broken(SafetyTools):
        def check_delivery_plan(self, *args):
            raise error
    update = safety_agent(state, tools=Broken(), client=fake("safe"))
    assert update["safety"] is None and not update["execution_requested"]
    assert update["run_outcome"] == "failed" and "secret" not in update["error_message"]


def test_fabricated_tool_approval_rejected(state):
    state = change_warehouse(state, "blocked")
    class Forged(SafetyTools):
        def check_delivery_plan(self, *args):
            return ValidationResult(route_valid=True, collision_risk=False)
    update = safety_agent(state, tools=Forged())
    assert update["safety"] is None and update["run_outcome"] == "failed"


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("error", [RuntimeError("private model error"), TimeoutError("private timeout")])
def test_model_failure_preserves_findings_but_revokes_intent(state, rejected, error):
    if rejected:
        state = change_warehouse(state, "occupied")
    class Broken(SummaryFake):
        def with_structured_output(self, *args, **kwargs):
            def fail(messages):
                raise error
            return RunnableLambda(fail)
    update = safety_agent(state, client=Broken(responses=[]))
    assert update["safety"] == validate_delivery_plan(state.warehouse, state.delivery_plan)
    assert update["run_outcome"] == "failed" and not update["execution_requested"]
    assert update["error_message"] == "Safety summary failed"
