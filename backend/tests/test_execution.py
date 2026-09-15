"""Atomic full deliveries: failures discard even successfully completed temporary steps."""

import pytest
from pydantic import ValidationError

from app.warehouse import (
    DeliveryExecutionResult, OrderStatus, Position, RobotStatus,
    WarehouseSimulation, WarehouseState, plan_delivery,
)


@pytest.fixture
def delivery():
    simulation = WarehouseSimulation()
    simulation.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    plan = plan_delivery(simulation.state, "o", "robot-1")
    assert plan is not None
    return simulation, plan


def assert_rollback(simulation, before, result, stage):
    assert not result.success
    assert result.failed_stage == stage
    assert result.error
    assert result.final_state is None
    assert result.committed_steps == 0
    assert simulation.state is before
    assert simulation.state.model_dump_json() == before.model_dump_json()
    assert simulation.get_order("o").status == OrderStatus.PENDING
    assert simulation.get_robot("robot-1").status == RobotStatus.IDLE
    assert simulation.get_robot("robot-1").carried_package_id is None


def test_successful_atomic_delivery_uses_existing_primitives(delivery, monkeypatch):
    simulation, plan = delivery
    before = simulation.state
    original_move = WarehouseSimulation.move_robot
    original_pickup = WarehouseSimulation.pickup_package
    original_deliver = WarehouseSimulation.deliver_package
    moves = []
    temporaries = []

    def move(temporary, robot_id, cell):
        assert temporary is not simulation
        assert simulation.state is before  # Nothing is visible before commit.
        temporaries.append(temporary)
        moves.append(cell)
        return original_move(temporary, robot_id, cell)

    def pickup(temporary, order_id, robot_id):
        assert simulation.state is before
        battery = temporary.get_robot(robot_id).battery
        result = original_pickup(temporary, order_id, robot_id)
        assert temporary.get_robot(robot_id).battery == battery
        return result

    def deliver(temporary, order_id, robot_id):
        assert simulation.state is before
        battery = temporary.get_robot(robot_id).battery
        result = original_deliver(temporary, order_id, robot_id)
        assert temporary.get_robot(robot_id).battery == battery
        return result

    monkeypatch.setattr(WarehouseSimulation, "move_robot", move)
    monkeypatch.setattr(WarehouseSimulation, "pickup_package", pickup)
    monkeypatch.setattr(WarehouseSimulation, "deliver_package", deliver)
    result = simulation.execute_delivery(plan)

    assert result.success
    assert result.final_state is simulation.state
    assert result.validation.route_valid
    assert result.failed_stage is result.error is None
    assert moves == list(plan.pickup_route[1:]) + list(plan.delivery_route[1:])
    assert len({id(item) for item in temporaries}) == 1
    assert result.committed_steps == plan.total_steps == 9
    robot = simulation.get_robot("robot-1")
    assert robot.position == Position(x=9, y=0)
    assert robot.battery == 91
    assert robot.status == RobotStatus.IDLE
    assert robot.carried_package_id is None
    order = simulation.get_order("o")
    assert order.status == OrderStatus.DELIVERED
    assert order.assigned_robot_id == "robot-1"
    assert simulation.pending_orders() == ()
    assert simulation.state.revision == before.revision + 1
    assert simulation.state.robots[1:] == before.robots[1:]
    assert simulation.state.obstacles == before.obstacles
    assert before.orders[0].status == OrderStatus.PENDING
    assert before.robots[0].battery == 100
    assert DeliveryExecutionResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("start, pickup, dropoff, battery, steps", [
    ((0, 0), (0, 0), (9, 0), 9, 9),  # No movement before pickup.
    ((0, 0), (9, 0), (9, 0), 9, 9),  # No movement after pickup.
    ((9, 0), (9, 0), (9, 0), 0, 0),  # Entire delivery without movement.
])
def test_zero_step_legs_and_exact_battery(start, pickup, dropoff, battery, steps):
    data = WarehouseSimulation().state.model_dump()
    data["robots"][0].update(position={"x": start[0], "y": start[1]}, battery=battery)
    simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    simulation.create_order("o", "p", Position(x=pickup[0], y=pickup[1]),
                            Position(x=dropoff[0], y=dropoff[1]))
    plan = plan_delivery(simulation.state, "o", "robot-1")
    before = simulation.state
    result = simulation.execute_delivery(plan)
    assert result.success
    assert result.committed_steps == plan.total_steps == steps
    assert simulation.get_robot("robot-1").battery == battery - steps == 0
    assert simulation.get_robot("robot-1").status == RobotStatus.IDLE
    assert simulation.get_robot("robot-1").carried_package_id is None
    assert simulation.get_order("o").status == OrderStatus.DELIVERED
    assert simulation.state.revision == before.revision + 1


def test_insufficient_battery_rejected_before_assignment(delivery, monkeypatch):
    simulation, _ = delivery
    data = simulation.state.model_dump()
    data["robots"][0]["battery"] = 8
    simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    plan = plan_delivery(simulation.state, "o", "robot-1")
    before = simulation.state
    called = []
    monkeypatch.setattr(WarehouseSimulation, "assign_order", lambda *args: called.append(True))
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, "validation")
    assert "insufficient_battery" in {issue.code for issue in result.validation.reasons}
    assert not called


@pytest.mark.parametrize("kind", ["obstacle", "blocked", "stale_only"])
def test_changed_warehouse_rejects_plan(delivery, kind):
    simulation, plan = delivery
    if kind == "obstacle":
        data = simulation.state.model_dump()
        data["obstacles"].append({"x": 5, "y": 0})
        data["revision"] += 1
        simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    else:
        simulation.add_blocked_cell(Position(x=5, y=0) if kind == "blocked" else Position(x=8, y=8))
    before = simulation.state
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, "validation")
    codes = {issue.code for issue in result.validation.reasons}
    assert "stale_plan" in codes
    if kind != "stale_only":
        assert kind in codes


@pytest.mark.parametrize("action, stage, after_success", [
    ("assign_order", "assignment", False),
    ("pickup_package", "pickup", False),
    ("pickup_package", "pickup", True),
    ("deliver_package", "delivery", False),
    ("deliver_package", "delivery", True),
])
def test_lifecycle_failure_discards_temporary_work(delivery, monkeypatch, action, stage, after_success):
    simulation, plan = delivery
    before = simulation.state
    original = getattr(WarehouseSimulation, action)
    observed = []

    def fail(temporary, order_id, robot_id):
        assert temporary is not simulation
        assert simulation.state is before
        if after_success:
            original(temporary, order_id, robot_id)
        observed.append(temporary.state)
        raise RuntimeError(f"Injected {stage} failure")

    monkeypatch.setattr(WarehouseSimulation, action, fail)
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, stage)
    assert "RuntimeError" in result.error
    assert observed
    if stage == "pickup":
        assert observed[0].robots[0].battery == 98
    if stage == "delivery":
        assert observed[0].robots[0].battery == 91
    if stage == "delivery" and after_success:
        assert observed[0].orders[0].status == OrderStatus.DELIVERED


@pytest.mark.parametrize("fail_at, stage", [(2, "pickup_route"), (4, "delivery_route")])
def test_mid_route_failure_rolls_back_prior_moves(delivery, monkeypatch, fail_at, stage):
    simulation, plan = delivery
    before = simulation.state
    original = WarehouseSimulation.move_robot
    calls = []

    def fail(temporary, robot_id, cell):
        moved = original(temporary, robot_id, cell)
        calls.append(temporary.state)
        assert simulation.state is before
        if len(calls) == fail_at:
            raise ValueError("Injected failure after a successful temporary movement")
        return moved

    monkeypatch.setattr(WarehouseSimulation, "move_robot", fail)
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, stage)
    assert len(calls) == fail_at
    assert calls[-1].robots[0].battery == 100 - fail_at
    if stage == "delivery_route":
        assert calls[-1].robots[0].carried_package_id == "p"


def test_block_added_during_temporary_execution_uses_real_movement_checks(delivery, monkeypatch):
    simulation, plan = delivery
    before = simulation.state
    original = WarehouseSimulation.move_robot
    calls = []

    def introduce_block(temporary, robot_id, cell):
        calls.append(cell)
        if cell == Position(x=4, y=0):
            temporary.add_blocked_cell(cell)
        return original(temporary, robot_id, cell)

    monkeypatch.setattr(WarehouseSimulation, "move_robot", introduce_block)
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, "delivery_route")
    assert result.validation.route_valid  # Initial snapshot was safe.
    assert "blocked" in result.error
    assert len(calls) == 4


def test_finalization_rejects_missing_delivery_transition(delivery, monkeypatch):
    simulation, plan = delivery
    before = simulation.state
    monkeypatch.setattr(WarehouseSimulation, "deliver_package", lambda *args: None)
    result = simulation.execute_delivery(plan)
    assert_rollback(simulation, before, result, "finalization")


def test_delivered_order_cannot_be_executed_again(delivery):
    simulation, plan = delivery
    assert simulation.execute_delivery(plan).success
    completed = simulation.state
    result = simulation.execute_delivery(plan)
    assert not result.success
    assert result.failed_stage == "validation"
    assert result.final_state is None
    assert result.committed_steps == 0
    assert simulation.state is completed
    assert "order_status" in {issue.code for issue in result.validation.reasons}


def test_unrelated_order_remains_pending(delivery):
    simulation, _ = delivery
    other = simulation.create_order("o2", "p2", Position(x=1, y=1), Position(x=9, y=9))
    plan = plan_delivery(simulation.state, "o", "robot-1")
    assert simulation.execute_delivery(plan).success
    assert simulation.get_order("o2") == other


def test_invalid_proposal_returns_typed_failure(delivery):
    simulation, _ = delivery
    before = simulation.state
    result = simulation.execute_delivery(None)
    assert_rollback(simulation, before, result, "validation")


def test_result_model_rejects_partial_publication(delivery):
    simulation, _ = delivery
    with pytest.raises(ValidationError):
        DeliveryExecutionResult(success=False, final_state=simulation.state,
                                failed_stage="pickup", error="failed")
    with pytest.raises(ValidationError):
        DeliveryExecutionResult(success=False, committed_steps=2, failed_stage="pickup", error="failed")
    with pytest.raises(ValidationError):
        DeliveryExecutionResult(success=True)
