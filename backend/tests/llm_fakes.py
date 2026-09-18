"""Offline model fixture. Any A*/validation here creates MOCK responses only.

Production agents do not call these routines. Scripted responses can deliberately
disagree with them, demonstrating that decisions come from the injected model.
"""

import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from app.graph.state import FleetSelection, LLMRoutePlan, LLMMovementPlan, OrderSelection, SafetyDecision
from app.warehouse import DeliveryPlan, WarehouseState, plan_delivery, validate_delivery_plan
from app.warehouse.models import Robot, Position
from app.warehouse.routing import astar_path
from app.warehouse.validation import validate_route
from app.warehouse.movement import MovementPlan


class Fake(FakeMessagesListChatModel):
    auto_select: bool = False
    scripts: dict[str, list[dict]] = Field(default_factory=dict)
    calls: list = Field(default_factory=list)

    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert method == "function_calling"
        def invoke(messages):
            payload = json.loads(messages[-1].content)
            self.calls.append((schema, payload, messages[0].content))
            if schema.__name__ in self.scripts and self.scripts[schema.__name__]:
                return schema.model_validate(self.scripts[schema.__name__].pop(0))
            if not self.auto_select:
                return schema.model_validate_json(self.invoke(messages).content)
            return automatic(schema, payload)
        return RunnableLambda(invoke)


def client(*outputs, scripts=None):
    return Fake(auto_select=not outputs, scripts=scripts or {},
                responses=[AIMessage(content=json.dumps(output)) for output in outputs] or [AIMessage(content="unused")])


def automatic(schema, data):
    if schema is OrderSelection:
        return schema(order_id=data["pending_orders"][0]["id"], explanation="Mock selects oldest")
    if schema is FleetSelection:
        feasible = [c for c in data["candidates"] if c["feasible"]]
        chosen = min(feasible, key=lambda c: (c["total_cost"], c["robot"]["id"])) if feasible else None
        return schema(robot_id=chosen["robot"]["id"] if chosen else None, explanation="Mock minimum-cost choice" if chosen else "No feasible candidate: unavailable, unreachable or insufficient battery")
    warehouse = WarehouseState.model_validate(data["warehouse"])
    if schema is LLMMovementPlan:
        robot = next(r for r in warehouse.robots if r.id == data["robot_id"])
        route = astar_path(warehouse.width, warehouse.height, robot.position, Position.model_validate(data["target"]),
            warehouse.obstacles, warehouse.blocked_cells | {r.position for r in warehouse.robots if r.id != robot.id})
        return schema(robot_id=robot.id, route=route, explanation="Mock parking route")
    if schema is SafetyDecision and "movement_plan" in data:
        plan = MovementPlan.model_validate(data["movement_plan"])
        robot = next(r for r in warehouse.robots if r.id == plan.robot_id)
        valid = validate_route(warehouse, robot.id, robot.position, plan.route[-1], plan.route).route_valid
        valid = valid and robot.battery >= plan.total_steps
        return schema(approved=valid, conflicts=[] if valid else ["Parking route invalid"], explanation="Mock parking check")
    order_id = data["selected_order"]["id"]
    robot_id = data["selected_robot"]["id"]
    if schema is LLMRoutePlan:
        plan = plan_delivery(warehouse, order_id, robot_id)
        return schema(order_id=order_id, robot_id=robot_id,
                      outcome="planned" if plan else "unreachable",
                      route_to_pickup=plan.pickup_route if plan else None,
                      route_to_dropoff=plan.delivery_route if plan else None,
                      explanation="Mock route coordinates" if plan else "Mock reports unreachable")
    if schema is SafetyDecision:
        result = validate_delivery_plan(warehouse, DeliveryPlan.model_validate(data["delivery_plan"]))
        conflicts = tuple(issue.message for issue in result.reasons) + tuple(
            f"Occupied by {item.robot_id}" for item in result.conflicts)
        return schema(approved=result.route_valid, conflicts=conflicts,
                      explanation="Mock approves" if result.route_valid else "Mock rejects; revise the route")
    raise AssertionError(f"Unexpected schema: {schema}")
