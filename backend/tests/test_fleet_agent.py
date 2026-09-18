"""Deterministic Fleet authority with optional structured Groq commentary."""
import pytest
from langchain_core.runnables import RunnableLambda
from app.graph.agents import fleet_agent
from app.graph.state import FleetExplanation, OrderSelection, WarehouseGraphState
from app.graph.tools import FleetCandidate, FleetTools
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



@pytest.mark.parametrize("robot_id", ["r1", "r2", "r3", "missing", None])
def test_model_cannot_override_deterministic_assignment(robot_id):
    state = make_state(batteries=(8, 100, 100))  # r2 is minimum feasible.
    model = client({"robot_id": robot_id, "explanation": "Choose a different robot"})
    before = state.model_dump_json()
    update = fleet_agent(state, client=model)
    assert update["selected_robot_id"] == "r2" and update["run_outcome"] == "running"
    assert model.calls[0][0] is FleetExplanation and len(model.calls) == 1
    assert "Groq explanation unavailable" in update["node_activity"][-1].message
    assert "Choose a different robot" not in update["node_activity"][-1].message
    payload = model.calls[0][1]
    assert payload["selected_robot_id"] == "r2" and payload["minimum_robot_ids"] == ["r2"]
    assert payload["robots"] == [r.model_dump(mode="json") for r in state.warehouse.robots]
    assert "not the assignment authority" in model.calls[0][2]
    assert state.model_dump_json() == before


def test_valid_groq_explanation_describes_the_deterministic_winner():
    model = client({"explanation": "r3 has the lowest complete projected A* cost and sufficient battery."})
    state = make_state(positions=((0, 4), (0, 2), (0, 0)))
    update = fleet_agent(state, client=model)
    assert update["selected_robot_id"] == "r3" and update["run_outcome"] == "running"
    assert "Groq: r3 has the lowest complete projected A* cost" in update["node_activity"][-1].message
    assert model.calls[0][1]["selected_robot_id"] == "r3"


@pytest.mark.parametrize("status", ["busy", "charging", "offline"])
def test_unavailable_robot_is_excluded_deterministically(status):
    update = fleet_agent(make_state(statuses=(status, "idle", "idle")), client=client())
    assert update["selected_robot_id"] == "r2" and update["run_outcome"] == "running"


def test_no_feasible_robot_is_determined_before_commentary():
    model = client({"explanation": "All projected batteries are inadequate."})
    update = fleet_agent(make_state(batteries=(0, 0, 0)), client=model)
    assert update["run_outcome"] == "no_robot" and update["selected_robot_id"] is None
    assert len(model.calls) == 1 and model.calls[0][1]["selected_robot_id"] is None


@pytest.mark.parametrize("output", [{}, {"robot_id": "r1"}, {"explanation": " "},
                                    {"explanation": "Comment", "cost": 1}, {"explanation": "x" * 301}])
def test_malformed_commentary_keeps_winner_and_trusted_summary(output):
    update = fleet_agent(make_state(), client=client(output))
    assert update["run_outcome"] == "running" and update["selected_robot_id"] == "r1"
    assert update["safety"] is None and update["error_message"] is None
    assert "2 pickup + 7 delivery = 9 steps" in update["node_activity"][-1].message
    assert "Groq explanation unavailable" in update["node_activity"][-1].message


@pytest.mark.parametrize("error", [TimeoutError("private"), RuntimeError("private")])
def test_explanation_provider_failure_does_not_block_or_leak_details(error, caplog):
    class Broken(Fake):
        def with_structured_output(self, *args, **kwargs):
            def fail(_):
                raise error
            return RunnableLambda(fail)
    update = fleet_agent(make_state(), client=Broken(responses=[]))
    assert update["run_outcome"] == "running" and update["selected_robot_id"] == "r1"
    assert "private" not in update["node_activity"][-1].message
    assert "deterministic assignment retained" in caplog.text


@pytest.mark.parametrize("problem", ["missing", "duplicate", "wrong_robot", "total", "feasibility"])
def test_invalid_candidate_data_stops_before_commentary(problem):
    class Broken(FleetTools):
        def candidate_records(self, *args):
            candidates = list(super().candidate_records(*args))
            if problem == "missing":
                return candidates[:-1]
            if problem == "duplicate":
                return [candidates[0], candidates[0], candidates[2]]
            changes = {"wrong_robot": {"robot": Robot(id="unknown", position=Position(x=5, y=0))},
                       "total": {"total_cost": 0}, "feasibility": {"total_cost": None}}[problem]
            candidates[0] = candidates[0].model_copy(update=changes)
            return candidates
    model = client()
    update = fleet_agent(make_state(), client=model, tools=Broken())
    assert update["run_outcome"] == "failed" and update["selected_robot_id"] is None
    assert not model.calls and "candidate evaluation" in update["error_message"]


def test_fleet_tools_retrieve_records_and_compute_costs():
    state = make_state()
    tools = FleetTools()
    assert tools.robot_records(state.warehouse) is state.warehouse.robots
    assert tools.order_record(state.warehouse, "o") is state.warehouse.orders[0]
    candidates = tools.candidate_records(state.warehouse, "o", state.warehouse.robots)
    assert [c.total_cost for c in candidates] == [9, 11, 13]
    with pytest.raises(KeyError):
        tools.order_record(state.warehouse, "missing")
