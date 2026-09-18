"""Exported schema guidance and prompt semantics at the LLM boundary."""

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.graph.state import (OrderSelection, FleetSelection, FleetExplanation, LLMRoutePlan, SafetyDecision,
                             RobotForecast, PlannedDelivery, LLMMovementPlan, PlannedParking, RobotSchedule)
from app.warehouse.movement import MovementPlan


@pytest.mark.parametrize("model", [OrderSelection, FleetSelection, FleetExplanation, LLMRoutePlan, SafetyDecision,
                                    RobotForecast, PlannedDelivery, LLMMovementPlan, PlannedParking,
                                    RobotSchedule, MovementPlan])
def test_model_and_field_guidance_is_exported(model):
    schema = model.model_json_schema()
    assert len(schema["description"].split()) >= 6
    assert schema["additionalProperties"] is False
    for name in model.model_fields:
        description = schema["properties"][name]["description"]
        assert len(description.split()) >= 6, (model.__name__, name, description)
        assert description.lower() not in (name, model.__name__.lower())


@pytest.mark.parametrize("model", [OrderSelection, FleetExplanation, SafetyDecision])
def test_groq_tool_schema_keeps_the_model_guidance(model):
    exported = model.model_json_schema()
    tool = convert_to_openai_tool(model)["function"]
    assert tool["description"] == exported["description"]
    for name in model.model_fields:
        assert tool["parameters"]["properties"][name]["description"] == exported["properties"][name]["description"]


def test_delivery_continuation_and_parking_have_distinct_schema_meanings():
    delivery = LLMRoutePlan.model_json_schema()
    pickup = delivery["properties"]["route_to_pickup"]["description"]
    dropoff = delivery["properties"]["route_to_dropoff"]["description"]
    assert "previous drop-off -> next pickup directly" in pickup
    assert "no parking visit" in pickup
    assert "package remains delivered at the drop-off" in dropoff
    assert "permanent final position" in dropoff
    assert "no assigned order remains" in LLMMovementPlan.model_json_schema()["description"]
    forecast = RobotForecast.model_json_schema()["properties"]["robot"]["description"]
    final = RobotSchedule.model_json_schema()["properties"]["projected_robot"]["description"]
    assert "Final parking cost is not yet included" in forecast
    assert "all delivery and final departure steps" in final


def test_guidance_preserves_required_fields_and_strict_validation():
    assert set(FleetSelection.model_json_schema()["required"]) == {"robot_id", "explanation"}
    assert FleetSelection(robot_id=None, explanation="No available robot").robot_id is None
    with pytest.raises(ValueError):
        SafetyDecision(approved="true", explanation="Not a boolean")
    with pytest.raises(ValueError):
        LLMRoutePlan(robot_id="r", order_id="o", route_to_pickup=[], route_to_dropoff=[], explanation="Empty")


def test_fleet_schemas_separate_authoritative_assignment_from_groq_explanation():
    selection = FleetSelection.model_json_schema()
    description = selection["properties"]["robot_id"]["description"]
    assert "Set by code" in description and "Groq cannot override" in description
    assert "Previous assignments create no preference" in description
    explanation = FleetExplanation.model_json_schema()
    assert set(explanation["properties"]) == set(explanation["required"]) == {"explanation"}
    assert "current projected state" in explanation["description"]
    assert "Do not choose or recommend a different robot" in explanation["properties"]["explanation"]["description"]
    with pytest.raises(ValueError):
        FleetExplanation(robot_id="r2", explanation="Override")


def test_hybrid_safety_schema_preserves_hard_facts():
    safety = SafetyDecision.model_json_schema()
    assert "trusted deterministic findings" in safety["description"]
    assert "hard failures require false" in safety["properties"]["approved"]["description"]
