from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    phoenix_api_base_url: str = ""
    phoenix_api_token: str = ""
    ssh_private_key_path: str = ""
    ssh_username: str = "azureuser"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_api_base: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
