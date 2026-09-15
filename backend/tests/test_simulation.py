"""Deterministic actions, boundary cases, and failure atomicity."""

import pytest

from app.warehouse import OrderStatus, Position, RobotStatus, WarehouseSimulation, WarehouseState


@pytest.fixture
def simulation():
    return WarehouseSimulation()


def test_initial_layout_is_fixed_and_instances_are_independent(simulation):
    other = WarehouseSimulation()
    assert simulation.state == other.state
    assert (simulation.state.width, simulation.state.height) == (10, 10)
    assert simulation.robot_positions() == {
        "robot-1": Position(x=0, y=0), "robot-2": Position(x=0, y=1),
        "robot-3": Position(x=0, y=2),
    }
    assert simulation.obstacles() == frozenset(
        Position(x=x, y=y) for x in (3, 6) for y in range(2, 8)
    )
    assert simulation.state.dropoff_locations == {Position(x=9, y=0), Position(x=9, y=9)}
    assert not simulation.state.orders
    assert not simulation.state.blocked_cells
    assert all(robot.battery == 100 and robot.status == RobotStatus.IDLE for robot in simulation.state.robots)
    simulation.move_robot("robot-1", Position(x=1, y=0))
    assert other.get_robot("robot-1").position == Position(x=0, y=0)


def test_inspection_cannot_change_simulation(simulation):
    positions = simulation.robot_positions()
    positions["robot-1"] = Position(x=9, y=9)
    assert simulation.get_robot("robot-1").position == Position(x=0, y=0)


def test_create_order_preserves_robots_and_records_package(simulation):
    before = simulation.state
    order = simulation.create_order("order-1", "package-1", Position(x=2, y=2), Position(x=9, y=0))
    assert simulation.state.orders == (order,)
    assert order.package.id == "package-1"
    assert order.package.pickup == Position(x=2, y=2)
    assert order.dropoff == Position(x=9, y=0)
    assert order.status == OrderStatus.PENDING
    assert simulation.state.robots == before.robots
    assert before.orders == ()


@pytest.mark.parametrize("pickup, dropoff", [
    (Position(x=3, y=2), Position(x=9, y=0)),
    (Position(x=-1, y=0), Position(x=9, y=0)),
    (Position(x=10, y=0), Position(x=9, y=0)),
    (Position(x=1, y=0), Position(x=8, y=0)),
    (Position(x=1, y=0), Position(x=9, y=10)),
])
def test_invalid_order_does_not_change_state(simulation, pickup, dropoff):
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.create_order("o", "p", pickup, dropoff)
    assert simulation.state == before


@pytest.mark.parametrize("order_id, package_id", [("o1", "p2"), ("o2", "p1")])
def test_duplicate_order_or_package_is_rejected(simulation, order_id, package_id):
    simulation.create_order("o1", "p1", Position(x=1, y=0), Position(x=9, y=0))
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.create_order(order_id, package_id, Position(x=2, y=0), Position(x=9, y=9))
    assert simulation.state == before


def test_blocking_and_unblocking_are_idempotent(simulation):
    before = simulation.state
    cell = Position(x=1, y=0)
    simulation.add_blocked_cell(cell)
    simulation.add_blocked_cell(cell)
    assert simulation.state.blocked_cells == {cell}
    with pytest.raises(ValueError, match="blocked"):
        simulation.move_robot("robot-1", cell)
    simulation.remove_blocked_cell(cell)
    simulation.remove_blocked_cell(cell)
    assert simulation.state == before
    assert simulation.move_robot("robot-1", cell).position == cell


@pytest.mark.parametrize("cell", [Position(x=10, y=0), Position(x=0, y=-1), Position(x=3, y=2), Position(x=0, y=0)])
def test_invalid_block_does_not_change_state(simulation, cell):
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.add_blocked_cell(cell)
    assert simulation.state == before


def test_out_of_bounds_unblock_is_rejected(simulation):
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.remove_blocked_cell(Position(x=10, y=0))
    assert simulation.state == before


def test_temporarily_blocked_order_endpoints_are_allowed(simulation):
    pickup, dropoff = Position(x=1, y=0), Position(x=9, y=0)
    simulation.add_blocked_cell(pickup)
    simulation.add_blocked_cell(dropoff)
    order = simulation.create_order("o", "p", pickup, dropoff)
    assert order.status == OrderStatus.PENDING
    assert simulation.state.blocked_cells == {pickup, dropoff}


def test_single_move_consumes_one_battery_and_preserves_other_state(simulation):
    before = simulation.state
    moved = simulation.move_robot("robot-1", Position(x=1, y=0))
    assert moved.position == Position(x=1, y=0)
    assert moved.battery == 99
    assert moved.status == RobotStatus.IDLE
    assert simulation.state.robots[1:] == before.robots[1:]
    assert simulation.state.orders == before.orders
    assert simulation.state.obstacles == before.obstacles
    assert before.robots[0].position == Position(x=0, y=0)


@pytest.mark.parametrize("destination, reason", [
    (Position(x=-1, y=0), "outside"),
    (Position(x=0, y=-1), "outside"),
    (Position(x=10, y=0), "outside"),
    (Position(x=0, y=10), "outside"),
    (Position(x=1, y=1), "adjacent"),
    (Position(x=2, y=0), "adjacent"),
    (Position(x=0, y=0), "adjacent"),
    (Position(x=0, y=1), "occupied"),
])
def test_invalid_move_is_atomic(simulation, destination, reason):
    before = simulation.state
    with pytest.raises(ValueError, match=reason):
        simulation.move_robot("robot-1", destination)
    assert simulation.state == before


def test_move_into_adjacent_shelf_is_rejected(simulation):
    for cell in [Position(x=1, y=0), Position(x=2, y=0), Position(x=2, y=1), Position(x=2, y=2)]:
        simulation.move_robot("robot-1", cell)
    before = simulation.state
    with pytest.raises(ValueError, match="obstacle"):
        simulation.move_robot("robot-1", Position(x=3, y=2))
    assert simulation.state == before


def test_unknown_robot_is_rejected(simulation):
    before = simulation.state
    with pytest.raises(KeyError, match="Unknown robot"):
        simulation.move_robot("missing", Position(x=1, y=0))
    assert simulation.state == before


@pytest.mark.parametrize("status, battery, allowed", [
    (RobotStatus.IDLE, 0, False), (RobotStatus.CHARGING, 100, False),
    (RobotStatus.OFFLINE, 100, False), (RobotStatus.BUSY, 1, True),
])
def test_status_and_battery_control_movement(simulation, status, battery, allowed):
    data = simulation.state.model_dump()
    data["robots"][0].update(status=status, battery=battery)
    simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    before = simulation.state
    if allowed:
        moved = simulation.move_robot("robot-1", Position(x=1, y=0))
        assert moved.battery == 0
        assert moved.status == status
        with pytest.raises(ValueError, match="battery"):
            simulation.move_robot("robot-1", Position(x=2, y=0))
        assert simulation.get_robot("robot-1") == moved
    else:
        with pytest.raises(ValueError):
            simulation.move_robot("robot-1", Position(x=1, y=0))
        assert simulation.state == before


def test_all_four_orthogonal_directions_and_repeatable_sequence():
    def run():
        simulation = WarehouseSimulation()
        for cell in [Position(x=1, y=0), Position(x=1, y=1), Position(x=2, y=1),
                     Position(x=2, y=0), Position(x=1, y=0)]:
            simulation.move_robot("robot-1", cell)
        return simulation.state

    assert run() == run()
    assert run().robots[0].battery == 95
