"""Safety interprets trusted findings; hard failures cannot be approved."""
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
def test_groq_may_approve_or_reject_valid_deterministic_findings(state, approved):
    model = client(dict(approved=approved, conflicts=[] if approved else ["Model sees a conflict"],
                        explanation="Model safety reasoning"))
    update = safety_agent(state, client=model)
    assert update["safety"].approved is approved
    assert update["run_outcome"] == ("ready" if approved else "failed")
    assert model.calls[0][0] is SafetyDecision and len(model.calls) == 1
    assert model.calls[0][1]["delivery_plan"] == state.delivery_plan.model_dump(mode="json")
    assert "evaluate safety only" in model.calls[0][2]
    assert model.calls[0][1]["trusted_findings"]["route_valid"]
    if not approved:
        assert update["execution_requested"] is False


def test_hard_failure_overrides_llm_approval_with_specific_feedback(state):
    data = state.model_dump()
    data["delivery_plan"]["pickup_route"] = [{"x":0,"y":0},{"x":2,"y":0}]
    data["delivery_plan"]["total_steps"] = 8
    state = WarehouseGraphState.model_validate(data)
    update = safety_agent(state, client=client(dict(approved=True, conflicts=[], explanation="Model approves")))
    assert not update["safety"].approved and update["run_outcome"] == "failed"
    assert not update["execution_requested"]
    assert any("non_adjacent" in message and "(2,0)" in message for message in update["safety"].conflicts)
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


@pytest.mark.parametrize("kind, code", [
    ("battery", "insufficient_battery"), ("shelf", "obstacle"), ("blocked", "blocked"),
    ("bounds", "out_of_bounds"), ("endpoint", "wrong_start"), ("revision", "stale_plan"),
    ("status", "robot_status"), ("occupied", None),
])
def test_trusted_delivery_failures_are_sent_to_groq_and_cannot_be_overridden(state, kind, code):
    data = state.model_dump()
    if kind == "battery":
        data["warehouse"]["robots"][0]["battery"] = 0
    elif kind == "shelf":
        data["delivery_plan"]["delivery_route"] = [Position(x=2,y=0), Position(x=3,y=2), Position(x=9,y=0)]
    elif kind == "blocked":
        data["warehouse"]["blocked_cells"] = [Position(x=1,y=0)]
    elif kind == "bounds":
        data["delivery_plan"]["pickup_route"] = [Position(x=0,y=0), Position(x=-1,y=0), Position(x=2,y=0)]
    elif kind == "endpoint":
        data["delivery_plan"]["pickup_route"] = (Position(x=1,y=0), *data["delivery_plan"]["pickup_route"][1:])
    elif kind == "revision":
        data["delivery_plan"]["warehouse_revision"] -= 1
    elif kind == "status":
        data["warehouse"]["robots"][0]["status"] = "charging"
    else:
        data["warehouse"]["robots"][1]["position"] = Position(x=1,y=0)
    data["delivery_plan"]["total_steps"] = len(data["delivery_plan"]["pickup_route"]) + len(data["delivery_plan"]["delivery_route"]) - 2
    invalid = WarehouseGraphState.model_validate(data)
    before = invalid.model_dump()
    model = client(dict(approved=True, conflicts=[], explanation="LLM incorrectly approves"))
    update = safety_agent(invalid, client=model)
    assert not update["safety"].approved and not update["execution_requested"]
    assert len(model.calls) == 1
    facts = model.calls[0][1]["trusted_findings"]
    assert not facts["route_valid"]
    if code:
        assert code in [issue["code"] for issue in facts["reasons"]]
        assert any(code in text for text in update["safety"].conflicts)
    else:
        assert facts["collision_risk"] and facts["conflicts"][0]["robot_id"] == "robot-2"
        assert any("robot-2" in text and "(1,0)" in text for text in update["safety"].conflicts)
    assert invalid.model_dump() == before  # Inspection never publishes movement.


@pytest.mark.parametrize("kind, code", [
    ("battery", "insufficient_battery"), ("reserved", "parking_reserved"),
    ("service_target", "parking_target"), ("wrong_target", "wrong_goal"),
    ("blocked", "blocked"), ("occupied", None),
])
def test_parking_hard_rules_are_trusted_before_model_approval(state, kind, code):
    from app.graph.agents import parking_safety
    from app.graph.tools import RouteTools
    from app.warehouse.movement import MovementPlan
    from app.warehouse import WarehouseState

    simulation = WarehouseSimulation(state.warehouse)
    assert simulation.execute_delivery(state.delivery_plan).success
    warehouse = simulation.state
    target = warehouse.parking_cells[0]
    plan = RouteTools().plan_parking(warehouse, "robot-1", target)
    data = warehouse.model_dump()
    kwargs = {}
    if kind == "battery":
        data["robots"][0]["battery"] = 0
    elif kind == "reserved":
        kwargs["reserved_cells"] = {target}
    elif kind == "wrong_target":
        kwargs["expected_target"] = warehouse.parking_cells[1]
    elif kind == "service_target":
        plan = MovementPlan(robot_id="robot-1", route=[Position(x=9,y=0)], warehouse_revision=warehouse.revision)
    elif kind == "blocked":
        data["blocked_cells"] = [target]
    else:
        data["robots"][1]["position"] = target
    warehouse = WarehouseState.model_validate(data)
    model = client(dict(approved=True, conflicts=[], explanation="Approve departure"))
    decision = parking_safety(warehouse, plan, client=model, **kwargs)
    assert not decision.approved and len(model.calls) == 1
    facts = model.calls[0][1]["trusted_findings"]
    if code:
        assert code in [issue["code"] for issue in facts["reasons"]]
    else:
        assert facts["collision_risk"]


def test_shared_dropoff_requires_previous_robot_departure_before_approval():
    from test_parking_schedules import scenario, run
    state, model = scenario()
    ready = run(state, model)
    first, second = ready.planned_deliveries
    simulation = WarehouseSimulation(state.warehouse)
    assert simulation.execute_delivery(first.delivery_plan).success
    local = WarehouseGraphState(warehouse=simulation.state, command="plan",
        order_selection=second.selection, selected_robot_id=second.robot_id,
        delivery_plan=second.delivery_plan)
    model = client(dict(approved=True, conflicts=[], explanation="Ignore prior robot"))
    result = safety_agent(local, client=model)
    assert not result["safety"].approved
    assert any(first.robot_id in text and "(9,0)" in text for text in result["safety"].conflicts)
