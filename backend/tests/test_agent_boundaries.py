"""Three shared-model roles, deterministic Route and unchanged execution guards."""

import pytest

from app.graph.graph import build_graph
from app.graph.state import WarehouseGraphState
from llm_fakes import client
from test_batch import initial, merge


def decide(approved):
    return dict(approved=approved, conflicts=[] if approved else ["Try a different delivery leg"],
                explanation="Approved" if approved else "Revise the delivery leg")


def invoke(state, model):
    return WarehouseGraphState.model_validate(build_graph(client=model).invoke(state))


def test_only_order_fleet_safety_use_shared_model_with_trusted_tools(monkeypatch):
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
        "SafetyDecision": [decide(True)] * 3})
    state = initial(count=1)
    result = invoke(state, model)
    assert result.run_outcome == "ready" and result.warehouse == state.warehouse
    assert [schema.__name__ for schema, _, _ in model.calls] == [
        "OrderSelection", "FleetSelection", "SafetyDecision", "SafetyDecision", "SafetyDecision"]
    assert result.delivery_plan.total_steps == 4  # Exact shortest route, no model constraints.
    assert len(costs) == 11  # Fleet costs + preview/final delivery paths + parking.


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_safety_rejection_does_not_retry_identical_deterministic_inputs(budget):
    model = client(scripts={"SafetyDecision": [decide(False)]})
    state = merge(initial(count=1), {"max_replans": budget})
    result = invoke(state, model)
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert result.warehouse == state.warehouse and not result.execution_requested
    assert result.planned_deliveries[0].status == "unplannable"
    assert [schema.__name__ for schema, _, _ in model.calls] == [
        "OrderSelection", "FleetSelection", "SafetyDecision"]
    assert sum(a.node == "route" for a in result.node_activity) == 1


def test_execute_rejection_without_changed_constraints_stops_and_revokes_approval():
    model = client(scripts={"SafetyDecision": [decide(True)] * 3 + [decide(False)]})
    ready = invoke(initial(count=1), model)
    rejected = invoke(merge(ready, {"command": "execute"}), model)
    assert rejected.run_outcome == "failed" and rejected.replan_count == 0
    assert rejected.warehouse == ready.warehouse and not rejected.execution_requested
    assert all(item.status == "stale" and item.safety is None for item in rejected.planned_deliveries)
    assert len(model.calls) == 6  # Only the extra Safety review; no new assignment or path.


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


def test_hard_findings_reject_corrupted_path_without_retrying_unchanged_inputs():
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
    assert result.run_outcome == "failed" and result.replan_count == 0
    assert tools.calls == 1
    assert not result.planned_deliveries[0].safety.approved
    assert any("non_adjacent" in text for text in result.planned_deliveries[0].safety.conflicts)
    assert result.warehouse == state.warehouse
