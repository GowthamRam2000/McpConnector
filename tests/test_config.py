import pytest
from pydantic import ValidationError

from swiggy_deal_finder.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("SWIGGY_MCP_URL", "https://staging.example/mcp")
    monkeypatch.setenv("GCP_PROJECT_ID", "demo-project")
    monkeypatch.setenv("GCP_LOCATION", "asia-south1")
    monkeypatch.setenv("VERTEX_MODEL_ID", "gemini-flash-lite")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 32)

    settings = Settings(_env_file=None)

    assert settings.swiggy_mcp_url == "https://staging.example/mcp"
    assert settings.gcp_project_id == "demo-project"
    assert settings.environment == "dev"  # default


def test_missing_required_raises(monkeypatch):
    for var in (
        "SWIGGY_MCP_URL",
        "GCP_PROJECT_ID",
        "GCP_LOCATION",
        "VERTEX_MODEL_ID",
        "APP_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
