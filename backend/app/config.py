"""Application-owned LLM settings and one client construction/injection boundary.

Call create_model_client once at application setup and share its return value.
Nothing is loaded or constructed at import time; clients never belong in graph state.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator


class ConfigurationError(ValueError):
    """Missing live-model configuration or provider integration."""


def structured_output(client: BaseChatModel, schema: type[BaseModel]) -> Runnable:
    """Configure the shared Groq client for Pydantic output via tool calling.

    This wraps an injected client; it does not create another provider client.
    """
    return client.with_structured_output(schema, method="function_calling")


class LLMSettings(BaseModel):
    """Incomplete settings are valid for offline injection; live creation checks them."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    max_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("api_key")
    @classmethod
    def nonblank_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("API key must not be blank")
        return value


def load_settings(*, environ: Mapping[str, str] | None = None,
                  env_file: str | Path | None = None) -> LLMSettings:
    """Read selected variables; explicit environment overrides an optional dotenv.

    No implicit file discovery, global environment mutation, or interpolation.
    Passing environ={} isolates tests from the developer's environment.
    """
    values = {}
    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise ConfigurationError("Requested environment file does not exist")
        values.update(dotenv_values(path, interpolate=False))
    values.update(os.environ if environ is None else environ)
    fields = {"GROQ_MODEL": "model",
              "GROQ_API_KEY": "api_key", "LLM_TIMEOUT_SECONDS": "timeout_seconds",
              "LLM_MAX_RETRIES": "max_retries"}
    return LLMSettings.model_validate({field: values[key] for key, field in fields.items()
                                      if key in values})


def create_model_client(settings: LLMSettings | None = None, *,
                        client: BaseChatModel | None = None) -> BaseChatModel:
    """Return an injected client unchanged, or construct one live-provider client.

    Construction performs no invocation. Ownership/lifetime belongs to the caller;
    do not call this factory separately inside future nodes. No global cache.
    """
    if client is not None:
        return client
    settings = settings if settings is not None else load_settings()
    missing = []
    if settings.model is None:
        missing.append("GROQ_MODEL")
    if settings.api_key is None:
        missing.append("GROQ_API_KEY")
    if missing:
        raise ConfigurationError("Live model requires: " + ", ".join(missing))
    try:
        from langchain_groq import ChatGroq
    except ImportError:
        raise ConfigurationError(
            "Groq live client requires the langchain-groq package; "
            "install it in the project virtual environment before live setup"
        ) from None
    return ChatGroq(model=settings.model, api_key=settings.api_key,
                    timeout=settings.timeout_seconds,
                    max_retries=settings.max_retries)
