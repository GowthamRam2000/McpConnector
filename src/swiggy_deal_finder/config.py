from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env.

    Vertex/Gemini fields use the google-genai SDK's standard env-var names
    (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GEMINI_MODEL, ...) so the SDK
    and this app read one source of truth.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    swiggy_mcp_url: str = ""
    swiggy_oauth_client_id: str = ""
    swiggy_oauth_client_secret: str = ""
    swiggy_oauth_redirect_uri: str = "http://localhost:8000/auth/callback"

    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    google_genai_use_vertexai: bool = True
    gemini_model: str = "gemini-3.5-flash"
    google_application_credentials: str = ""

    app_secret_key: str
    database_url: str = "sqlite:///./dev.db"
    environment: str = "dev"
