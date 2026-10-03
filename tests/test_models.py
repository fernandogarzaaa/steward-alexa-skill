"""Model layer tests: provider selection without network calls."""
import os

import pytest

from steward.models import (ConfigurationError, build_bedrock_model,
                            build_model, build_openai_compatible_model,
                            describe_active_provider, has_aws_credentials)
from strands.models.bedrock import BedrockModel
from strands.models.openai import OpenAIModel

KEYS = ["STEWARD_MODEL_PROVIDER", "STEWARD_MODEL_BASE_URL",
        "STEWARD_MODEL_API_KEY", "STEWARD_MODEL_ID",
        "STEWARD_BEDROCK_MODEL_ID", "STEWARD_AWS_REGION",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION", "AWS_REGION"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_openai_compatible_selected_by_env(monkeypatch):
    monkeypatch.setenv("STEWARD_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("STEWARD_MODEL_ID", "test-model")
    model = build_model()
    assert isinstance(model, OpenAIModel)
    assert "openai-compatible" in describe_active_provider()


def test_explicit_openai_compatible_builder():
    model = build_openai_compatible_model("http://x/v1", "k", "m")
    assert isinstance(model, OpenAIModel)


def test_bedrock_selected_with_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    assert has_aws_credentials() is True
    model = build_model()
    assert isinstance(model, BedrockModel)
    assert describe_active_provider().startswith("bedrock:")


def test_bedrock_builder_uses_env_model_id(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("STEWARD_BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
    model = build_bedrock_model()
    assert isinstance(model, BedrockModel)
    assert "nova-lite" in describe_active_provider()


def test_unconfigured_raises_actionable_error():
    assert has_aws_credentials() is False
    with pytest.raises(ConfigurationError) as e:
        build_model()
    msg = str(e.value)
    assert "STEWARD_MODEL_BASE_URL" in msg
    assert "Bedrock" in msg
    assert describe_active_provider() == "unconfigured"
