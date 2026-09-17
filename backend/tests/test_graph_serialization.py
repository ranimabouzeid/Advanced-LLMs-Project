"""Exercise the installed checkpoint serializer without a saver or workflow."""

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.graph.state import SafetyDecision, NodeActivity, OrderSelection, WarehouseGraphState
from app.warehouse import (
    DeliveryPlan, Order, OrderStatus, Package, Position, Robot, RobotConflict,
    RobotStatus, ValidationIssue, ValidationResult, WarehouseSimulation,
    WarehouseState, plan_delivery, validate_delivery_plan,
)


@pytest.mark.parametrize("phase", ["initial", "approved", "rejected"])
@pytest.mark.parametrize("representation", ["model", "channels"])
def test_checkpoint_serializer_roundtrip(phase, representation):
    simulation = WarehouseSimulation()
    fields = {}
    if phase != "initial":
        simulation.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
        plan = plan_delivery(simulation.state, "o", "robot-1")
        if phase == "rejected":
            simulation.move_robot("robot-2", Position(x=1, y=1))
            simulation.move_robot("robot-2", Position(x=1, y=0))
        safety = SafetyDecision(approved=phase == "approved", conflicts=() if phase == "approved" else ("Occupied",), explanation="Mock decision")
        fields = dict(order_selection=OrderSelection(order_id="o", explanation="First pending"),
                      selected_robot_id="robot-1", delivery_plan=plan, safety=safety,
                      planning_outcome="planned" if phase == "approved" else "stale",
                      run_outcome="ready" if phase == "approved" else "failed",
                      node_activity=(NodeActivity(node="route", status="completed"),
                                     NodeActivity(node="safety", status="completed")))
        if phase == "rejected":
            assert safety.conflicts
    state = WarehouseGraphState(warehouse=simulation.state, command="plan", **fields)
    serializer = JsonPlusSerializer(allowed_msgpack_modules=[
        WarehouseGraphState, OrderSelection, SafetyDecision, NodeActivity, WarehouseState,
        DeliveryPlan, ValidationResult, ValidationIssue, RobotConflict,
        Order, OrderStatus, Package, Position, Robot, RobotStatus,
    ])
    # LangGraph persists field/channel values, not just model_dump JSON.
    payload = (state if representation == "model" else
               {name: getattr(state, name) for name in WarehouseGraphState.model_fields})
    encoded = serializer.dumps_typed(payload)
    assert encoded[0] == "msgpack"  # No pickle fallback.
    restored = serializer.loads_typed(encoded)
    reconstructed = WarehouseGraphState.model_validate(restored)
    assert reconstructed == state
    assert isinstance(reconstructed.warehouse, WarehouseState)
    assert isinstance(reconstructed.warehouse.obstacles, frozenset)
    assert reconstructed.warehouse_revision == state.warehouse_revision
    if phase != "initial":
        assert isinstance(reconstructed.delivery_plan, DeliveryPlan)
        assert isinstance(reconstructed.safety, SafetyDecision)
        assert [record.node for record in reconstructed.node_activity] == ["route", "safety"]
    if phase == "rejected":
        assert isinstance(reconstructed.safety.conflicts[0], str)
        assert reconstructed.safety.approved is False
