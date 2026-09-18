"""Mandatory model decisions, feedback retries, and unchanged execution guards."""

import pytest

from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from llm_fakes import client
from test_batch import initial, merge


def route(detour=0):
    return dict(robot_id="robot-1", order_id="o1", route_type="delivery",
                avoid_cells=[{"x": 2, "y": 0}] if detour else [],
                retry_feedback_acknowledged=True, explanation="Routing intent with corridor constraint")


def decide(approved):
    return dict(approved=approved, conflicts=[] if approved else ["Try a different delivery leg"],
                explanation="Approved" if approved else "Revise the delivery leg")


def invoke(state, model):
    return WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))


def test_all_four_use_shared_model_with_trusted_hybrid_tools(monkeypatch):
    from app.graph import tools
    original_astar = tools.astar_path
    costs = []

    def cost_only(*args):
        costs.append(args)
        return original_astar(*args)

    monkeypatch.setattr(tools, "astar_path", cost_only)
    def forbidden(*args, **kwargs):
        raise AssertionError("Planning must use the model")
    for target in ("app.warehouse.WarehouseSimulation.execute_delivery", "app.config.create_model_client"):
        monkeypatch.setattr(target, forbidden)
    model = client(scripts={
        "OrderSelection": [dict(order_id="o1", explanation="Choose this order")],
        "FleetSelection": [dict(robot_id="robot-1", explanation="Choose this robot")],
        "RouteIntent": [route(1), route(1)], "SafetyDecision": [decide(True)] * 3})
    state = initial(count=1)
    result = invoke(state, model)
    assert result.run_outcome == "ready" and result.warehouse == state.warehouse
    assert [schema.__name__ for schema, _, _ in model.calls] == [
        "OrderSelection", "FleetSelection", "RouteIntent", "SafetyDecision",
        "RouteIntent", "SafetyDecision", "RouteIntent", "SafetyDecision"]
    assert result.delivery_plan.total_steps == 6  # A* shortest route under the LLM corridor constraint.
    assert len(costs) == 11  # Fleet costs + preview/final delivery paths + parking.


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_safety_rejections_have_bounded_model_retries(budget):
    model = client(scripts={"RouteIntent": [route(i) for i in range(budget + 1)],
                            "SafetyDecision": [decide(False)] * (budget + 1)})
    state = merge(initial(count=1), {"max_replans": budget})
    result = invoke(state, model)
    assert result.run_outcome == "failed" and result.replan_count == budget
    assert result.warehouse == state.warehouse and not result.execution_requested
    assert result.planned_deliveries[0].status == "unplannable"
    calls = [payload for schema, payload, _ in model.calls if schema.__name__ == "RouteIntent"]
    assert len(calls) == budget + 1
    for attempt, payload in enumerate(calls[1:], 1):
        assert payload["replan_count"] == attempt
        assert payload["safety_feedback"] == decide(False)
        assert payload["previous_route"] is not None


def test_unchanged_shortest_path_is_recomputed_and_rechecked_on_retry():
    model = client(scripts={"SafetyDecision": [decide(False)]})
    result = invoke(initial(count=1), model)
    assert result.run_outcome == "ready" and result.replan_count == 1
    routes = [payload for schema, payload, _ in model.calls if schema.__name__ == "RouteIntent"]
    assert routes[1]["previous_route"] is not None and routes[1]["safety_feedback"] == decide(False)
    assert sum(schema.__name__ == "SafetyDecision" for schema, _, _ in model.calls) == 4


def test_execute_rejection_returns_replacement_then_requires_new_execute():
    model = client(scripts={"SafetyDecision": [decide(True)] * 3 + [decide(False)]})
    ready = invoke(initial(count=1), model)
    replacement = invoke(merge(ready, {"command": "execute"}), model)
    assert replacement.run_outcome == "ready" and replacement.replan_count == 1
    assert replacement.warehouse == ready.warehouse and not replacement.execution_requested
    retried = [payload for schema, payload, _ in model.calls if schema.__name__ == "RouteIntent"
               and payload.get("safety_feedback")]
    assert retried and all(payload["safety_feedback"] == decide(False) for payload in retried)
    assert replacement.route_retry_feedback == ()
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


def test_hard_findings_flow_into_fresh_route_intent_and_astar_retry():
    from app.graph.tools import RouteTools
    from app.warehouse import DeliveryPlan

    class CorruptedOnce(RouteTools):
        calls = 0
        def plan_delivery(self, *args):
            plan = super().plan_delivery(*args)
            self.calls += 1
            if self.calls == 1:
                data = plan.model_dump()
                data["delivery_route"] = (plan.delivery_route[0], plan.delivery_route[-1])
                data["total_steps"] = len(plan.pickup_route)
                return DeliveryPlan.model_validate(data)
            return plan

    tools = CorruptedOnce()
    model = client(scripts={"SafetyDecision": [decide(True)] * 4})
    state = initial(count=1)
    result = WarehouseGraphState.model_validate(build_graph(client=model, route_tools=tools).invoke(state))
    assert result.run_outcome == "ready" and result.replan_count == 1
    assert tools.calls == 3  # First proposal, feedback retry and finalization.
    routes = [data for schema, data, _ in model.calls if schema.__name__ == "RouteIntent"]
    assert any("non_adjacent" in text for text in routes[1]["safety_feedback"]["conflicts"])
    assert routes[2]["safety_feedback"] == routes[1]["safety_feedback"]
    assert result.delivery_plan.total_steps == 4 and result.warehouse == state.warehouse
    assert result.route_retry_feedback == ()
