from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    phoenix_api_base_url: str | None = None
    phoenix_api_token: SecretStr | None = None
    phoenix_timeout_s: float = Field(default=8.0, ge=0.5, le=60)

    database_url: str | None = None

    ssh_private_key_path: str | None = None
    ssh_private_key_dir: str | None = None
    ssh_username: str | None = None
    ssh_known_hosts_path: str | None = None
    ssh_host_key_policy: str = Field(default="accept-new", pattern="^(accept-new|strict|insecure-ignore)$")

    command_timeout_s: int = Field(default=30, ge=1, le=600)
    command_output_limit_bytes: int = Field(default=200_000, ge=1_024, le=5_000_000)

    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str | None = None

    @property
    def phoenix_configured(self) -> bool:
        if not self.phoenix_api_base_url or not self.phoenix_api_token:
            return False
        token = self.phoenix_api_token.get_secret_value()
        return not (
            "REPLACE" in self.phoenix_api_base_url
            or self.phoenix_api_base_url.startswith("https://REPLACE")
            or token.startswith("replace-")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
