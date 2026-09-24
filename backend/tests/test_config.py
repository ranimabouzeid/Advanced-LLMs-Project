"""Offline configuration tests; no credentials or network calls required."""

import sys
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import ValidationError

from app.config import ConfigurationError, LLMSettings, create_model_client, load_settings


def test_valid_environment_and_secret_exclusion():
    settings = load_settings(environ={"GROQ_MODEL": " test-model ",
                                      "GROQ_API_KEY": "test-placeholder", "LLM_TIMEOUT_SECONDS": "12.5",
                                      "LLM_MAX_RETRIES": "1", "UNRELATED": "ignored"})
    assert settings.model == "test-model"
    assert settings.timeout_seconds == 12.5 and settings.max_retries == 1
    assert "test-placeholder" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    assert "test-placeholder" not in settings.model_dump_json()


@pytest.mark.parametrize("env, missing", [({}, "GROQ_MODEL"),
    ({"GROQ_MODEL": "test-model"}, "GROQ_API_KEY"),
    ({"GROQ_API_KEY": "test-placeholder"}, "GROQ_MODEL")])
def test_missing_live_configuration(env, missing):
    with pytest.raises(ConfigurationError, match=missing):
        create_model_client(load_settings(environ=env))


@pytest.mark.parametrize("env", [
    {"GROQ_MODEL": " "}, {"GROQ_API_KEY": " "},
    {"LLM_TIMEOUT_SECONDS": "0"}, {"LLM_TIMEOUT_SECONDS": "nan"},
    {"LLM_MAX_RETRIES": "-1"}, {"LLM_MAX_RETRIES": "6"}, {"LLM_MAX_RETRIES": "1.5"},
])
def test_invalid_settings(env):
    with pytest.raises(ValidationError):
        load_settings(environ=env)


def test_injection_skips_environment_and_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Injection must not load settings")
    monkeypatch.setattr("app.config.load_settings", forbidden)
    monkeypatch.setitem(sys.modules, "langchain_groq", None)
    fake = FakeListChatModel(responses=["offline"])
    assert create_model_client(client=fake) is fake
    assert create_model_client(LLMSettings(), client=fake) is fake


def test_provider_construction_is_once_and_does_not_invoke(monkeypatch):
    calls = []
    fake = FakeListChatModel(responses=["unused"])
    def constructor(**kwargs):
        calls.append(kwargs)
        return fake
    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=constructor))
    settings = LLMSettings(model="test-model", api_key="test-placeholder")
    shared = create_model_client(settings)
    assert shared is fake and len(calls) == 1
    assert calls[0] == dict(model="test-model", api_key=settings.api_key,
                            timeout=30, max_retries=2, max_tokens=512)
    assert create_model_client(client=shared) is shared and len(calls) == 1


def test_missing_provider_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_groq", None)
    with pytest.raises(ConfigurationError, match="langchain-groq"):
        create_model_client(LLMSettings(model="test-model", api_key="test-placeholder"))


def test_dotenv_precedence_and_no_global_mutation(tmp_path, monkeypatch):
    for name in ("GROQ_MODEL", "GROQ_API_KEY", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("GROQ_MODEL=file-model\nGROQ_API_KEY=test-placeholder\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_MODEL", "process-model")
    assert load_settings(env_file=path).model == "process-model"
    assert load_settings(env_file=path, environ={}).model == "file-model"
    assert load_settings(environ={}).model is None
    assert load_settings().model == "process-model"


def test_missing_explicit_dotenv(tmp_path):
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_settings(env_file=tmp_path / "missing", environ={})


def test_unrelated_provider_configuration_is_ignored():
    settings = load_settings(environ={"OPENAI_API_KEY": "unused-placeholder",
                                      "LLM_PROVIDER": "openai"})
    assert settings.api_key is None and settings.model is None


@pytest.mark.parametrize("arguments, valid", [
    ('{"order_id":"o1","explanation":"Oldest order"}', True),
    ('{"order_id":"o1"}', False),
])
def test_real_groq_structured_order_parsing_offline(monkeypatch, arguments, valid):
    from langchain_groq import ChatGroq
    from app.config import structured_output
    from app.graph.state import OrderSelection

    client = create_model_client(LLMSettings(model="test-model", api_key="test-placeholder",
                                           timeout_seconds=7, max_retries=0))
    assert isinstance(client, ChatGroq)
    assert client.model_name == "test-model"
    assert client.request_timeout == 7 and client.max_retries == 0
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "offline-call", "type": "function", "function": {
                    "name": "OrderSelection", "arguments": arguments}}]}}],
            "model": "test-model", "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                "total_tokens": 2}}

    monkeypatch.setattr(client.client, "create", completion)
    runnable = structured_output(client, OrderSelection)
    if valid:
        result = runnable.invoke("Select o1")
        assert isinstance(result, OrderSelection) and result.order_id == "o1"
    else:
        with pytest.raises(ValidationError):
            runnable.invoke("Select o1")
    assert len(calls) == 1
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "OrderSelection"}}
    schema = calls[0]["tools"][0]["function"]["parameters"]
    assert set(schema["required"]) == {"order_id", "explanation"}


@pytest.mark.parametrize("schema_name, output", [
    ("FleetExplanation", {"explanation": "The optimizer selected r1 by complete projected A* cost"}),
    ("SafetyDecision", {"approved": True, "conflicts": [], "explanation": "Approved"}),
])
def test_real_groq_other_role_schemas_offline(monkeypatch, schema_name, output):
    import json
    from app.config import structured_output
    from app.graph import state
    schema = getattr(state, schema_name)
    model = create_model_client(LLMSettings(model="test-model", api_key="test-placeholder"))
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "offline-call", "type": "function", "function": {
                    "name": schema_name, "arguments": json.dumps(output)}}]}}], "model": "test-model"}
    monkeypatch.setattr(model.client, "create", completion)
    assert structured_output(model, schema).invoke("Decide") == schema.model_validate(output)
    assert len(calls) == 1
    assert calls[0]["tool_choice"]["function"]["name"] == schema_name
