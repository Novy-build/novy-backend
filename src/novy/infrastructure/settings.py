from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = "production"
    mongodb_connection_string: SecretStr | None = Field(
        default=None, validation_alias="MONGODB_CONNECTION_STRING", repr=False, exclude=True
    )
    # These shared dotenv entries are accepted but not used until auth is implemented.
    supabase_url: SecretStr | None = Field(
        default=None, validation_alias="NEXT_PUBLIC_SUPABASE_URL", repr=False, exclude=True
    )
    supabase_publishable_key: SecretStr | None = Field(
        default=None,
        validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        repr=False,
        exclude=True,
    )

    @model_validator(mode="before")
    @classmethod
    def mask_secret_inputs(cls, data: Any) -> Any:
        # Mask raw values before Pydantic can attach them to structured errors.
        if isinstance(data, dict):
            data = data.copy()
            for name in (
                "MONGODB_CONNECTION_STRING",
                "mongodb_connection_string",
                "NEXT_PUBLIC_SUPABASE_URL",
                "supabase_url",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
                "supabase_publishable_key",
            ):
                value = data.get(name)
                if isinstance(value, str):
                    data[name] = SecretStr(value)
                elif isinstance(value, (bytes, bytearray)):
                    data[name] = SecretStr(value.decode("utf-8", errors="replace"))
                elif value is not None and not isinstance(value, SecretStr):
                    data[name] = object()
        return data

    @field_validator("mongodb_connection_string")
    @classmethod
    def validate_mongodb_connection_string(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None

        uri = value.get_secret_value()
        try:
            if "#" in uri or "\ufffd" in uri:
                raise ValueError("Invalid MongoDB connection string")
            parts = urlsplit(uri)
            if ";" in parts.query:
                raise ValueError("Invalid MongoDB connection string")
            options = parse_qsl(parts.query, keep_blank_values=True)
            hostname = parts.hostname
        except ValueError:
            raise ValueError("Invalid MongoDB connection string") from None

        if uri != uri.strip() or parts.scheme not in {"mongodb", "mongodb+srv"} or not hostname:
            raise ValueError("Invalid MongoDB connection string")

        for name, option in options:
            name, option = name.lower(), option.lower()
            if (
                (name in {"tls", "ssl"} and option != "true")
                or (
                    name
                    in {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
                    and option != "false"
                )
                or name == "srvallowedhostssuffix"
            ):
                raise ValueError("MongoDB connection must use secure TLS settings")
        return value
