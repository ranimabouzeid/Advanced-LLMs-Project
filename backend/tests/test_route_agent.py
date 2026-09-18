"""Deterministic shortest paths, projected starts and shared Fleet cost semantics."""
import pytest
from app.graph.agents import route_agent, parking_route
from app.graph.state import OrderSelection, RobotForecast, WarehouseGraphState
from app.graph.tools import FleetTools, RouteTools
from app.graph import batch
from app.warehouse import Order, Package, Position, Robot, WarehouseState, plan_delivery


def p(x, y):
    return Position(x=x, y=y)


def state_for(*, start=(0, 0), pickup=(2, 0), dropoff=(9, 0), blocked=(), other=(0, 2), obstacles=()):
    pos = lambda xy: p(*xy)
    warehouse = WarehouseState(revision=7,
        robots=(Robot(id="r1", position=pos(start)), Robot(id="r2", position=pos(other)),
                Robot(id="r3", position=p(0, 4))),
        orders=(Order(id="o", package=Package(id="pkg", pickup=pos(pickup)), dropoff=pos(dropoff)),),
        dropoff_locations=frozenset({pos(dropoff)}), blocked_cells=frozenset(map(pos, blocked)),
        obstacles=frozenset(map(pos, obstacles)))
    return WarehouseGraphState(warehouse=warehouse, command="plan", selected_robot_id="r1",
                               order_selection=OrderSelection(order_id="o", explanation="Pending"))


@pytest.mark.parametrize("start,pickup,dropoff", [((0,0),(2,0),(9,0)), ((2,0),(2,0),(9,0)),
                                               ((0,0),(2,0),(2,0)), ((2,0),(2,0),(2,0))])
def test_exact_astar_without_any_model(start, pickup, dropoff, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Route must never construct or invoke a model")
    monkeypatch.setattr("app.graph.agents._decide", forbidden)
    monkeypatch.setattr("app.config.create_model_client", forbidden)
    state = state_for(start=start, pickup=pickup, dropoff=dropoff)
    before = state.model_dump()
    update = route_agent(state)
    assert update["run_outcome"] == "running" and update["safety"] is None
    plan = update["delivery_plan"]
    assert plan == plan_delivery(state.warehouse, "o", "r1")
    assert plan.pickup_route[0] == p(*start) and plan.pickup_route[-1] == p(*pickup)
    assert plan.delivery_route[0] == p(*pickup) and plan.delivery_route[-1] == p(*dropoff)
    assert plan.total_steps == abs(start[0]-pickup[0]) + abs(pickup[0]-dropoff[0])
    assert route_agent(state) == update and state.model_dump() == before
    parking = parking_route(state.warehouse, "r1", p(7, 9))
    assert parking.route[0] == p(*start) and parking.route[-1] == p(7, 9)


def test_astar_avoids_shelves_blocks_occupancy_with_legal_adjacent_steps():
    state = state_for(blocked=((1, 0),), obstacles=((1, 1),), other=(0, 3))
    plan = route_agent(state)["delivery_plan"]
    assert plan.total_steps == 13
    occupied = {p(0, 3), p(0, 4)} | state.warehouse.obstacles | state.warehouse.blocked_cells
    for leg in (plan.pickup_route, plan.delivery_route):
        assert not occupied.intersection(leg)
        assert all(state.warehouse.contains(cell) for cell in leg)
        assert all(abs(a.x-b.x) + abs(a.y-b.y) == 1 for a,b in zip(leg, leg[1:]))


@pytest.mark.parametrize("blocked", [((2, 0),), ((9, 0),)])
def test_unreachable_pickup_or_delivery_is_controlled(blocked):
    update = route_agent(state_for(blocked=blocked))
    assert update["run_outcome"] == update["planning_outcome"] == "unreachable"
    assert update["delivery_plan"] is None and update["node_activity"][-1].status == "completed"


def test_unreachable_parking_is_explicit_none():
    state = state_for(blocked=((6, 9), (8, 9), (7, 8)))
    assert parking_route(state.warehouse, "r1", p(7, 9)) is None


@pytest.mark.parametrize("changes", [{}, {"blocked": ((1, 0),)}, {"other": (1, 0)},
                                     {"obstacles": ((1, 0), (1, 1)), "other": (0, 3)}, {"start": (4, 0)}])
def test_fleet_delivery_costs_equal_route_costs(changes):
    state = state_for(**changes)
    candidate = FleetTools().candidate_records(state.warehouse, "o", state.warehouse.robots)[0]
    plan = route_agent(state)["delivery_plan"]
    assert candidate.feasible and candidate.pickup_cost == len(plan.pickup_route)-1
    assert candidate.delivery_cost == len(plan.delivery_route)-1
    assert candidate.total_cost == plan.total_steps


def test_route_uses_selected_projected_endpoint_and_battery_as_fleet_did():
    base = state_for()
    projected = Robot(id="r1", position=p(8, 0), battery=70)
    state = WarehouseGraphState.model_validate({**base.model_dump(), "projected_warehouse": base.warehouse,
        "robot_forecasts": [RobotForecast(robot=projected if r.id == "r1" else r) for r in base.warehouse.robots]})
    robots = tuple(f.robot for f in state.robot_forecasts)
    candidate = FleetTools().candidate_records(state.warehouse, "o", robots)[0]
    plan = batch.route(state)["delivery_plan"]
    assert plan.pickup_route[0] == projected.position
    assert candidate.robot.battery == 70 and candidate.total_cost == plan.total_steps == 13
    assert state.warehouse.robots[0].position == p(0, 0) and state.warehouse.robots[0].battery == 100


def test_changed_blocked_cells_change_path_but_unchanged_input_is_stable():
    original = route_agent(state_for())["delivery_plan"]
    changed = route_agent(state_for(blocked=((1, 0),)))["delivery_plan"]
    assert original.total_steps == 9 and changed.total_steps == 11
    assert changed == route_agent(state_for(blocked=((1, 0),)))["delivery_plan"]


def test_missing_selections_and_unexpected_tool_failure_remain_failures(caplog):
    state = WarehouseGraphState(warehouse=state_for().warehouse, command="plan")
    assert route_agent(state)["run_outcome"] == "failed"
    class Broken(RouteTools):
        def plan_delivery(self, *args):
            raise RuntimeError("Diagnostic planner fault")
    update = route_agent(state_for(), tools=Broken())
    assert update["run_outcome"] == "failed" and update["delivery_plan"] is None
    assert "RuntimeError: Diagnostic planner fault" in caplog.text
