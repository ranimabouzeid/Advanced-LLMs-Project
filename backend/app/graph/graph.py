"""Sequential batch graph; role nodes communicate only through typed shared state."""

from functools import partial

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from . import batch
from .schedules import finalize
from .state import WarehouseGraphState
from .tools import OrderTools, FleetTools, RouteTools, SafetyTools


def after_dispatch(state: WarehouseGraphState) -> str:
    return "order" if state.command == "plan" else "safety"


def after_order(state: WarehouseGraphState) -> str:
    if state.run_outcome == "no_work":
        return "finish"
    return "fleet" if state.run_outcome == "running" else "failure"


def after_fleet(state: WarehouseGraphState) -> str:
    if state.run_outcome == "no_robot":
        return "collect"
    return "route" if state.run_outcome == "running" else "failure"


def after_route(state: WarehouseGraphState) -> str:
    if state.run_outcome == "unreachable":
        return "collect"
    return "safety" if state.run_outcome == "running" else "failure"


def after_safety(state: WarehouseGraphState) -> str:
    if state.node_activity and state.node_activity[-1].status == "failed":
        return "failure"
    if state.projected_warehouse is not None:
        if state.safety is not None and not state.safety.approved:
            return "retry_route" if state.replan_count < state.max_replans else "collect"
        return "collect"
    if state.run_outcome == "ready" and state.execution_requested:
        return "execution"
    if state.planning_outcome == "stale" and state.replan_count < state.max_replans:
        return "replan"
    return "failure"


def after_collect(state: WarehouseGraphState) -> str:
    return "order" if state.planning_index < len(state.planning_queue) else "finish"


def build_graph(*, client: BaseChatModel, order_tools: OrderTools | None = None,
                fleet_tools: FleetTools | None = None, route_tools: RouteTools | None = None,
                safety_tools: SafetyTools | None = None, checkpointer=None):
    """One shared client, one saver, distinct roles, finite model-selected order loop."""
    graph = StateGraph(WarehouseGraphState)
    graph.add_node("dispatch", batch.dispatch)
    graph.add_node("order", partial(batch.order, client=client, tools=order_tools))
    graph.add_node("fleet", partial(batch.fleet, client=client, tools=fleet_tools))
    graph.add_node("route", partial(batch.route, client=client, tools=route_tools))
    graph.add_node("safety", partial(batch.safety, client=client, tools=safety_tools))
    graph.add_node("retry_route", partial(batch.retry_route, client=client, tools=route_tools))
    graph.add_node("collect", batch.collect)
    graph.add_node("finish", partial(finalize, client=client, route_tools=route_tools, safety_tools=safety_tools))
    graph.add_node("replan", batch.replan)
    graph.add_node("execution", batch.execution)
    graph.add_node("failure", batch.failure)
    graph.add_edge(START, "dispatch")
    for node, selector, destinations in (
        ("dispatch", after_dispatch, ["order", "safety"]),
        ("order", after_order, ["fleet", "finish", "failure"]),
        ("fleet", after_fleet, ["route", "collect", "failure"]),
        ("route", after_route, ["safety", "collect", "failure"]),
        ("retry_route", after_route, ["safety", "collect", "failure"]),
        ("safety", after_safety, ["collect", "execution", "replan", "retry_route", "failure"]),
        ("collect", after_collect, ["order", "finish"]),
    ):
        graph.add_conditional_edges(node, selector, destinations)
    graph.add_edge("replan", "order")
    graph.add_edge("finish", END)
    graph.add_edge("execution", END)
    graph.add_edge("failure", END)
    # Queue length bounds planning; max_replans bounds replacements. The default
    # LangGraph limit of 25 steps would incorrectly stop a small valid batch.
    return graph.compile(checkpointer=checkpointer).with_config(recursion_limit=10000)
