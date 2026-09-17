"""Fleet decisions come from structured model output, never deterministic ranking."""
import pytest
from langchain_core.runnables import RunnableLambda
from app.graph.agents import fleet_agent
from app.graph.state import FleetSelection, OrderSelection, WarehouseGraphState
from app.graph.tools import FleetTools
from app.warehouse import Order, Package, Position, Robot, WarehouseState
from llm_fakes import client, Fake


def make_state(*, batteries=(100, 100, 100), statuses=("idle", "idle", "idle"),
               positions=((0, 0), (0, 2), (0, 4)), blocked=(), pickup=(2, 0), reverse=False):
    robots = tuple(Robot(id=f"r{i + 1}", position=Position(x=xy[0], y=xy[1]),
                         battery=batteries[i], status=statuses[i]) for i, xy in enumerate(positions))
    order = Order(id="o", package=Package(id="p", pickup=Position(x=pickup[0], y=pickup[1])),
                  dropoff=Position(x=9, y=0))
    warehouse = WarehouseState(robots=tuple(reversed(robots)) if reverse else robots,
                               orders=(order,), dropoff_locations=frozenset({order.dropoff}),
                               blocked_cells=frozenset(Position(x=x, y=y) for x, y in blocked))
    return WarehouseGraphState(warehouse=warehouse, command="plan",
                               order_selection=OrderSelection(order_id="o", explanation="Pending"))



@pytest.mark.parametrize("robot_id", ["r1", "r2", "r3"])
def test_model_chooses_any_available_robot_not_minimum_cost(robot_id, monkeypatch):
    state = make_state()
    model = client({"robot_id": robot_id, "explanation": "My choice"})
    def forbidden(*args, **kwargs):
        raise AssertionError("No deterministic planner in Fleet")
    monkeypatch.setattr("app.warehouse.routing.plan_delivery", forbidden)
    before = state.model_dump_json()
    update = fleet_agent(state, client=model)
    assert update["selected_robot_id"] == robot_id and update["run_outcome"] == "running"
    assert model.calls[0][0] is FleetSelection and len(model.calls) == 1
    payload = model.calls[0][1]
    assert payload["robots"] == [r.model_dump(mode="json") for r in state.warehouse.robots]
    assert payload["selected_order"]["id"] == "o" and "warehouse" in payload
    assert "select the robot only" in model.calls[0][2]
    assert state.model_dump_json() == before


@pytest.mark.parametrize("robot_id,status", [("missing", "idle"), ("r1", "busy"),
                                            ("r1", "charging"), ("r1", "offline")])
def test_unknown_or_unavailable_model_selection_fails(robot_id, status):
    state = make_state(statuses=(status, "idle", "idle"))
    update = fleet_agent(state, client=client({"robot_id": robot_id, "explanation": "Choice"}))
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None


def test_model_can_report_no_suitable_robot():
    model = client({"robot_id": None, "explanation": "Battery inadequate"})
    update = fleet_agent(make_state(), client=model)
    assert update["run_outcome"] == "no_robot" and len(model.calls) == 1


@pytest.mark.parametrize("output", [{}, {"robot_id": "r1"}, {"robot_id": "r1", "explanation": " "},
                                    {"robot_id": "r1", "explanation": "Choice", "cost": 1}])
def test_malformed_fleet_output_is_clean_failure(output):
    update = fleet_agent(make_state(), client=client(output))
    assert update["run_outcome"] == "failed" and update["safety"] is None


@pytest.mark.parametrize("error", [TimeoutError("private"), RuntimeError("private")])
def test_model_failure_does_not_leak_provider_error(error):
    class Broken(Fake):
        def with_structured_output(self, *args, **kwargs):
            def fail(_):
                raise error
            return RunnableLambda(fail)
    update = fleet_agent(make_state(), client=Broken(responses=[]))
    assert update["run_outcome"] == "failed" and "private" not in update["error_message"]


def test_fleet_tools_are_retrieval_only():
    state = make_state()
    tools = FleetTools()
    assert tools.robot_records(state.warehouse) is state.warehouse.robots
    assert tools.order_record(state.warehouse, "o") is state.warehouse.orders[0]
    assert not hasattr(tools, "evaluate_candidate")
    with pytest.raises(KeyError):
        tools.order_record(state.warehouse, "missing")
