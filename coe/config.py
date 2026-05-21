from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://coe:coe@localhost:5432/coe"

    # Jira ingest settings
    jira_base_url: str = "https://capsule.atlassian.net"
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_projects: list[str] = []  # COE allowlist, e.g. ["SEC", "OPS"]

    # Wiz ingest settings
    wiz_client_id: str = ""
    wiz_client_secret: str = ""
    wiz_api_url: str = "https://api.wiz.io/graphql"
    wiz_auth_url: str = "https://auth.wiz.io/oauth/token"

    # CrowdStrike ingest settings
    crowdstrike_client_id: str = ""
    crowdstrike_client_secret: str = ""
    crowdstrike_base_url: str = "https://api.crowdstrike.com"

    # Vibranium ingest settings
    vibranium_base_url: str = ""
    vibranium_api_token: str = ""

    @field_validator("jira_projects", mode="before")
    @classmethod
    def _split_csv_jira_projects(cls, v: object) -> object:
        """Accept comma-separated env strings (`JIRA_PROJECTS=SEC,OPS`) as well as
        JSON (`JIRA_PROJECTS=["SEC","OPS"]`). pydantic-settings v2 expects JSON
        for complex env types by default; this validator widens that to also
        accept the comma-separated form K8s ConfigMaps typically use."""
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
