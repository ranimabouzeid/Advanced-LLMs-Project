"""Deterministic updates and real LangGraph channel merges; no production nodes."""

import pytest
from langgraph.graph import END, START, StateGraph

from app.graph.state import NodeActivity, OrderSelection, WarehouseGraphState
from app.graph.updates import (
    fresh_plan, is_plan_approved, record_safety, replan, replace_warehouse,
    select_order, select_robot,
)
from app.warehouse import Position, WarehouseSimulation, WarehouseState, plan_delivery, validate_delivery_plan


def merge(state, update):
    return WarehouseGraphState.model_validate({**state.model_dump(), **update})


@pytest.fixture
def initial():
    simulation = WarehouseSimulation()
    simulation.create_order("o", "p", Position(x=2, y=0), Position(x=9, y=0))
    simulation.create_order("other", "other-p", Position(x=1, y=1), Position(x=9, y=9))
    return WarehouseGraphState(warehouse=simulation.state, command="execute",
                               execution_requested=True,
                               node_activity=(NodeActivity(node="order", status="completed"),))


def selected(initial):
    state = merge(initial, select_order(initial, OrderSelection(order_id="o", explanation="First pending")))
    return merge(state, select_robot(state, "robot-1"))


def planned(initial):
    state = selected(initial)
    return merge(state, fresh_plan(state, plan_delivery(state.warehouse, "o", "robot-1")))


def approved(initial):
    state = planned(initial)
    return merge(state, record_safety(state, validate_delivery_plan(state.warehouse, state.delivery_plan)))


def test_compiled_graph_partial_update_evolution(initial):
    observed = []
    patches = []

    def step(operation):
        def fixture_node(state: WarehouseGraphState):
            assert isinstance(state, WarehouseGraphState)
            observed.append(state)
            before = state.model_dump_json()
            patch = operation(state)
            assert state.model_dump_json() == before
            assert "command" not in patch and "node_activity" not in patch and "max_replans" not in patch
            patches.append(patch)
            return patch
        return fixture_node

    def mutate(state):
        temporary = WarehouseSimulation(state.warehouse)
        temporary.add_blocked_cell(Position(x=5, y=0))
        return replace_warehouse(state, temporary.state)

    operations = [
        lambda s: select_order(s, OrderSelection(order_id="o", explanation="First pending")),
        lambda s: select_robot(s, "robot-1"),
        lambda s: fresh_plan(s, plan_delivery(s.warehouse, "o", "robot-1")),
        lambda s: record_safety(s, validate_delivery_plan(s.warehouse, s.delivery_plan)),
        mutate,
        lambda s: replan(s, plan_delivery(s.warehouse, "o", "robot-1")),
        lambda s: fresh_plan(s, plan_delivery(s.warehouse, "o", "robot-1")),
    ]
    builder = StateGraph(WarehouseGraphState)
    previous = START
    for index, operation in enumerate(operations):
        name = f"fixture_{index}"
        builder.add_node(name, step(operation))
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    result = WarehouseGraphState.model_validate(builder.compile().invoke(initial))
    observed.append(result)
    assert observed[0].order_selection is None
    assert observed[0].selected_robot_id is observed[0].delivery_plan is observed[0].safety is None
    assert observed[1].order_selection.order_id == "o"
    assert observed[2].selected_robot_id == "robot-1"
    assert observed[3].delivery_plan is not None and observed[3].safety is None
    assert is_plan_approved(observed[4])
    stale = observed[5]
    assert stale.warehouse_revision == initial.warehouse_revision + 1
    assert stale.delivery_plan == observed[4].delivery_plan
    assert stale.delivery_plan.warehouse_revision != stale.warehouse_revision
    assert stale.planning_outcome == "stale" and stale.safety is None
    assert not stale.execution_requested and not is_plan_approved(stale)
    assert observed[6].replan_count == 1
    assert result.replan_count == 0 and result.safety is None
    assert result.node_activity == initial.node_activity
    assert patches[4]["safety"] is None  # Explicit None clears a real graph channel.
    assert "delivery_plan" not in patches[4]  # Omission preserves the proposal.


@pytest.mark.parametrize("kind", ["order", "robot"])
def test_selection_invalidates_dependents(initial, kind):
    state = merge(approved(initial), {"error_message": "Old error", "replan_count": 2,
                                      "execution_requested": True})
    patch = (select_order(state, OrderSelection(order_id="other", explanation="Next"))
             if kind == "order" else select_robot(state, "robot-2"))
    result = merge(state, patch)
    assert result.delivery_plan is result.safety is result.error_message is None
    assert result.replan_count == 0 and not result.execution_requested
    assert result.planning_outcome == "not_planned" and result.run_outcome != "ready"
    assert "warehouse" not in patch
    assert state.safety.route_valid and state.replan_count == 2
    if kind == "order":
        assert result.selected_robot_id is None
    else:
        assert "order_selection" not in patch and result.order_selection == state.order_selection


def test_retry_budget_and_fresh_reset(initial):
    state = planned(initial)
    for count in (1, 2, 3):
        patch = replan(state, state.delivery_plan)
        state = merge(state, patch)
        assert state.replan_count == count and state.safety is None
    with pytest.raises(ValueError, match="exhausted"):
        replan(state, state.delivery_plan)
    assert state.replan_count == 3
    assert merge(state, fresh_plan(state, state.delivery_plan)).replan_count == 0


@pytest.mark.parametrize("offset", [-1, 0, 2])
def test_changed_snapshot_requires_one_revision_increment(initial, offset):
    data = initial.warehouse.model_dump()
    data["blocked_cells"] = [{"x": 5, "y": 0}]
    data["revision"] += offset
    with pytest.raises(ValueError, match="exactly once"):
        replace_warehouse(initial, WarehouseState.model_validate(data))


def test_noop_preserves_approval_and_revision(initial):
    state = approved(initial)
    assert replace_warehouse(state, state.warehouse) == {}
    assert is_plan_approved(state)


def test_stale_approval_cannot_pass_even_if_supplied_directly(initial):
    state = approved(initial)
    temporary = WarehouseSimulation(state.warehouse)
    temporary.add_blocked_cell(Position(x=8, y=8))
    stale = merge(state, {"warehouse": temporary.state})
    assert not is_plan_approved(stale)
    with pytest.raises(ValueError, match="deterministic validation"):
        record_safety(stale, state.safety)
    with pytest.raises(ValueError, match="current warehouse revision"):
        fresh_plan(stale, state.delivery_plan)


def test_safety_rejection_and_missing_dependencies(initial):
    with pytest.raises(ValueError):
        select_robot(initial, "robot-1")
    state = approved(initial)
    with pytest.raises(ValueError):
        record_safety(initial, state.safety)
    with pytest.raises(ValueError):
        replan(selected(initial), state.delivery_plan)
    wrong = plan_delivery(state.warehouse, "other", "robot-1")
    with pytest.raises(ValueError, match="match the selected"):
        fresh_plan(state, wrong)
    temporary = WarehouseSimulation(state.warehouse)
    temporary.add_blocked_cell(Position(x=5, y=0))
    stale = merge(state, replace_warehouse(state, temporary.state))
    rejected = merge(stale, record_safety(stale, validate_delivery_plan(stale.warehouse, stale.delivery_plan)))
    assert rejected.safety is not None and not rejected.safety.route_valid
    assert rejected.run_outcome == "failed" and not is_plan_approved(rejected)


def test_completed_order_snapshot_clears_ineligible_selection(initial):
    state = approved(initial)
    temporary = WarehouseSimulation(state.warehouse)
    result = temporary.execute_delivery(state.delivery_plan)
    assert result.success
    updated = merge(state, replace_warehouse(state, result.final_state))
    assert updated.order_selection is updated.selected_robot_id is updated.delivery_plan is updated.safety is None
    assert updated.warehouse_revision == state.warehouse_revision + 1
