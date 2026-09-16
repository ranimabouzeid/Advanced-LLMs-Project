"""Sequential command workflow. No persistence, sessions, or external services."""

from functools import partial
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from app.warehouse import DeliveryPlan, WarehouseSimulation
from .agents import order_agent, fleet_agent, route_agent, safety_agent
from .state import WarehouseGraphState
from .tools import OrderTools, FleetTools, RouteTools, SafetyTools
from .updates import StateUpdate, execution_result, is_plan_approved, retry_result, workflow_failure


class CommandInput(WarehouseGraphState):
    """Only the proposal is permissive at entry so malformed input can fail safely.

    All subsequent nodes use the strict WarehouseGraphState schema.
    """

    delivery_plan: Any = None


def dispatch(state: CommandInput) -> StateUpdate:
    if state.delivery_plan is not None:
        try:
            raw = state.delivery_plan
            DeliveryPlan.model_validate(raw.model_dump() if isinstance(raw, DeliveryPlan) else raw)
        except Exception:
            return dict(delivery_plan=None, safety=None, planning_outcome="failed",
                        execution_requested=False, run_outcome="failed",
                        error_message="Malformed delivery plan")
    # Only this entry point establishes intent; replacement planning clears it.
    return dict(execution_requested=state.command == "execute", safety=None,
                error_message=None, run_outcome="running")


def after_dispatch(state: WarehouseGraphState) -> str:
    if state.run_outcome == "failed":
        return END
    return "order" if state.command == "plan" else "safety"


def after_order(state: WarehouseGraphState) -> str:
    return "fleet" if state.run_outcome == "running" and state.order_selection is not None else END


def after_fleet(state: WarehouseGraphState) -> str:
    return "route" if state.run_outcome == "running" and state.selected_robot_id is not None else END


def after_route(state: WarehouseGraphState) -> str:
    return "safety" if state.planning_outcome == "planned" and state.run_outcome == "running" else END


def actionable_change(state: WarehouseGraphState) -> bool:
    """Only a newer snapshot and routing-related rejection justify another A*."""
    plan, result = state.delivery_plan, state.safety
    return bool(plan is not None and result is not None and not result.route_valid
                and plan.warehouse_revision < state.warehouse_revision
                and state.order_selection is not None
                and plan.order_id == state.order_selection.order_id
                and plan.robot_id == state.selected_robot_id
                and {issue.code for issue in result.reasons}
                <= {"stale_plan", "blocked", "obstacle", "wrong_start"})


def after_safety(state: WarehouseGraphState) -> str:
    if not state.node_activity or state.node_activity[-1].node != "safety":
        return "failure"
    status = state.node_activity[-1].status
    if status == "failed":
        return "failure"
    if status == "completed" and state.run_outcome == "ready" and is_plan_approved(state):
        return "execution" if state.command == "execute" and state.execution_requested else END
    if status == "rejected" and actionable_change(state) and state.replan_count < state.max_replans:
        return "retry_route"
    return "failure"


def failure(state: WarehouseGraphState) -> StateUpdate:
    if state.node_activity and state.node_activity[-1].status == "failed":
        message = state.error_message or "Workflow operation failed"
    elif actionable_change(state) and state.replan_count >= state.max_replans:
        message = "Replan limit exhausted"
    else:
        message = state.error_message or "Safety approval missing or rejection is not replannable"
    return workflow_failure(state, message)


def retry_route(state: WarehouseGraphState, *, tools: RouteTools | None = None) -> StateUpdate:
    # Guard direct invocation too. No tool runs for an unadmitted attempt.
    if after_safety(state) != "retry_route":
        return failure(state)
    return retry_result(state, route_agent(state, tools=tools))


def execution(state: WarehouseGraphState) -> StateUpdate:
    try:
        if (state.command != "execute" or not state.execution_requested
                or state.run_outcome != "ready" or not is_plan_approved(state)):
            return execution_result(state, error="Execution requires current deterministic approval and intent")
        result = WarehouseSimulation(state.warehouse).execute_delivery(state.delivery_plan)
        if not result.success:
            return execution_result(state, error=f"Atomic delivery failed during {result.failed_stage}")
        return execution_result(state, warehouse=result.final_state)
    except Exception:
        return execution_result(state, error="Atomic delivery failed")


def build_graph(*, client: BaseChatModel, order_tools: OrderTools | None = None,
                fleet_tools: FleetTools | None = None, route_tools: RouteTools | None = None,
                safety_tools: SafetyTools | None = None, fleet_model: bool = False,
                safety_model: bool = False):
    """Compile with one injected shared client; optional roles reuse that client."""
    graph = StateGraph(WarehouseGraphState, input_schema=CommandInput)
    graph.add_node("dispatch", dispatch, input_schema=CommandInput)
    graph.add_node("order", partial(order_agent, client=client, tools=order_tools))
    graph.add_node("fleet", partial(fleet_agent, client=client if fleet_model else None, tools=fleet_tools))
    graph.add_node("route", partial(route_agent, tools=route_tools))
    graph.add_node("retry_route", partial(retry_route, tools=route_tools))
    graph.add_node("safety", partial(safety_agent, client=client if safety_model else None, tools=safety_tools))
    graph.add_node("execution", execution)
    graph.add_node("failure", failure)
    graph.add_edge(START, "dispatch")
    for node, selector, destinations in (
        ("dispatch", after_dispatch, ["order", "safety", END]),
        ("order", after_order, ["fleet", END]),
        ("fleet", after_fleet, ["route", END]),
        ("route", after_route, ["safety", END]),
        ("retry_route", after_route, ["safety", END]),
        ("safety", after_safety, ["execution", "retry_route", "failure", END]),
    ):
        graph.add_conditional_edges(node, selector, destinations)
    graph.add_edge("execution", END)
    graph.add_edge("failure", END)
    return graph.compile()
