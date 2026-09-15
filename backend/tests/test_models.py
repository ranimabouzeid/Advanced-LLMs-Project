"""Validation of domain records and complete warehouse snapshots."""

import pytest
from pydantic import ValidationError

from app.warehouse import Order, Package, Position, Robot, WarehouseSimulation, WarehouseState


@pytest.mark.parametrize("value", [1.5, "1", True])
def test_coordinates_require_actual_integers(value):
    with pytest.raises(ValidationError):
        Position(x=value, y=0)


@pytest.mark.parametrize("battery", [-1, 101, 2.5, True])
def test_invalid_battery_is_rejected(battery):
    with pytest.raises(ValidationError):
        Robot(id="r", position=Position(x=0, y=0), battery=battery)


@pytest.mark.parametrize("model, fields", [
    (Robot, {"position": Position(x=0, y=0)}),
    (Package, {"pickup": Position(x=1, y=0)}),
    (Order, {"package": Package(id="p", pickup=Position(x=1, y=0)), "dropoff": Position(x=9, y=0)}),
])
def test_blank_identifiers_are_rejected(model, fields):
    with pytest.raises(ValidationError):
        model(id="   ", **fields)


def test_invalid_status_and_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Robot(id="r", position=Position(x=0, y=0), status="flying")
    with pytest.raises(ValidationError):
        Position(x=0, y=0, z=1)


@pytest.mark.parametrize("change", [
    {"width": 9},
    {"height": 11},
    {"dropoff_locations": []},
    {"obstacles": [{"x": 10, "y": 0}]},
    {"blocked_cells": [{"x": -1, "y": 0}]},
    {"dropoff_locations": [{"x": 3, "y": 2}]},
    {"blocked_cells": [{"x": 3, "y": 2}]},
    {"obstacles": [{"x": 0, "y": 0}]},
    {"blocked_cells": [{"x": 0, "y": 0}]},
])
def test_inconsistent_layout_is_rejected(change):
    data = WarehouseSimulation().state.model_dump()
    with pytest.raises(ValidationError):
        WarehouseState.model_validate({**data, **change})


@pytest.mark.parametrize("problem", ["count", "duplicate_id", "collision", "out_of_bounds"])
def test_invalid_robot_layout_is_rejected(problem):
    data = WarehouseSimulation().state.model_dump()
    if problem == "count":
        data["robots"] = data["robots"][:2]
    elif problem == "duplicate_id":
        data["robots"][1]["id"] = data["robots"][0]["id"]
    elif problem == "collision":
        data["robots"][1]["position"] = data["robots"][0]["position"]
    else:
        data["robots"][1]["position"] = {"x": 0, "y": 10}
    with pytest.raises(ValidationError):
        WarehouseState.model_validate(data)


def test_snapshot_json_roundtrip():
    simulation = WarehouseSimulation()
    simulation.create_order("o1", "p1", Position(x=2, y=2), Position(x=9, y=0))
    assert WarehouseState.model_validate_json(simulation.state.model_dump_json()) == simulation.state
    assert WarehouseState.model_validate(simulation.state.model_dump()) == simulation.state
    assert simulation.state.model_dump()["dropoff_locations"] == [{"x": 9, "y": 0}, {"x": 9, "y": 9}]


def test_snapshots_and_nested_models_are_immutable():
    state = WarehouseSimulation().state
    with pytest.raises(ValidationError):
        state.width = 9
    with pytest.raises(ValidationError):
        state.robots[0].battery = 0
    with pytest.raises(ValidationError):
        state.robots[0].position.x = 5
    assert isinstance(state.obstacles, frozenset)
    assert isinstance(state.robots, tuple)
