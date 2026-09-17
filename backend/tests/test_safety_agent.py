"""Safety is a structured LLM decision; deterministic integrity belongs to execution."""
import pytest
from langchain_core.runnables import RunnableLambda
from app.graph.agents import safety_agent
from app.graph.state import OrderSelection, SafetyDecision, WarehouseGraphState
from app.graph.tools import SafetyTools
from app.warehouse import Position, WarehouseSimulation, plan_delivery
from llm_fakes import client, Fake


@pytest.fixture
def state():
    sim = WarehouseSimulation()
    sim.create_order("o", "p", Position(x=2,y=0), Position(x=9,y=0))
    return WarehouseGraphState(warehouse=sim.state, command="execute", execution_requested=True,
        order_selection=OrderSelection(order_id="o", explanation="Chosen"), selected_robot_id="robot-1",
        delivery_plan=plan_delivery(sim.state, "o", "robot-1"))


@pytest.mark.parametrize("approved", [True, False])
def test_model_decides_approval_without_validator(state, approved, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Safety must not call deterministic validation")
    monkeypatch.setattr("app.warehouse.validation.validate_delivery_plan", forbidden)
    model = client(dict(approved=approved, conflicts=[] if approved else ["Model sees a conflict"],
                        explanation="Model safety reasoning"))
    update = safety_agent(state, client=model)
    assert update["safety"].approved is approved
    assert update["run_outcome"] == ("ready" if approved else "failed")
    assert model.calls[0][0] is SafetyDecision and len(model.calls) == 1
    assert model.calls[0][1]["delivery_plan"] == state.delivery_plan.model_dump(mode="json")
    assert "evaluate safety only" in model.calls[0][2]
    if not approved:
        assert update["execution_requested"] is False


def test_model_approval_is_not_replaced_even_for_an_unsafe_route(state):
    data = state.model_dump()
    data["delivery_plan"]["pickup_route"] = [{"x":0,"y":0},{"x":2,"y":0}]
    data["delivery_plan"]["total_steps"] = 8
    state = WarehouseGraphState.model_validate(data)
    update = safety_agent(state, client=client(dict(approved=True, conflicts=[], explanation="Model approves")))
    assert update["safety"].approved and update["run_outcome"] == "ready"
    # The same proposal remains invalid for actual simulation execution.
    assert not WarehouseSimulation(state.warehouse).execute_delivery(state.delivery_plan).success


@pytest.mark.parametrize("output", [{}, {"approved": "yes", "explanation": "Reason"},
    {"approved": True, "conflicts": ["Occupied"], "explanation": "Contradiction"},
    {"approved": False, "conflicts": [], "explanation": " "},
    {"approved": False, "conflicts": [{"cell": [1,0]}], "explanation": "Wrong shape"},
    {"approved": True, "conflicts": [], "explanation": "Fine", "execute": True},
])
def test_malformed_safety_output_fails_cleanly(state, output):
    update = safety_agent(state, client=client(output))
    assert update["safety"] is None and update["run_outcome"] == "failed"
    assert update["execution_requested"] is False


@pytest.mark.parametrize("error", [TimeoutError("private"), RuntimeError("private")])
def test_provider_errors_are_sanitized(state, error):
    class Broken(Fake):
        def with_structured_output(self, *args, **kwargs):
            def fail(_):
                raise error
            return RunnableLambda(fail)
    update = safety_agent(state, client=Broken(responses=[]))
    assert update["safety"] is None and "private" not in update["error_message"]


def test_context_lookup_failure(state):
    class Broken(SafetyTools):
        def current_context(self, *args):
            raise RuntimeError("private")
    update = safety_agent(state, client=client(), tools=Broken())
    assert update["run_outcome"] == "failed" and "private" not in update["error_message"]


def test_missing_plan_does_not_call_model(state):
    model = client()
    state = WarehouseGraphState.model_validate({**state.model_dump(), "delivery_plan": None})
    assert safety_agent(state, client=model)["run_outcome"] == "failed"
    assert not model.calls
