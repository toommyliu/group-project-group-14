import re
from typing import Annotated, Iterable, Literal, Self

from pydantic import SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


LogLevel = Literal["debug", "info", "warning", "error"]


def on_domain(email: str, domain: str) -> bool:
    return re.fullmatch(rf"[a-z0-9._'-]{{1,64}}@{re.escape(domain)}", email) is not None


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENGRADER_",
        env_file=".env",
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    log_level: LogLevel = "info"
    auth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    google_domain: Annotated[str, StringConstraints(to_lower=True)] = "sjsu.edu"
    instructors: Annotated[frozenset[str], NoDecode] = frozenset()
    secure_cookie: bool = False

    @field_validator("log_level", mode="before")
    @classmethod
    def parse_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.lower()
            return "warning" if value == "warn" else value
        return value

    @field_validator("instructors", mode="before")
    @classmethod
    def parse_instructors(cls, value: str | Iterable[str]) -> frozenset[str]:
        emails = value.split(",") if isinstance(value, str) else value
        return frozenset(email.strip().lower() for email in emails if email.strip())

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
        if outside := sorted(
            email
            for email in self.instructors
            if not on_domain(email, self.google_domain)
        ):
            raise ValueError(
                f"OPENGRADER_INSTRUCTORS must use @{self.google_domain} addresses: "
                f"{', '.join(outside)}"
            )
        return self
