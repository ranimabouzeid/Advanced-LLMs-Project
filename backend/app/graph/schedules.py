"""Finalize independent assignments into physical, sequential robot schedules."""

from . import batch
from .agents import route_agent, safety_agent, parking_route, parking_safety
from .state import NodeActivity, PlannedDelivery, PlannedParking, RobotSchedule, WarehouseGraphState
from app.warehouse.models import WarehouseState
from app.warehouse.movement import execute_parking


class ScheduleUnavailable(ValueError):
    """A public planning limitation, distinct from provider/infrastructure errors."""


def project_parking(warehouse, plan):
    """Derive final projected position and battery after parking without mutating committed state.

    Endpoint integrity is validated here; actual movement is checked at execution.
    """
    return WarehouseState.model_validate({**warehouse.model_dump(), "revision": warehouse.revision + 1,
        "robots": [{**r.model_dump(), "position": plan.route[-1], "battery": r.battery - plan.total_steps}
                   if r.id == plan.robot_id else r for r in warehouse.robots]})


def parking_target(warehouse, robot_id, reserved):
    """Choose the first free designated staging cell, excluding pickups and other reservations."""
    excluded = (warehouse.obstacles | warehouse.blocked_cells | warehouse.dropoff_locations
                | {o.package.pickup for o in warehouse.orders}
                | {r.position for r in warehouse.robots if r.id != robot_id} | set(reserved))
    return next((cell for cell in warehouse.parking_cells if cell not in excluded), None)


def finalize(state, *, client, route_tools=None, safety_tools=None):
    """Finalize assignment forecasts into sequential robot schedules without committed movement.

    Each robot chains drop-off -> next pickup, then parks once after its last
    assignment. Route/Safety use the shared LLM with bounded retries. A failed
    finalization leaves no actionable partial schedule.
    """
    assignments = [item for item in state.planned_deliveries if item.status == "approved"]
    robots = list(dict.fromkeys(item.robot_id for item in assignments))
    # Recover idle robots left on service cells by a previously rejected parking
    # move, even when every package has already been delivered.
    robots = [r.id for r in state.warehouse.robots if r.id not in robots and r.status == "idle"
              and r.carried_package_id is None and r.position in state.warehouse.dropoff_locations] + robots
    if not robots:
        return batch.finish(state)
    warehouse, activity, count = state.warehouse, state.node_activity, state.replan_count
    records, schedules, reserved = [], [], set()
    try:
        for robot_id in robots:
            group = [item for item in assignments if item.robot_id == robot_id]
            for index, item in enumerate(group):
                next_item = group[index + 1] if index + 1 < len(group) else None
                context = {"phase": "schedule_finalization", "order_ids": [d.order_id for d in group],
                           "next_order_id": next_item.order_id if next_item else None,
                           "departure": "next_pickup" if next_item else "parking/staging"}
                local = WarehouseGraphState(warehouse=warehouse, command="plan",
                    order_selection=item.selection, selected_robot_id=robot_id,
                    node_activity=activity, replan_count=count, max_replans=state.max_replans)
                while True:
                    update = route_agent(local, client=client, tools=route_tools, schedule_context=context)
                    local = WarehouseGraphState.model_validate({**local.model_dump(), **update, "replan_count": count})
                    activity = local.node_activity
                    if local.run_outcome != "running":
                        raise ScheduleUnavailable("Final delivery route unavailable")
                    update = safety_agent(local, client=client, tools=safety_tools, schedule_context=context)
                    local = WarehouseGraphState.model_validate({**local.model_dump(), **update})
                    activity = local.node_activity
                    if local.safety is None:
                        raise ValueError("Final safety decision unavailable")
                    if local.safety.approved:
                        break
                    if count >= state.max_replans:
                        raise ScheduleUnavailable("Schedule replan limit exhausted")
                    count += 1
                    local = WarehouseGraphState.model_validate({**local.model_dump(), "replan_count": count})
                record = PlannedDelivery(order_id=item.order_id, selection=item.selection,
                    robot_id=robot_id, delivery_plan=local.delivery_plan, safety=local.safety, status="approved")
                records.append(record)
                warehouse = batch.project(warehouse, record.delivery_plan)
            target = parking_target(warehouse, robot_id, reserved)
            if target is None:
                raise ScheduleUnavailable("No free parking cell; schedule cannot be finalized")
            previous = feedback = None
            while True:
                plan = parking_route(warehouse, robot_id, target, client=client, previous=previous, feedback=feedback)
                activity = (*activity, NodeActivity(node="route", status="completed", message="Final parking route proposed"))
                feedback = parking_safety(warehouse, plan, client=client)
                activity = (*activity, NodeActivity(node="safety", status="completed" if feedback.approved else "rejected",
                                                    message=feedback.explanation))
                if feedback.approved:
                    break
                if count >= state.max_replans:
                    raise ScheduleUnavailable("Parking replan limit exhausted")
                count += 1
                previous = plan
            warehouse = project_parking(warehouse, plan)
            reserved.add(target)
            schedules.append(RobotSchedule(robot_id=robot_id, order_ids=tuple(item.order_id for item in group),
                parking=PlannedParking(plan=plan, safety=feedback),
                projected_robot=next(r for r in warehouse.robots if r.id == robot_id)))
    except Exception as exc:
        # Provider details and partially finalized proposals never become actionable.
        reason = str(exc) if isinstance(exc, ScheduleUnavailable) else "Schedule finalization failed; no execution permitted"
        records = tuple(PlannedDelivery.model_validate({**item.model_dump(), "status": "unplannable", "reason": reason})
                        if item.status == "approved" else item for item in state.planned_deliveries)
        return dict(planned_deliveries=records, robot_schedules=(), robot_forecasts=(),
            projected_warehouse=None, planning_queue=(), planning_index=0,
            order_selection=None, selected_robot_id=None, delivery_plan=None, safety=None,
            execution_requested=False, planning_outcome="failed", run_outcome="failed",
            replan_count=count, error_message=reason,
            node_activity=(*activity, NodeActivity(node="route", status="failed", message=reason)))
    records.extend(item for item in state.planned_deliveries if item.status != "approved")
    candidate = WarehouseGraphState.model_validate({**state.model_dump(), "planned_deliveries": records,
        "robot_schedules": schedules, "node_activity": activity, "replan_count": count})
    return {**batch.finish(candidate), "run_outcome": "ready", "error_message": None,
            "planned_deliveries": tuple(records), "robot_schedules": tuple(schedules),
            "node_activity": activity, "replan_count": count}


def review(state, *, client, safety_tools=None):
    """Reassess every finalized delivery and parking leg with the LLM before execution.

    Rejection or revision mismatch requests a review-only replacement; provider
    failures stop the command. All intermediate warehouse changes are projected.
    """
    warehouse, activity = state.warehouse, state.node_activity
    records, schedules = [], []
    by_id = {item.order_id: item for item in state.planned_deliveries}

    def rejected(message):
        """Request bounded replacement planning with execution intent revoked."""
        return dict(run_outcome="failed", execution_requested=False, planning_outcome="stale",
                    error_message=message, node_activity=activity, safety=None)

    try:
        for schedule in state.robot_schedules:
            for index, order_id in enumerate(schedule.order_ids):
                item = by_id[order_id]
                local = WarehouseGraphState(warehouse=warehouse, command="execute", execution_requested=True,
                    order_selection=item.selection, selected_robot_id=item.robot_id,
                    delivery_plan=item.delivery_plan, node_activity=activity)
                next_order = schedule.order_ids[index + 1] if index + 1 < len(schedule.order_ids) else None
                update = safety_agent(local, client=client, tools=safety_tools, schedule_context={
                    "phase": "execute_review", "order_ids": list(schedule.order_ids),
                    "next_order_id": next_order, "departure": "next_pickup" if next_order else "parking/staging",
                    "parking_target": schedule.parking.plan.route[-1].model_dump()})
                activity = update["node_activity"]
                if activity[-1].status == "failed":
                    return {**update, "node_activity": activity}
                if item.delivery_plan.warehouse_revision != warehouse.revision or not update["safety"].approved:
                    return rejected("Delivery requires a new schedule and review")
                records.append(PlannedDelivery.model_validate({**item.model_dump(), "safety": update["safety"],
                                                               "status": "approved", "reason": None}))
                warehouse = batch.project(warehouse, item.delivery_plan)
            parking = schedule.parking
            decision = parking_safety(warehouse, parking.plan, client=client)
            activity = (*activity, NodeActivity(node="safety", status="completed" if decision.approved else "rejected",
                                                message=decision.explanation))
            if parking.plan.warehouse_revision != warehouse.revision or not decision.approved:
                return rejected("Parking requires a new schedule and review")
            warehouse = project_parking(warehouse, parking.plan)
            schedules.append(RobotSchedule.model_validate({**schedule.model_dump(),
                "parking": {**parking.model_dump(), "safety": decision, "status": "approved", "reason": None}}))
    except Exception:
        return dict(run_outcome="failed", execution_requested=False, safety=None,
                    error_message="Schedule review failed", node_activity=(*activity,
                    NodeActivity(node="safety", status="failed", message="Schedule review failed")))
    records.extend(item for item in state.planned_deliveries if item.status == "unplannable")
    return dict(planned_deliveries=tuple(records), robot_schedules=tuple(schedules),
                node_activity=activity, safety=records[0].safety if records else None,
                run_outcome="ready", planning_outcome="planned")


def execute(state):
    """Apply approved schedules with atomic delivery and parking simulation steps.

    Typed movement failure retains completed steps and stops dependent work;
    unexpected errors propagate so the session preserves its previous commit.
    """
    if (state.command != "execute" or not state.execution_requested or state.run_outcome != "ready"
            or not state.robot_schedules
            or state.batch_revision != state.warehouse_revision
            or any(s.parking.status != "approved" or not s.parking.safety.approved for s in state.robot_schedules)
            or any(d.status not in ("approved", "unplannable") for d in state.planned_deliveries)):
        return batch.failure(state)
    warehouse, stopped, completed = state.warehouse, False, 0
    records = {item.order_id: item for item in state.planned_deliveries}
    schedules = []
    for schedule in state.robot_schedules:
        for order_id in schedule.order_ids:
            item = records[order_id]
            if stopped:
                records[order_id] = PlannedDelivery.model_validate({**item.model_dump(), "status": "not_executed",
                    "reason": "Earlier schedule step failed; Plan again"})
                continue
            result = batch.apply_delivery(warehouse, item.delivery_plan)
            if result.success:
                warehouse, completed = result.final_state, completed + 1
                records[order_id] = PlannedDelivery.model_validate({**item.model_dump(), "status": "delivered"})
            else:
                stopped = True
                records[order_id] = PlannedDelivery.model_validate({**item.model_dump(), "status": "failed",
                    "reason": "Atomic delivery rejected"})
        parking = schedule.parking
        if stopped:
            status, reason = "not_executed", "Earlier schedule step failed"
        else:
            result = execute_parking(warehouse, parking.plan)
            status, reason = ("completed", None) if result.success else ("failed", result.error)
            if result.success:
                warehouse = result.final_state
            else:
                stopped = True
        schedules.append(RobotSchedule.model_validate({**schedule.model_dump(),
            "parking": {**parking.model_dump(), "status": status, "reason": reason}}))
    outcome = "partial" if stopped and completed else "failed" if stopped else "delivered"
    return dict(warehouse=warehouse, planned_deliveries=tuple(records[item.order_id] for item in state.planned_deliveries),
        robot_schedules=tuple(schedules), run_outcome=outcome, order_selection=None, selected_robot_id=None,
        delivery_plan=None, safety=None, planning_outcome="not_planned", execution_requested=False,
        error_message="Schedule stopped; completed deliveries preserved. Plan remaining work again" if stopped else None,
        node_activity=(*state.node_activity, NodeActivity(node="execution", status="rejected" if stopped else "completed",
                                                       message=f"Completed {completed} deliveries; parking included")))
