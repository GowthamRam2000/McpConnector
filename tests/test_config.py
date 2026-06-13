import pytest
from pydantic import ValidationError

from swiggy_deal_finder.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 32)

    settings = Settings(_env_file=None)

    assert settings.google_cloud_project == "demo-project"
    assert settings.google_cloud_location == "asia-south1"
    assert settings.gemini_model == "gemini-3.5-flash"
    assert settings.environment == "dev"  # default


def test_missing_required_raises(monkeypatch):
    # app_secret_key is the only field without a safe default.
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
