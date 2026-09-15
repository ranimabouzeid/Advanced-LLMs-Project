"""Pure geometric checks and complete pending-delivery feasibility."""

import pytest
from pydantic import ValidationError

from app.warehouse import (
    DeliveryPlan, Position, RobotConflict, ValidationResult, WarehouseSimulation,
    WarehouseState, plan_delivery, validate_delivery_plan, validate_route,
)


@pytest.fixture
def simulation():
    warehouse = WarehouseSimulation()
    warehouse.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    return warehouse


@pytest.mark.parametrize("coordinates, start, goal, code", [
    ([], (0, 0), (2, 0), "empty_route"),
    ([(1, 0), (2, 0)], (0, 0), (2, 0), "wrong_start"),
    ([(0, 0), (1, 0)], (0, 0), (2, 0), "wrong_goal"),
    ([(0, 0), (-1, 0)], (0, 0), (-1, 0), "out_of_bounds"),
    ([(9, 0), (10, 0)], (9, 0), (10, 0), "out_of_bounds"),
    ([(0, 0), (1, 1)], (0, 0), (1, 1), "non_adjacent"),
    ([(0, 0), (2, 0)], (0, 0), (2, 0), "non_adjacent"),
    ([(0, 0), (0, 0)], (0, 0), (0, 0), "non_adjacent"),
    ([(2, 2), (3, 2)], (2, 2), (3, 2), "obstacle"),
])
def test_invalid_route_geometry(simulation, coordinates, start, goal, code):
    route = [Position(x=x, y=y) for x, y in coordinates]
    before = simulation.state
    result = validate_route(before, "robot-1", Position(x=start[0], y=start[1]),
                            Position(x=goal[0], y=goal[1]), route)
    assert not result.route_valid
    assert not result.collision_risk
    assert code in {issue.code for issue in result.reasons}
    assert simulation.state == before


def test_blocked_cell_is_not_a_robot_conflict(simulation):
    simulation.add_blocked_cell(Position(x=1, y=0))
    result = validate_route(simulation.state, "robot-1", Position(x=0, y=0), Position(x=1, y=0),
                            [Position(x=0, y=0), Position(x=1, y=0)])
    assert not result.route_valid
    assert not result.collision_risk
    assert result.conflicts == ()
    assert result.reasons[0].code == "blocked"


def test_robot_conflict_includes_identity_and_cell(simulation):
    result = validate_route(simulation.state, "robot-1", Position(x=0, y=0), Position(x=0, y=1),
                            [Position(x=0, y=0), Position(x=0, y=1)], leg="pickup")
    assert not result.route_valid
    assert result.collision_risk is True
    assert result.conflicts == (RobotConflict(robot_id="robot-2", cell=Position(x=0, y=1), leg="pickup"),)


@pytest.mark.parametrize("status, valid", [("idle", True), ("busy", True), ("charging", False), ("offline", False)])
def test_route_status_rules(simulation, status, valid):
    data = simulation.state.model_dump()
    data["robots"][0]["status"] = status
    state = WarehouseState.model_validate(data)
    result = validate_route(state, "robot-1", Position(x=0, y=0), Position(x=0, y=0), [Position(x=0, y=0)])
    assert result.route_valid is valid
    assert not result.collision_risk


def test_unknown_robot_is_a_validation_failure(simulation):
    result = validate_route(simulation.state, "missing", Position(x=1, y=0), Position(x=1, y=0),
                            [Position(x=1, y=0)])
    assert not result.route_valid
    assert not result.collision_risk
    assert result.reasons[0].code == "unknown_robot"


def test_delivery_leg_uses_pickup_start_and_json_roundtrip(simulation):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    assert plan.delivery_route[0] == Position(x=2, y=0)
    assert plan.delivery_route[0] != simulation.get_robot("robot-1").position
    result = validate_delivery_plan(simulation.state, plan)
    assert result.route_valid
    assert not result.collision_risk
    assert result.reasons == result.conflicts == ()
    assert DeliveryPlan.model_validate_json(plan.model_dump_json()) == plan
    assert ValidationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("battery, valid", [(8, False), (9, True), (10, True)])
def test_complete_battery_boundary(simulation, battery, valid):
    data = simulation.state.model_dump()
    data["robots"][0]["battery"] = battery
    state = WarehouseState.model_validate(data)
    plan = plan_delivery(state, "o", "robot-1")
    assert plan.total_steps == 9
    result = validate_delivery_plan(state, plan)
    assert result.route_valid is valid
    assert not result.collision_risk
    if not valid:
        assert "insufficient_battery" in {issue.code for issue in result.reasons}


def test_stale_revision_rejects_geometrically_unchanged_plan(simulation):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    simulation.add_blocked_cell(Position(x=8, y=8))
    result = validate_delivery_plan(simulation.state, plan)
    assert not result.route_valid
    assert not result.collision_risk
    assert "stale_plan" in {issue.code for issue in result.reasons}
    assert validate_delivery_plan(simulation.state, plan_delivery(simulation.state, "o", "robot-1")).route_valid


def test_validation_detects_new_block_on_delivery_leg(simulation):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    simulation.add_blocked_cell(Position(x=5, y=0))
    result = validate_delivery_plan(simulation.state, plan)
    assert not result.route_valid
    assert any(issue.code == "blocked" and issue.leg == "delivery" for issue in result.reasons)
    assert not result.collision_risk


def test_plan_cannot_understate_battery_cost(simulation):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    with pytest.raises(ValidationError):
        DeliveryPlan.model_validate({**plan.model_dump(), "total_steps": 0})
    # Defense in depth for callers using Pydantic's unchecked copy operation.
    forged = plan.model_copy(update={"total_steps": 0})
    data = simulation.state.model_dump()
    data["robots"][0]["battery"] = 8
    result = validate_delivery_plan(WarehouseState.model_validate(data), forged)
    assert {"step_count", "insufficient_battery"} <= {issue.code for issue in result.reasons}


@pytest.mark.parametrize("field", ["pickup_route", "delivery_route"])
def test_empty_plan_leg_rejected_by_model_and_validator(simulation, field):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    with pytest.raises(ValidationError):
        DeliveryPlan.model_validate({**plan.model_dump(), field: []})
    result = validate_delivery_plan(simulation.state, plan.model_copy(update={field: ()}))
    assert not result.route_valid
    assert "empty_route" in {issue.code for issue in result.reasons}


@pytest.mark.parametrize("field", ["order_id", "robot_id"])
def test_unknown_plan_identifiers(simulation, field):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    invalid = DeliveryPlan.model_validate({**plan.model_dump(), field: "missing"})
    result = validate_delivery_plan(simulation.state, invalid)
    assert not result.route_valid
    assert not result.collision_risk


def test_assigned_order_is_not_a_fresh_delivery_proposal(simulation):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    simulation.assign_order("o", "robot-1")
    result = validate_delivery_plan(simulation.state, plan)
    assert {"order_status", "robot_status", "stale_plan"} <= {issue.code for issue in result.reasons}


@pytest.mark.parametrize("field, value", [("warehouse_revision", -1), ("total_steps", True)])
def test_plan_numeric_fields_are_validated(simulation, field, value):
    plan = plan_delivery(simulation.state, "o", "robot-1")
    with pytest.raises(ValidationError):
        DeliveryPlan.model_validate({**plan.model_dump(), field: value})


def test_validation_flags_cannot_disagree_with_findings():
    with pytest.raises(ValidationError):
        ValidationResult(route_valid=True, collision_risk=True)
    with pytest.raises(ValidationError):
        ValidationResult(route_valid=True, collision_risk=True,
                         conflicts=(RobotConflict(robot_id="r", cell=Position(x=0, y=0)),))
