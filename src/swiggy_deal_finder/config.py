from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Swiggy MCP
    swiggy_mcp_url: str
    swiggy_oauth_client_id: str = ""
    swiggy_oauth_client_secret: str = ""
    swiggy_oauth_redirect_uri: str = ""

    # Vertex AI / Gemini
    gcp_project_id: str
    gcp_location: str
    vertex_model_id: str
    google_application_credentials: str = ""

    # App
    app_secret_key: str
    database_url: str = "sqlite:///./dev.db"
    environment: str = "dev"
