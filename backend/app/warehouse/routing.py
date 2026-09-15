"""Reusable A* routing with no simulation mutation or orchestration dependency."""

from collections.abc import Iterable
from heapq import heappop, heappush
from itertools import count

from .models import Position


def astar_path(
    width: int,
    height: int,
    start: Position,
    goal: Position,
    obstacles: Iterable[Position] = (),
    blocked_cells: Iterable[Position] = (),
) -> list[Position] | None:
    """Return a shortest orthogonal route including start and goal, or None.

    Movement costs one per cell. Manhattan distance is the heuristic. Neighbors
    are considered in +x, +y, -x, -y order; equal priorities use insertion order.
    A blocked start/goal or disconnected goal returns None. A free start equal
    to goal returns [start]. Invalid dimensions or out-of-bounds input cells
    raise ValueError. Coordinates use the existing Position domain model.

    Inputs are read once and never modified. Robot occupancy, battery, execution,
    and changes to the warehouse after planning are outside this function's scope.
    Callers can include other robots' occupied cells in blocked_cells if needed.
    """
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("Grid dimensions must be positive integers")

    def inside(cell: Position) -> bool:
        return 0 <= cell.x < width and 0 <= cell.y < height

    unavailable = frozenset(obstacles) | frozenset(blocked_cells)
    if not inside(start) or not inside(goal) or any(not inside(cell) for cell in unavailable):
        raise ValueError("Start, goal, and obstacle/blocked cells must be inside the grid")
    if start in unavailable or goal in unavailable:
        return None

    def heuristic(cell: Position) -> int:
        return abs(cell.x - goal.x) + abs(cell.y - goal.y)

    sequence = count()
    frontier = [(heuristic(start), next(sequence), 0, start)]
    best_cost = {start: 0}
    previous: dict[Position, Position] = {}

    while frontier:
        _, _, cost, current = heappop(frontier)
        if cost != best_cost[current]:
            continue  # A cheaper entry for this cell was queued later.
        if current == goal:
            route = [current]
            while current in previous:
                current = previous[current]
                route.append(current)
            route.reverse()
            return route

        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            neighbor = Position(x=current.x + dx, y=current.y + dy)
            if not inside(neighbor) or neighbor in unavailable:
                continue
            new_cost = cost + 1
            if neighbor in best_cost and new_cost >= best_cost[neighbor]:
                continue
            best_cost[neighbor] = new_cost
            previous[neighbor] = current
            heappush(frontier, (new_cost + heuristic(neighbor), next(sequence), new_cost, neighbor))

    return None
