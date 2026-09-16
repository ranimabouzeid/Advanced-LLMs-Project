"""Shared-state schema checks without graph nodes, clients, or checkpointing."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.graph.state import NodeActivity, OrderSelection, WarehouseGraphState
from app.warehouse import (
    DeliveryPlan, Position, ValidationResult, WarehouseSimulation, WarehouseState,
    plan_delivery, validate_delivery_plan,
)


@pytest.fixture
def simulation():
    simulation = WarehouseSimulation()
    simulation.create_order("o1", "p1", Position(x=2, y=0), Position(x=9, y=0))
    return simulation


def test_initial_state_and_optional_defaults(simulation):
    state = WarehouseGraphState(warehouse=simulation.state, command="plan")
    assert isinstance(state, BaseModel)
    assert state.warehouse is simulation.state
    assert state.warehouse_revision == simulation.state.revision == 1
    assert state.replan_count == 0 and state.max_replans == 3
    assert state.run_outcome == "idle"
    assert state.planning_outcome == "not_planned"
    assert state.execution_requested is False
    assert state.node_activity == ()
    for field in ("order_selection", "selected_robot_id", "delivery_plan", "safety", "error_message"):
        assert getattr(state, field) is None


@pytest.mark.parametrize("revision", [-1, True, 1.5])
def test_invalid_nested_revision(simulation, revision):
    warehouse = simulation.state.model_dump()
    warehouse["revision"] = revision
    with pytest.raises(ValidationError):
        WarehouseGraphState(warehouse=warehouse, command="plan")


@pytest.mark.parametrize("changes", [
    {"replan_count": -1}, {"replan_count": True}, {"replan_count": 1.5},
    {"max_replans": -1}, {"max_replans": 4}, {"max_replans": True},
    {"max_replans": 1.5}, {"replan_count": 2, "max_replans": 1},
])
def test_invalid_retry_limits(simulation, changes):
    with pytest.raises(ValidationError):
        WarehouseGraphState(warehouse=simulation.state, command="plan", **changes)


@pytest.mark.parametrize("limit", [0, 3])
def test_retry_boundaries(simulation, limit):
    state = WarehouseGraphState(warehouse=simulation.state, command="plan",
                                replan_count=limit, max_replans=limit)
    assert state.replan_count == limit


def test_nested_models_and_json_roundtrip(simulation):
    plan = plan_delivery(simulation.state, "o1", "robot-1")
    safety = validate_delivery_plan(simulation.state, plan)
    state = WarehouseGraphState(
        warehouse=simulation.state, command="execute", execution_requested=True,
        order_selection=OrderSelection(order_id="o1", explanation="First pending order"),
        selected_robot_id="robot-1", delivery_plan=plan, planning_outcome="planned",
        safety=safety, run_outcome="ready",
        node_activity=(NodeActivity(node="route", status="completed"),
                       NodeActivity(node="safety", status="completed", message="Checked both legs")),
    )
    assert state.delivery_plan is plan and state.safety is safety
    data = json.loads(state.model_dump_json())
    assert data["warehouse"]["revision"] == 1
    assert "warehouse_revision" not in data
    assert "route_valid" not in data
    assert data["safety"]["route_valid"] is True
    assert data["delivery_plan"]["pickup_route"][0] == {"x": 0, "y": 0}
    assert data["warehouse"]["obstacles"] == sorted(data["warehouse"]["obstacles"], key=lambda p: (p["x"], p["y"]))
    restored = WarehouseGraphState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert WarehouseGraphState.model_validate(state.model_dump()) == state
    assert isinstance(restored.warehouse, WarehouseState)
    assert isinstance(restored.delivery_plan, DeliveryPlan)
    assert isinstance(restored.safety, ValidationResult)
    assert isinstance(restored.warehouse.obstacles, frozenset)
    assert isinstance(restored.delivery_plan.pickup_route, tuple)
    assert [item.node for item in restored.node_activity] == ["route", "safety"]


def test_unchecked_and_rejected_safety_are_distinct(simulation):
    plan = plan_delivery(simulation.state, "o1", "robot-1")
    simulation.add_blocked_cell(Position(x=5, y=0))
    safety = validate_delivery_plan(simulation.state, plan)
    state = WarehouseGraphState(warehouse=simulation.state, command="plan", safety=safety)
    assert state.safety is not None and state.safety.route_valid is False
    restored = WarehouseGraphState.model_validate_json(state.model_dump_json())
    assert restored.safety.reasons == safety.reasons


@pytest.mark.parametrize("outcome", ["idle", "running", "ready", "delivered", "no_work", "no_robot", "unreachable", "failed"])
def test_typed_run_outcomes(simulation, outcome):
    state = WarehouseGraphState(warehouse=simulation.state, command="plan", run_outcome=outcome)
    assert state.run_outcome == outcome


@pytest.mark.parametrize("command", ["plan", "execute"])
def test_typed_commands(simulation, command):
    state = WarehouseGraphState(warehouse=simulation.state, command=command,
                                execution_requested=command == "execute")
    assert state.command == command


@pytest.mark.parametrize("changes", [
    {"command": "dispatch"}, {"run_outcome": "success-ish"}, {"planning_outcome": "maybe"},
    {"execution_requested": True}, {"execution_requested": "false"},
    {"warehouse_revision": 1}, {"route_valid": True}, {"selected_robot_id": "missing"},
    {"error_message": " "}, {"error_message": "x" * 301},
])
def test_invalid_fields(simulation, changes):
    with pytest.raises(ValidationError):
        WarehouseGraphState.model_validate({"warehouse": simulation.state, "command": "plan", **changes})


@pytest.mark.parametrize("explanation", ["", "   ", "x" * 301])
def test_selection_requires_short_explanation(explanation):
    with pytest.raises(ValidationError):
        OrderSelection(order_id="o1", explanation=explanation)


@pytest.mark.parametrize("kind", ["missing", "assigned"])
def test_selection_requires_eligible_order(simulation, kind):
    if kind == "assigned":
        simulation.assign_order("o1", "robot-1")
    selection = OrderSelection(order_id="missing" if kind == "missing" else "o1", explanation="Selection")
    with pytest.raises(ValidationError, match="eligible pending order"):
        WarehouseGraphState(warehouse=simulation.state, command="plan", order_selection=selection)


@pytest.mark.parametrize("fields", [
    {"node": "unknown", "status": "completed"},
    {"node": "order", "status": "maybe"},
    {"node": "order", "status": "completed", "message": "x" * 301},
])
def test_activity_is_typed(fields):
    with pytest.raises(ValidationError):
        NodeActivity(**fields)


def test_state_is_immutable_and_activity_inputs_are_detached(simulation):
    records = [NodeActivity(node="order", status="completed")]
    state = WarehouseGraphState(warehouse=simulation.state, command="plan", node_activity=records)
    other = WarehouseGraphState(warehouse=simulation.state, command="plan")
    records.append(NodeActivity(node="fleet", status="completed"))
    assert len(state.node_activity) == 1 and other.node_activity == ()
    with pytest.raises(ValidationError):
        state.replan_count = 1
    with pytest.raises(ValidationError):
        state.warehouse_revision = 2
    with pytest.raises(ValidationError):
        state.node_activity[0].message = "Changed"


def test_initial_json_roundtrip_and_schema(simulation):
    state = WarehouseGraphState(warehouse=simulation.state, command="plan")
    assert WarehouseGraphState.model_validate_json(state.model_dump_json()) == state
    schema = WarehouseGraphState.model_json_schema()
    assert "WarehouseState" in schema["$defs"]
    assert "DeliveryPlan" in schema["$defs"]
    assert "ValidationResult" in schema["$defs"]
    assert "warehouse_revision" not in schema["properties"]
