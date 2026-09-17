"""HTTP contracts backed by real sessions/checkpoints and offline model clients."""

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api.schemas import CommandResponse, SessionResponse
from app.graph.state import WarehouseGraphState
from app.sessions import SessionCommandRejected, SessionCoordinator, SessionExecutionError
from app.warehouse import WarehouseSimulation
from test_graph_workflow import client as fake_client


ORDER = {"order_id": "o", "package_id": "p", "pickup": {"x": 2, "y": 0},
         "dropoff": {"x": 9, "y": 0}}
ROOT = "/api/sessions"


@pytest.fixture
def coordinator():
    return SessionCoordinator(client=fake_client())


@pytest.fixture
def http(coordinator):
    with TestClient(create_app(coordinator=coordinator), raise_server_exceptions=False) as http:
        yield http


def session(http):
    response = http.post(ROOT)
    assert response.status_code == 201
    return response.json()["session_id"]


def prepared(http):
    sid = session(http)
    response = http.post(f"{ROOT}/{sid}/orders", json=ORDER)
    assert response.status_code == 201
    return sid


def state(http, sid):
    response = http.get(f"{ROOT}/{sid}/state")
    assert response.status_code == 200
    return response.json()["state"]


def command(http, sid, operation):
    response = http.post(f"{ROOT}/{sid}/{operation}")
    assert response.status_code == 200, response.text
    CommandResponse.model_validate(response.json())
    return response.json()


def test_create_and_get_valid_initial_state(http):
    created = http.post(ROOT)
    parsed = SessionResponse.model_validate_json(created.text)
    assert created.status_code == 201
    assert parsed.state == WarehouseGraphState(warehouse=WarehouseSimulation().state, command="plan")
    assert http.get(f"{ROOT}/{parsed.session_id}/state").json() == created.json()


@pytest.mark.parametrize("method,path,body", [
    ("get", "state", None), ("post", "plan", None), ("post", "execute", None),
    ("post", "orders", ORDER), ("post", "blocked-cells", {"x": 5, "y": 0}),
    ("delete", "blocked-cells/5/0", None), ("post", "reset", None),
])
def test_unknown_session(http, method, path, body):
    response = http.request(method, f"{ROOT}/unknown/{path}", **({"json": body} if body else {}))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_session"


def test_order_creation_does_not_plan_or_execute(http):
    sid = prepared(http)
    current = state(http, sid)
    assert current["warehouse"]["revision"] == 1
    assert current["warehouse"]["orders"][0]["status"] == "pending"
    assert current["node_activity"] == []
    assert current["delivery_plan"] is current["safety"] is current["order_selection"] is None
    assert current["warehouse"]["robots"] == WarehouseSimulation().state.model_dump(mode="json")["robots"]


@pytest.mark.parametrize("body", [{}, {**ORDER, "unknown": "secret-value"},
                                   {**ORDER, "order_id": " "},
                                   {**ORDER, "pickup": {"x": 2}},
                                   {**ORDER, "pickup": {"x": 2, "y": 0, "z": 0}}])
def test_invalid_order_shapes_are_sanitized(http, body):
    sid = session(http)
    response = http.post(f"{ROOT}/{sid}/orders", json=body)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request",
                                        "message": "Invalid request body or path parameters"}}
    assert not state(http, sid)["warehouse"]["orders"]


@pytest.mark.parametrize("value", ["2", 2.0, True, None])
@pytest.mark.parametrize("endpoint", ["orders", "blocked-cells"])
def test_strict_coordinate_types(http, value, endpoint):
    sid = session(http)
    body = ({**ORDER, "pickup": {"x": value, "y": 0}} if endpoint == "orders"
            else {"x": value, "y": 0})
    assert http.post(f"{ROOT}/{sid}/{endpoint}", json=body).status_code == 422


def test_malformed_json_and_path(http):
    sid = session(http)
    response = http.post(f"{ROOT}/{sid}/orders", content='{"secret":"hidden",',
                         headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and "hidden" not in response.text
    assert http.delete(f"{ROOT}/{sid}/blocked-cells/not-an-int/0").status_code == 422


@pytest.mark.parametrize("body", [ORDER, {**ORDER, "order_id": "other"},
                                   {**ORDER, "order_id": "other", "package_id": "other-p",
                                    "dropoff": {"x": 8, "y": 0}}])
def test_invalid_domain_mutation_keeps_state(http, body):
    sid = prepared(http)
    before = state(http, sid)
    response = http.post(f"{ROOT}/{sid}/orders", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_mutation"
    assert state(http, sid) == before


def test_plan_ready_then_execute_delivered(http):
    sid = prepared(http)
    before = state(http, sid)
    ready = command(http, sid, "plan")
    assert ready["outcome"] == "ready" and ready["error"] is None
    assert ready["state"]["warehouse"] == before["warehouse"]
    assert [item["node"] for item in ready["state"]["node_activity"]] == ["order", "fleet", "route", "safety"]
    assert state(http, sid) == ready["state"]
    delivered = command(http, sid, "execute")
    assert delivered["outcome"] == "delivered"
    assert delivered["state"]["warehouse"]["orders"][0]["status"] == "delivered"
    assert state(http, sid) == delivered["state"]


def test_plan_no_work(http):
    result = command(http, session(http), "plan")
    assert result["outcome"] == "no_work" and result["error"] is None


def test_plan_naturally_unreachable_preserves_fleet_no_robot(http):
    sid = prepared(http)
    assert http.post(f"{ROOT}/{sid}/blocked-cells", json=ORDER["pickup"]).status_code == 200
    result = command(http, sid, "plan")
    assert result["outcome"] == "no_robot"
    assert [item["node"] for item in result["state"]["node_activity"]] == ["order", "fleet"]


def test_route_unreachable_during_stale_execute(http):
    sid = prepared(http)
    command(http, sid, "plan")
    changed = http.post(f"{ROOT}/{sid}/blocked-cells", json=ORDER["pickup"]).json()["state"]
    result = command(http, sid, "execute")
    assert result["outcome"] == "unreachable"
    assert result["state"]["warehouse"] == changed["warehouse"]
    assert result["state"]["node_activity"][-1]["node"] == "route"


def test_stale_execute_returns_review_only_then_explicit_delivery(http):
    sid = prepared(http)
    original = command(http, sid, "plan")
    changed = http.post(f"{ROOT}/{sid}/blocked-cells", json={"x": 5, "y": 0}).json()["state"]
    assert changed["safety"] is None and changed["planning_outcome"] == "stale"
    replacement = command(http, sid, "execute")
    assert replacement["outcome"] == "ready"
    assert replacement["state"]["warehouse"] == changed["warehouse"]
    assert replacement["state"]["delivery_plan"] != original["state"]["delivery_plan"]
    assert not replacement["state"]["execution_requested"]
    assert command(http, sid, "execute")["outcome"] == "delivered"


def test_consumed_proposal_failed_attempt_keeps_delivery_and_unrelated_order(http):
    sid = prepared(http)
    other = {**ORDER, "order_id": "other", "package_id": "other-p"}
    pending = http.post(f"{ROOT}/{sid}/orders", json=other).json()["state"]["warehouse"]["orders"][1]
    command(http, sid, "plan")
    delivered = command(http, sid, "execute")["state"]
    for _ in range(2):
        rejected = command(http, sid, "execute")
        assert rejected["outcome"] == "failed"
        assert rejected["error"]["code"] == "consumed_proposal"
        assert rejected["state"] == delivered == state(http, sid)
    assert delivered["warehouse"]["orders"][1] == pending


def test_missing_proposal_returns_previous_commit(http):
    sid = session(http)
    before = state(http, sid)
    result = command(http, sid, "execute")
    assert result["outcome"] == "failed"
    assert result["error"]["code"] == "missing_proposal"
    assert result["state"] == before == state(http, sid)
    assert result["state"]["run_outcome"] == "idle"


def test_block_unblock_and_invalid_block(http):
    sid = prepared(http)
    before = state(http, sid)
    for cell in ({"x": 7, "y": 0}, {"x": 5, "y": 0}):
        assert http.post(f"{ROOT}/{sid}/blocked-cells", json=cell).status_code == 200
    blocked = state(http, sid)
    assert blocked["warehouse"]["blocked_cells"] == [{"x": 5, "y": 0}, {"x": 7, "y": 0}]
    assert blocked["warehouse"]["revision"] == before["warehouse"]["revision"] + 2
    removed = http.delete(f"{ROOT}/{sid}/blocked-cells/5/0")
    assert removed.status_code == 200
    assert removed.json()["state"]["warehouse"]["blocked_cells"] == [{"x": 7, "y": 0}]
    assert http.delete(f"{ROOT}/{sid}/blocked-cells/100/0").status_code == 422
    assert http.post(f"{ROOT}/{sid}/blocked-cells", json={"x": 0, "y": 0}).status_code == 422


def test_reset_retires_id_and_clears_proposals_blocks_diagnostics(http):
    sid = prepared(http)
    command(http, sid, "plan")
    http.post(f"{ROOT}/{sid}/blocked-cells", json={"x": 5, "y": 0})
    result = http.post(f"{ROOT}/{sid}/reset")
    assert result.status_code == 201
    fresh = result.json()
    assert fresh["session_id"] != sid
    assert SessionResponse.model_validate(fresh).state == WarehouseGraphState(
        warehouse=WarehouseSimulation().state, command="plan")
    assert http.get(f"{ROOT}/{sid}/state").status_code == 404
    assert http.post(f"{ROOT}/{sid}/execute").status_code == 404
    assert command(http, fresh["session_id"], "execute")["outcome"] == "failed"


def test_two_api_sessions_isolate_orders_blocks_robots_battery(http):
    a, b = prepared(http), session(http)
    untouched = state(http, b)
    http.post(f"{ROOT}/{a}/blocked-cells", json={"x": 5, "y": 5})
    command(http, a, "plan")
    delivered = command(http, a, "execute")["state"]
    assert delivered["warehouse"]["robots"] != untouched["warehouse"]["robots"]
    assert state(http, b) == untouched


def test_busy_maps_409_while_other_session_progresses(http, monkeypatch):
    sid, other = prepared(http), session(http)
    entered, release = Event(), Event()
    from test_graph_workflow import Fake
    original = Fake.with_structured_output

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(Fake, "with_structured_output", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(http.post, f"{ROOT}/{sid}/plan")
        try:
            assert entered.wait(10)
            conflict = http.get(f"{ROOT}/{sid}/state")
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "session_busy"
            assert state(http, other)
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 200
    assert state(http, sid)["run_outcome"] == "ready"


@pytest.mark.parametrize("exception", [SessionExecutionError("secret-token"), RuntimeError("secret-token")])
def test_unexpected_failures_sanitized(http, coordinator, monkeypatch, exception):
    sid = session(http)

    def fail(*args):
        raise exception

    monkeypatch.setattr(coordinator, "plan", fail)
    response = http.post(f"{ROOT}/{sid}/plan")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error",
                                        "message": "Session operation could not be completed"}}
    assert "secret-token" not in response.text


def test_real_model_failure_is_internal_not_expected_rejection(http, monkeypatch):
    sid = prepared(http)
    before = state(http, sid)

    def fail(*args, **kwargs):
        raise RuntimeError("provider-secret")

    monkeypatch.setattr("test_graph_workflow.Fake.with_structured_output", fail)
    response = http.post(f"{ROOT}/{sid}/plan")
    assert response.status_code == 500 and "provider-secret" not in response.text
    assert state(http, sid) == before


def test_real_checkpoint_failure_maps_500_and_preserves_commit(http, coordinator, monkeypatch):
    sid = prepared(http)
    before = state(http, sid)
    # Fault injection into the real coordinator's checkpoint operation, never
    # into the API. Runtime handlers have no checkpoint access.
    def fail(*args, **kwargs):
        raise RuntimeError("checkpoint-secret")

    monkeypatch.setattr(coordinator._graph, "update_state", fail)
    response = http.post(f"{ROOT}/{sid}/blocked-cells", json={"x": 5, "y": 0})
    assert response.status_code == 500 and "checkpoint-secret" not in response.text
    assert state(http, sid) == before


@pytest.mark.parametrize("field", ["state", "delivery_plan", "safety"])
@pytest.mark.parametrize("endpoint", ["orders", "blocked-cells"])
def test_mutation_endpoints_reject_state_injection(http, field, endpoint):
    sid = session(http)
    body = dict(ORDER) if endpoint == "orders" else {"x": 5, "y": 0}
    body[field] = {}
    assert http.post(f"{ROOT}/{sid}/{endpoint}", json=body).status_code == 422


def test_expected_rejection_handler_more_specific_than_internal(http, coordinator):
    sid = session(http)
    committed = coordinator.get_state(sid)
    with pytest.raises(SessionCommandRejected) as caught:
        coordinator.execute(sid)
    assert isinstance(caught.value, SessionExecutionError)
    assert caught.value.state == committed
    assert command(http, sid, "execute")["state"] == committed.model_dump(mode="json")


def test_completed_failed_outcome_mapping(http, coordinator, monkeypatch):
    # HTTP-only fixture: deterministic terminal rejection is already tested at
    # graph level; no arbitrary state input is exposed to callers.
    sid = session(http)
    failed = WarehouseGraphState.model_validate({**coordinator.get_state(sid).model_dump(),
                                                "run_outcome": "failed"})
    monkeypatch.setattr(coordinator, "plan", lambda _: failed)
    result = command(http, sid, "plan")
    assert result["outcome"] == "failed" and result["error"]["code"] == "workflow_rejected"


@pytest.mark.parametrize("endpoint", ["", "/plan", "/execute", "/reset"])
@pytest.mark.parametrize("body", [{"state": {}}, {"delivery_plan": {}}, {"safety": {"route_valid": True}}, {}])
def test_bodyless_endpoints_reject_injected_state(http, endpoint, body):
    sid = session(http)
    path = ROOT if not endpoint else f"{ROOT}/{sid}{endpoint}"
    assert http.post(path, json=body).status_code == 422


def test_nested_state_serialization_and_no_private_objects(http):
    sid = prepared(http)
    initial = state(http, sid)
    assert initial["safety"] is None
    result = command(http, sid, "plan")
    restored = CommandResponse.model_validate(result)
    assert restored.state.model_dump(mode="json") == result["state"]
    assert isinstance(result["state"]["delivery_plan"]["pickup_route"], list)
    assert result["state"]["warehouse"]["robots"][0]["status"] == "idle"
    assert "revision" in result["state"]["warehouse"]
    assert "warehouse_revision" not in result["state"]
    for forbidden in ("checkpoint_id", "checkpoint_ns", "configurable", "thread_id", "api_key",
                      "coordinator", "_registry", "_saver", "guard"):
        assert forbidden not in json.dumps(result)


@pytest.mark.parametrize("origins,environment,origin,allowed", [
    (None, None, "http://localhost:5173", True),
    (None, "http://localhost:3000, http://127.0.0.1:3000", "http://localhost:3000", True),
    (["http://localhost:4000"], "http://localhost:3000", "http://localhost:4000", True),
    ([], None, "http://localhost:5173", False),
    (None, None, "http://unapproved.example", False),
])
def test_cors(coordinator, monkeypatch, origins, environment, origin, allowed):
    if environment is None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", environment)
    with TestClient(create_app(coordinator=coordinator, allowed_origins=origins)) as http:
        response = http.options(ROOT, headers={"Origin": origin,
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "Content-Type"})
        assert response.status_code == (200 if allowed else 400)
        assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
        assert "access-control-allow-credentials" not in response.headers
        assert set(response.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST", "DELETE"}


def test_injected_coordinator_skips_model_factory_without_credentials(coordinator, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    def forbidden():
        pytest.fail("Live model factory must not run")

    monkeypatch.setattr("app.api.main.create_model_client", forbidden)
    with TestClient(create_app(coordinator=coordinator)) as http:
        assert http.post(ROOT).status_code == 201


def test_lifespan_constructs_one_shared_client_and_coordinator(monkeypatch):
    calls = []

    def factory():
        calls.append("client")
        return fake_client()

    monkeypatch.setattr("app.api.main.create_model_client", factory)
    app = create_app()
    assert calls == []
    with TestClient(app) as http:
        a, b = session(http), session(http)
        assert state(http, a) == state(http, b)
        assert calls == ["client"]
    assert not hasattr(app.state, "coordinator")


def test_import_does_not_construct_live_model():
    script = """
import app.config
def forbidden(*args, **kwargs):
    raise AssertionError('Live client factory called during import')
app.config.create_model_client = forbidden
import app.api.main
assert not hasattr(app.api.main.app.state, 'coordinator')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_docs_and_openapi_contract(http):
    assert http.get("/docs").status_code == 200
    response = http.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    expected = {ROOT: "post", ROOT + "/{session_id}/state": "get",
                ROOT + "/{session_id}/orders": "post", ROOT + "/{session_id}/plan": "post",
                ROOT + "/{session_id}/execute": "post", ROOT + "/{session_id}/blocked-cells": "post",
                ROOT + "/{session_id}/blocked-cells/{x}/{y}": "delete",
                ROOT + "/{session_id}/reset": "post"}
    assert set(schema["paths"]) == set(expected)
    for path, method in expected.items():
        assert method in schema["paths"][path]
    for suffix in ("", "/{session_id}/plan", "/{session_id}/execute", "/{session_id}/reset"):
        assert "requestBody" not in schema["paths"][ROOT + suffix]["post"]
    assert schema["components"]["schemas"]["CreateOrderRequest"]["additionalProperties"] is False
    assert "RunnableConfig" not in response.text and "SessionCoordinator" not in response.text


def test_api_layer_imports_no_simulation_graph_runtime_or_saver():
    api = Path(__file__).resolve().parents[1] / "app" / "api"
    for path in api.glob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module and (module.startswith("langgraph") or module in
                       {"app.graph.graph", "app.graph.agents", "app.warehouse.simulation"}) for module in imports)
        assert all(name not in source for name in ("WarehouseSimulation", "_registry", "_saver", "_graph"))
