"""Mandatory model decisions, feedback retries, and unchanged execution guards."""

import pytest

from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from llm_fakes import client
from test_batch import initial, merge


def route(detour=0):
    pickup = [{"x": 0, "y": 0}, {"x": 1, "y": 0}]
    delivery = [{"x": x, "y": 0} for x in range(1, 5)]
    if detour:
        delivery = ([{"x": 1, "y": y} for y in range(detour + 1)]
                    + [{"x": x, "y": detour} for x in range(2, 5)]
                    + [{"x": 4, "y": y} for y in reversed(range(detour))])
    return dict(robot_id="robot-1", order_id="o1", route_to_pickup=pickup,
                route_to_dropoff=delivery, explanation="Model coordinates")


def decide(approved):
    return dict(approved=approved, conflicts=[] if approved else ["Try a different delivery leg"],
                explanation="Approved" if approved else "Revise the delivery leg")


def invoke(state, model):
    return WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))


def test_all_four_use_shared_model_without_deterministic_decision_tools(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Planning must use the model")
    for target in ("app.warehouse.routing.plan_delivery", "app.warehouse.validate_delivery_plan",
                   "app.warehouse.validation.validate_delivery_plan",
                   "app.warehouse.WarehouseSimulation.execute_delivery", "app.config.create_model_client",
                   "llm_fakes.plan_delivery", "llm_fakes.validate_delivery_plan"):
        monkeypatch.setattr(target, forbidden)
    model = client(scripts={
        "OrderSelection": [dict(order_id="o1", explanation="Choose this order")],
        "FleetSelection": [dict(robot_id="robot-1", explanation="Choose this robot")],
        "LLMRoutePlan": [route(1), route(1)], "SafetyDecision": [decide(True)] * 3,
        "LLMMovementPlan": [dict(robot_id="robot-1", route=[{"x": x, "y": 0} for x in range(4, 8)] + [{"x": 7, "y": y} for y in range(1, 10)], explanation="Model parking")]})
    state = initial(count=1)
    result = invoke(state, model)
    assert result.run_outcome == "ready" and result.warehouse == state.warehouse
    assert [schema.__name__ for schema, _, _ in model.calls] == [
        "OrderSelection", "FleetSelection", "LLMRoutePlan", "SafetyDecision",
        "LLMRoutePlan", "SafetyDecision", "LLMMovementPlan", "SafetyDecision"]
    assert result.delivery_plan.total_steps == 6  # The model's longer route is retained.


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_safety_rejections_have_bounded_model_retries(budget):
    model = client(scripts={"LLMRoutePlan": [route(i) for i in range(budget + 1)],
                            "SafetyDecision": [decide(False)] * (budget + 1)})
    state = merge(initial(count=1), {"max_replans": budget})
    result = invoke(state, model)
    assert result.run_outcome == "failed" and result.replan_count == budget
    assert result.warehouse == state.warehouse and not result.execution_requested
    assert result.planned_deliveries[0].status == "unplannable"
    calls = [payload for schema, payload, _ in model.calls if schema.__name__ == "LLMRoutePlan"]
    assert len(calls) == budget + 1
    for attempt, payload in enumerate(calls[1:], 1):
        assert payload["replan_count"] == attempt
        assert payload["safety_feedback"] == decide(False)
        assert payload["previous_route"] is not None


def test_repeated_rejected_route_fails_without_rechecking_safety():
    model = client(scripts={"LLMRoutePlan": [route(), route()], "SafetyDecision": [decide(False)]})
    result = invoke(initial(count=1), model)
    assert result.run_outcome == "failed" and result.replan_count == 1
    assert result.error_message == "Route retry repeated the rejected route"
    assert sum(schema.__name__ == "SafetyDecision" for schema, _, _ in model.calls) == 1


def test_execute_rejection_returns_replacement_then_requires_new_execute():
    model = client(scripts={"LLMRoutePlan": [route(), route(), route(1), route(1)],
                            "SafetyDecision": [decide(True)] * 3 + [decide(False)]})
    ready = invoke(initial(count=1), model)
    replacement = invoke(merge(ready, {"command": "execute"}), model)
    assert replacement.run_outcome == "ready" and replacement.replan_count == 1
    assert replacement.warehouse == ready.warehouse and not replacement.execution_requested
    assert replacement.delivery_plan != ready.delivery_plan
    assert invoke(replacement, model).run_outcome == "delivered"


def test_stale_revision_cannot_be_approved_by_model():
    from app.graph.updates import replace_warehouse
    from app.warehouse import Position, WarehouseSimulation
    ready = invoke(initial(count=1), client())
    simulation = WarehouseSimulation(ready.warehouse)
    simulation.add_blocked_cell(Position(x=8, y=8))
    stale = merge(ready, {**replace_warehouse(ready, simulation.state), "command": "execute"})
    model = client(scripts={"SafetyDecision": [decide(True)]})
    result = invoke(stale, model)
    assert result.run_outcome == "ready" and result.replan_count == 1
    assert result.warehouse == stale.warehouse and not result.execution_requested
    assert result.delivery_plan.warehouse_revision == stale.warehouse_revision
