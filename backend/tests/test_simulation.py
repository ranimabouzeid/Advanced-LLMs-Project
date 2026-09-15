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
    assert simulation.state.model_dump(exclude={"revision"}) == before.model_dump(exclude={"revision"})
    assert simulation.state.revision == before.revision + 2
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


def test_complete_lifecycle_through_individual_primitives(simulation):
    from app.warehouse import plan_delivery, validate_delivery_plan

    simulation.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    plan = plan_delivery(simulation.state, "o", "robot-1")
    assert validate_delivery_plan(simulation.state, plan).route_valid
    assigned = simulation.assign_order("o", "robot-1")
    assert assigned.status == OrderStatus.ASSIGNED
    assert assigned.assigned_robot_id == "robot-1"
    assert simulation.get_robot("robot-1").status == RobotStatus.BUSY
    assert simulation.get_robot("robot-1").battery == 100
    assert simulation.pending_orders() == ()
    for cell in plan.pickup_route[1:]:
        simulation.move_robot("robot-1", cell)
    battery = simulation.get_robot("robot-1").battery
    assert simulation.pickup_package("o", "robot-1").status == OrderStatus.PICKED_UP
    assert simulation.get_robot("robot-1").carried_package_id == "p"
    assert simulation.get_robot("robot-1").battery == battery
    for cell in plan.delivery_route[1:]:
        simulation.move_robot("robot-1", cell)
        assert simulation.get_robot("robot-1").carried_package_id == "p"
    battery = simulation.get_robot("robot-1").battery
    delivered = simulation.deliver_package("o", "robot-1")
    assert delivered.status == OrderStatus.DELIVERED
    assert delivered.assigned_robot_id == "robot-1"
    robot = simulation.get_robot("robot-1")
    assert robot.position == Position(x=9, y=0)
    assert robot.carried_package_id is None
    assert robot.status == RobotStatus.IDLE
    assert robot.battery == battery == 100 - plan.total_steps == 91
    assert simulation.state.revision == 1 + 3 + plan.total_steps
    assert simulation.pending_orders() == ()

    before = simulation.state
    for action in (simulation.assign_order, simulation.pickup_package, simulation.deliver_package):
        with pytest.raises(ValueError):
            action("o", "robot-1")
        assert simulation.state == before
    with pytest.raises(ValueError):
        simulation.create_order("duplicate", "p", Position(x=1, y=0), Position(x=9, y=0))
    assert simulation.state == before

    # A delivered order is history, not an active reservation of this robot.
    simulation.create_order("next", "next-package", robot.position, Position(x=9, y=9))
    assert [order.id for order in simulation.pending_orders()] == ["next"]
    simulation.assign_order("next", "robot-1")


@pytest.mark.parametrize("status", [RobotStatus.BUSY, RobotStatus.CHARGING, RobotStatus.OFFLINE])
def test_assignment_requires_idle_robot(simulation, status):
    data = simulation.state.model_dump()
    data["robots"][0]["status"] = status
    simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    simulation.create_order("o", "p", Position(x=0, y=0), Position(x=9, y=0))
    before = simulation.state
    with pytest.raises(ValueError, match="idle"):
        simulation.assign_order("o", "robot-1")
    assert simulation.state == before


def test_repeated_assignment_and_second_active_order_are_rejected(simulation):
    simulation.create_order("o1", "p1", Position(x=0, y=0), Position(x=9, y=0))
    simulation.create_order("o2", "p2", Position(x=1, y=0), Position(x=9, y=0))
    simulation.assign_order("o1", "robot-1")
    before = simulation.state
    for order_id, robot_id in [("o1", "robot-1"), ("o1", "robot-2"), ("o2", "robot-1")]:
        with pytest.raises(ValueError):
            simulation.assign_order(order_id, robot_id)
        assert simulation.state == before
    assert [order.id for order in simulation.pending_orders()] == ["o2"]


def test_pickup_guards_and_delivery_before_pickup(simulation):
    simulation.create_order("o", "p", Position(x=1, y=0), Position(x=9, y=0))
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.pickup_package("o", "robot-1")
    with pytest.raises(ValueError):
        simulation.deliver_package("o", "robot-1")
    assert simulation.state == before
    simulation.assign_order("o", "robot-1")
    before = simulation.state
    with pytest.raises(ValueError, match="assigned robot"):
        simulation.pickup_package("o", "robot-2")
    with pytest.raises(ValueError, match="pickup position"):
        simulation.pickup_package("o", "robot-1")
    with pytest.raises(ValueError):
        simulation.deliver_package("o", "robot-1")
    assert simulation.state == before
    simulation.move_robot("robot-1", Position(x=1, y=0))
    simulation.pickup_package("o", "robot-1")
    before = simulation.state
    with pytest.raises(ValueError):
        simulation.pickup_package("o", "robot-1")
    with pytest.raises(ValueError, match="assigned robot"):
        simulation.deliver_package("o", "robot-2")
    with pytest.raises(ValueError, match="drop-off"):
        simulation.deliver_package("o", "robot-1")
    assert simulation.state == before


@pytest.mark.parametrize("action", ["assign_order", "pickup_package", "deliver_package"])
def test_unknown_lifecycle_identifiers_do_not_change_state(simulation, action):
    simulation.create_order("o", "p", Position(x=0, y=0), Position(x=9, y=0))
    before = simulation.state
    with pytest.raises(KeyError, match="Unknown order"):
        getattr(simulation, action)("missing", "robot-1")
    with pytest.raises(KeyError, match="Unknown robot"):
        getattr(simulation, action)("o", "missing")
    assert simulation.state == before


def test_zero_move_delivery_costs_no_battery():
    from app.warehouse import plan_delivery, validate_delivery_plan

    data = WarehouseSimulation().state.model_dump()
    data["robots"][0].update(position={"x": 9, "y": 0}, battery=0)
    simulation = WarehouseSimulation(WarehouseState.model_validate(data))
    cell = Position(x=9, y=0)
    simulation.create_order("o", "p", cell, cell)
    plan = plan_delivery(simulation.state, "o", "robot-1")
    assert plan.pickup_route == plan.delivery_route == (cell,)
    assert plan.total_steps == 0
    assert validate_delivery_plan(simulation.state, plan).route_valid
    simulation.assign_order("o", "robot-1")
    for step in plan.pickup_route[1:]:
        simulation.move_robot("robot-1", step)
    simulation.pickup_package("o", "robot-1")
    for step in plan.delivery_route[1:]:
        simulation.move_robot("robot-1", step)
    simulation.deliver_package("o", "robot-1")
    assert simulation.get_order("o").status == OrderStatus.DELIVERED
    assert simulation.get_robot("robot-1").battery == 0


def test_revision_tracks_real_changes_only(simulation):
    assert simulation.state.revision == 0
    cell = Position(x=1, y=0)
    simulation.remove_blocked_cell(cell)
    assert simulation.state.revision == 0
    simulation.add_blocked_cell(cell)
    assert simulation.state.revision == 1
    simulation.add_blocked_cell(cell)
    assert simulation.state.revision == 1
    with pytest.raises(ValueError):
        simulation.move_robot("robot-1", cell)
    assert simulation.state.revision == 1
    simulation.remove_blocked_cell(cell)
    simulation.move_robot("robot-1", cell)
    assert simulation.state.revision == 3
    assert WarehouseSimulation(simulation.state).state == simulation.state
