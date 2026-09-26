from typing import Annotated, Self

from pydantic import SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENGRADER_",
        env_file=".env",
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    auth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    google_domain: Annotated[str, StringConstraints(to_lower=True)] = "sjsu.edu"
    secure_cookie: bool = False

    @model_validator(mode="after")
    def validate_auth(self) -> Self:
        if not self.auth_enabled:
            return self
        if not all(
            (
                self.google_client_id,
                self.google_client_secret.get_secret_value(),
                self.session_secret.get_secret_value(),
            )
        ):
            raise ValueError(
                "Google sign-in requires OPENGRADER_GOOGLE_CLIENT_ID, "
                "OPENGRADER_GOOGLE_CLIENT_SECRET, and OPENGRADER_SESSION_SECRET."
            )
        if not self.google_domain:
            raise ValueError("OPENGRADER_GOOGLE_DOMAIN cannot be empty.")
        return self
