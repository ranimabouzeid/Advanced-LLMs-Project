"""A* route validity, determinism, edge cases, and independent optimality checks."""

from collections import deque
from itertools import combinations

import pytest

from app.warehouse import Position, WarehouseSimulation, astar_path


def assert_valid_route(route, width, height, start, goal, forbidden=()):
    assert route is not None
    assert route[0] == start
    assert route[-1] == goal
    assert len(set(route)) == len(route)
    for cell in route:
        assert 0 <= cell.x < width and 0 <= cell.y < height
        assert cell not in forbidden
    for first, second in zip(route, route[1:]):
        assert abs(first.x - second.x) + abs(first.y - second.y) == 1


def test_normal_route_is_shortest():
    start, goal = Position(x=1, y=1), Position(x=8, y=7)
    route = astar_path(10, 10, start, goal)
    assert_valid_route(route, 10, 10, start, goal)
    assert len(route) - 1 == 13


def test_route_around_permanent_obstacle():
    start, goal = Position(x=0, y=1), Position(x=2, y=1)
    shelves = {Position(x=1, y=1)}
    route = astar_path(3, 3, start, goal, shelves)
    assert_valid_route(route, 3, 3, start, goal, shelves)
    assert len(route) - 1 == 4


def test_dynamic_block_closes_an_aisle_and_replanning_uses_another():
    start, goal = Position(x=0, y=1), Position(x=4, y=1)
    shelves = {Position(x=2, y=0), Position(x=2, y=2)}
    direct = astar_path(5, 4, start, goal, shelves)
    blocked = {Position(x=2, y=1)}
    detour = astar_path(5, 4, start, goal, shelves, blocked)
    assert len(direct) - 1 == 4
    assert_valid_route(detour, 5, 4, start, goal, shelves | blocked)
    assert len(detour) - 1 == 8
    assert astar_path(5, 4, start, goal, shelves) == direct


def test_unreachable_destination():
    wall = {Position(x=1, y=y) for y in range(3)}
    assert astar_path(3, 3, Position(x=0, y=1), Position(x=2, y=1), wall) is None


def test_start_equals_destination():
    start = Position(x=1, y=1)
    assert astar_path(3, 3, start, start) == [start]


@pytest.mark.parametrize("endpoint", [Position(x=0, y=0), Position(x=2, y=2)])
@pytest.mark.parametrize("kind", ["obstacles", "blocked_cells"])
def test_obstructed_endpoint_has_no_route(endpoint, kind):
    assert astar_path(3, 3, Position(x=0, y=0), Position(x=2, y=2), **{kind: [endpoint]}) is None


def test_start_equals_goal_still_must_be_walkable():
    cell = Position(x=0, y=0)
    assert astar_path(1, 1, cell, cell, blocked_cells=[cell]) is None
    assert astar_path(1, 1, cell, cell) == [cell]


@pytest.mark.parametrize("width, height", [(0, 10), (10, -1), (2.5, 3), (True, 3), (3, "3")])
def test_invalid_dimensions(width, height):
    with pytest.raises(ValueError, match="positive integers"):
        astar_path(width, height, Position(x=0, y=0), Position(x=0, y=0))


@pytest.mark.parametrize("field, cell", [
    ("start", Position(x=-1, y=0)), ("goal", Position(x=3, y=0)),
    ("obstacles", Position(x=0, y=3)), ("blocked_cells", Position(x=0, y=-1)),
])
def test_out_of_bounds_inputs_are_rejected(field, cell):
    kwargs = {"start": Position(x=0, y=0), "goal": Position(x=2, y=2)}
    kwargs[field] = [cell] if field in ("obstacles", "blocked_cells") else cell
    with pytest.raises(ValueError, match="inside the grid"):
        astar_path(3, 3, **kwargs)


def test_tie_breaking_and_input_iteration_order_are_stable():
    start, goal = Position(x=0, y=0), Position(x=2, y=2)
    expected = [start, Position(x=1, y=0), Position(x=2, y=0), Position(x=2, y=1), goal]
    cells = [Position(x=0, y=2), Position(x=1, y=2)]
    assert astar_path(3, 3, start, goal) == expected
    for obstacles in (cells, list(reversed(cells)), set(cells), iter(cells)):
        assert astar_path(3, 3, start, goal, obstacles) == expected


def test_inputs_are_not_mutated_and_overlapping_blocks_are_allowed():
    shelves = {Position(x=1, y=1)}
    blocked = [Position(x=1, y=1), Position(x=1, y=2)]
    before = shelves.copy(), blocked.copy()
    route = astar_path(3, 3, Position(x=0, y=0), Position(x=2, y=2), shelves, blocked)
    assert_valid_route(route, 3, 3, Position(x=0, y=0), Position(x=2, y=2), shelves | set(blocked))
    assert (shelves, blocked) == before


@pytest.mark.parametrize("width, height", [(1, 5), (5, 1), (2, 7)])
def test_narrow_and_rectangular_grids(width, height):
    start, goal = Position(x=0, y=0), Position(x=width - 1, y=height - 1)
    route = astar_path(width, height, start, goal)
    assert_valid_route(route, width, height, start, goal)
    assert len(route) - 1 == width + height - 2


def test_route_can_be_executed_by_existing_simulation():
    simulation = WarehouseSimulation()
    before = simulation.state
    robot = simulation.get_robot("robot-1")
    goal = Position(x=8, y=7)
    occupied = {item.position for item in before.robots if item.id != robot.id}
    route = astar_path(
        before.width, before.height, robot.position, goal,
        before.obstacles, before.blocked_cells | occupied,
    )
    assert simulation.state == before  # Planning alone does not move anything.
    assert_valid_route(route, 10, 10, robot.position, goal, before.obstacles | occupied)
    for cell in route[1:]:
        simulation.move_robot(robot.id, cell)
    assert simulation.get_robot(robot.id).position == goal
    assert simulation.get_robot(robot.id).battery == 100 - (len(route) - 1)


def test_optimality_against_breadth_first_search_for_all_small_layouts():
    """BFS is an independent test oracle only; production routing uses A*."""
    start, goal = Position(x=0, y=0), Position(x=2, y=2)
    candidates = [(x, y) for x in range(3) for y in range(3) if (x, y) not in ((0, 0), (2, 2))]

    def shortest_distance(blocks):
        queue = deque([((0, 0), 0)])
        seen = {(0, 0)}
        while queue:
            (x, y), distance = queue.popleft()
            if (x, y) == (2, 2):
                return distance
            for cell in ((x - 1, y), (x, y - 1), (x + 1, y), (x, y + 1)):
                if 0 <= cell[0] < 3 and 0 <= cell[1] < 3 and cell not in blocks and cell not in seen:
                    seen.add(cell)
                    queue.append((cell, distance + 1))
        return None

    for size in range(len(candidates) + 1):
        for selection in combinations(candidates, size):
            obstacles = {Position(x=x, y=y) for x, y in selection}
            distance = shortest_distance(set(selection))
            route = astar_path(3, 3, start, goal, obstacles)
            if distance is None:
                assert route is None
            else:
                assert_valid_route(route, 3, 3, start, goal, obstacles)
                assert len(route) - 1 == distance
