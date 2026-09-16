"""Offline configuration tests; neither credentials nor provider package required."""

import sys
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import ValidationError

from app.config import ConfigurationError, LLMSettings, create_model_client, load_settings


def test_valid_environment_and_secret_exclusion():
    settings = load_settings(environ={"LLM_PROVIDER": "google_genai", "LLM_MODEL": " test-model ",
                                      "GOOGLE_API_KEY": "test-placeholder", "LLM_TIMEOUT_SECONDS": "12.5",
                                      "LLM_MAX_RETRIES": "1", "UNRELATED": "ignored"})
    assert settings.model == "test-model" and settings.provider == "google_genai"
    assert settings.timeout_seconds == 12.5 and settings.max_retries == 1
    assert "test-placeholder" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    assert "test-placeholder" not in settings.model_dump_json()


@pytest.mark.parametrize("env, missing", [({}, "LLM_MODEL"),
    ({"LLM_MODEL": "test-model"}, "GOOGLE_API_KEY"),
    ({"GOOGLE_API_KEY": "test-placeholder"}, "LLM_MODEL")])
def test_missing_live_configuration(env, missing):
    with pytest.raises(ConfigurationError, match=missing):
        create_model_client(load_settings(environ=env))


@pytest.mark.parametrize("env", [
    {"LLM_PROVIDER": "unknown"}, {"LLM_MODEL": " "}, {"GOOGLE_API_KEY": " "},
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
    monkeypatch.setitem(sys.modules, "langchain_google_genai", None)
    fake = FakeListChatModel(responses=["offline"])
    assert create_model_client(client=fake) is fake
    assert create_model_client(LLMSettings(), client=fake) is fake


def test_provider_construction_is_once_and_does_not_invoke(monkeypatch):
    calls = []
    fake = FakeListChatModel(responses=["unused"])
    def constructor(**kwargs):
        calls.append(kwargs)
        return fake
    monkeypatch.setitem(sys.modules, "langchain_google_genai", SimpleNamespace(ChatGoogleGenerativeAI=constructor))
    settings = LLMSettings(model="test-model", api_key="test-placeholder")
    shared = create_model_client(settings)
    assert shared is fake and len(calls) == 1
    assert calls[0] == dict(model="test-model", api_key=settings.api_key, vertexai=False,
                            timeout=30, max_retries=2)
    assert create_model_client(client=shared) is shared and len(calls) == 1


def test_missing_optional_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_google_genai", None)
    with pytest.raises(ConfigurationError, match="langchain-google-genai"):
        create_model_client(LLMSettings(model="test-model", api_key="test-placeholder"))


def test_dotenv_precedence_and_no_global_mutation(tmp_path, monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "GOOGLE_API_KEY", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("LLM_MODEL=file-model\nGOOGLE_API_KEY=test-placeholder\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "process-model")
    assert load_settings(env_file=path).model == "process-model"
    assert load_settings(env_file=path, environ={}).model == "file-model"
    assert load_settings(environ={}).model is None
    assert load_settings().model == "process-model"


def test_missing_explicit_dotenv(tmp_path):
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_settings(env_file=tmp_path / "missing", environ={})


def test_openai_configuration_is_not_accepted():
    with pytest.raises(ValidationError):
        load_settings(environ={"LLM_PROVIDER": "openai"})
    settings = load_settings(environ={"OPENAI_API_KEY": "unused-placeholder"})
    assert settings.provider == "google_genai" and settings.api_key is None
